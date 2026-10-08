"""Safe, best-effort public page extraction. No facts are synthesized."""
from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

MAX_BYTES = 1_500_000
MAX_REDIRECTS = 3
MISSING = "Não identificado na página."


def normalize_space(value):
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def valid_public_url(url):
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Use uma URL pública começando com http:// ou https://.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("A URL informada não é válida.")
    if parsed.port not in (None, 80, 443):
        raise ValueError("Por segurança, a análise aceita somente portas públicas 80 e 443.")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Endereços locais não podem ser analisados.")
    try:
        addresses = {info[4][0].split("%")[0] for info in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError("Não foi possível encontrar o endereço público desta página.") from exc
    if not addresses:
        raise ValueError("Não foi possível validar o endereço da página.")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("O domínio da página não pôde ser validado com segurança.") from exc
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise ValueError("Por segurança, endereços privados ou reservados não podem ser analisados.")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, ""))


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS
    max_repeats = 1

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        valid_public_url(urllib.parse.urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PageParser(HTMLParser):
    TEXT_TAGS = {"title", "h1", "h2", "h3", "p", "li", "blockquote", "button", "a", "summary", "dt", "dd"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.parts = []
        self.h1, self.h2, self.h3 = [], [], []
        self.paragraphs, self.list_items, self.quotes, self.summaries = [], [], [], []
        self.buttons, self.links = [], []
        self.jsonld = []
        self.active = []
        self.suppressed = 0
        self.current_link = None

    def handle_starttag(self, tag, attrs):
        attrs = {str(k).lower(): v for k, v in attrs if k}
        if tag in ("script", "style", "noscript", "svg"):
            self.suppressed += 1
            if tag == "script" and "ld+json" in str(attrs.get("type", "")).lower():
                self.active.append({"tag": "jsonld", "text": []})
            return
        if tag == "meta":
            key = str(attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").strip().lower()
            value = normalize_space(attrs.get("content", ""))
            if key and value:
                self.meta[key] = value
        if tag == "a":
            self.current_link = {"href": attrs.get("href", ""), "text": []}
        if tag in self.TEXT_TAGS:
            self.active.append({"tag": tag, "text": []})

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            if tag == "script":
                for index in range(len(self.active) - 1, -1, -1):
                    if self.active[index]["tag"] == "jsonld":
                        capture = self.active.pop(index)
                        raw = "".join(capture["text"]).strip()
                        if raw:
                            self.jsonld.append(raw)
                        break
            self.suppressed = max(0, self.suppressed - 1)
            return
        for index in range(len(self.active) - 1, -1, -1):
            if self.active[index]["tag"] == tag:
                capture = self.active.pop(index)
                text = normalize_space("".join(capture["text"]))
                if text:
                    getattr(self, {"h1": "h1", "h2": "h2", "h3": "h3", "p": "paragraphs", "li": "list_items", "blockquote": "quotes", "button": "buttons", "summary": "summaries"}.get(tag, "parts"), self.parts).append(text)
                if tag == "a" and self.current_link is not None:
                    self.current_link["text"] = text
                    self.links.append(self.current_link)
                    self.current_link = None
                break

    def handle_data(self, data):
        if self.suppressed:
            # JSON-LD capture is the only script content retained.
            for capture in self.active:
                if capture["tag"] == "jsonld":
                    capture["text"].append(data)
            return
        if data.strip():
            self.parts.append(normalize_space(data))
            for capture in self.active:
                capture["text"].append(data)
            if self.current_link is not None:
                self.current_link["text"].append(data)


def safe_fetch(url):
    safe_url = valid_public_url(url)
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(SafeRedirectHandler(), urllib.request.HTTPSHandler(context=context))
    request = urllib.request.Request(safe_url, headers={
        "User-Agent": "CAKTO-ADS-AI-PageAnalyzer/1.0 (+public-page-metadata)",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        "Accept-Encoding": "identity",
    })
    try:
        with opener.open(request, timeout=9) as response:
            final_url = valid_public_url(response.geturl())
            content_type = response.headers.get_content_type().lower()
            if content_type not in ("text/html", "application/xhtml+xml"):
                raise ValueError("O endereço respondeu com um tipo de conteúdo que não é uma página HTML pública.")
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > MAX_BYTES:
                raise ValueError("A página é grande demais para análise automática.")
            payload = response.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES:
                raise ValueError("A página é grande demais para análise automática.")
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except LookupError:
                text = payload.decode("utf-8", errors="replace")
            return final_url, text
    except urllib.error.HTTPError as exc:
        raise ValueError(f"A página respondeu com HTTP {exc.code}; tente outra URL ou preencha manualmente.") from exc
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", "erro de conexão"))
        raise ValueError(f"Não foi possível acessar a página: {reason}. Você pode preencher manualmente.") from exc
    except TimeoutError as exc:
        raise ValueError("A página demorou demais para responder. Você pode preencher manualmente.") from exc


def _structured_values(raw_blocks):
    products, offers, faqs, reviews = [], [], [], []

    def walk(node):
        if isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            kind = node.get("@type", "")
            kinds = kind if isinstance(kind, list) else [kind]
            if any(str(k).lower() == "product" for k in kinds):
                products.append(node)
            if any(str(k).lower() == "offer" for k in kinds):
                offers.append(node)
            if any(str(k).lower() == "faqpage" for k in kinds):
                for entity in node.get("mainEntity", []) if isinstance(node.get("mainEntity"), list) else [node.get("mainEntity")]:
                    if isinstance(entity, dict):
                        faqs.append(entity)
            if any(str(k).lower() == "review" for k in kinds):
                reviews.append(node)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)

    for raw in raw_blocks:
        try:
            walk(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    return products, offers, faqs, reviews


def _flatten_text(value):
    if isinstance(value, list):
        return "; ".join(filter(None, (_flatten_text(v) for v in value)))
    if isinstance(value, dict):
        return normalize_space(value.get("name") or value.get("text") or value.get("@value") or "")
    return normalize_space(value)


def _as_money(value):
    if value in (None, ""):
        return None
    try:
        text = str(value).strip().replace(" ", "")
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            text = text.replace(".", "").replace(",", ".")
        return round(float(re.sub(r"[^0-9.]", "", text)), 2)
    except (ValueError, TypeError):
        return None


def _first_unique(values, limit=10):
    result, seen = [], set()
    for value in values:
        text = normalize_space(value)
        if len(text) < 12 or text.lower() in seen:
            continue
        seen.add(text.lower())
        result.append(text[:600])
        if len(result) >= limit:
            break
    return result


def analyze_url(url):
    final_url, source = safe_fetch(url)
    parser = PageParser()
    parser.feed(source)
    parser.close()
    meta = parser.meta
    products, offers, structured_faq, structured_reviews = _structured_values(parser.jsonld)
    product = products[0] if products else {}

    title = meta.get("og:title") or meta.get("twitter:title") or (parser.h1[0] if parser.h1 else "") or meta.get("title") or ""
    name = _flatten_text(product.get("name")) or normalize_space(re.split(r"\s[|–—-]\s", title)[0])
    headline = parser.h1[0] if parser.h1 else meta.get("og:title", "")
    first_paragraph = next((p for p in parser.paragraphs if len(p) > 30), "")
    subheadline = first_paragraph if first_paragraph and first_paragraph != headline else (parser.h2[0] if parser.h2 else "")
    description = meta.get("description") or meta.get("og:description") or _flatten_text(product.get("description")) or first_paragraph

    page_text = " ".join(parser.parts)
    money_matches = re.findall(r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})?|[0-9]+(?:,[0-9]{2})?)", page_text, re.I)
    explicit_price = meta.get("product:price:amount") or meta.get("og:price:amount") or meta.get("price")
    for offer in offers:
        specification = offer.get("priceSpecification", {})
        specification_price = specification.get("price") if isinstance(specification, dict) else None
        explicit_price = explicit_price or offer.get("price") or specification_price
    product_offers = product.get("offers")
    if isinstance(product_offers, dict):
        explicit_price = explicit_price or product_offers.get("price")
    elif isinstance(product_offers, list) and product_offers and isinstance(product_offers[0], dict):
        explicit_price = explicit_price or product_offers[0].get("price")
    price = _as_money(explicit_price)
    promo_price = None
    explicit_pair = re.search(r"de\s+R\$\s*([\d.,]+)\s+(?:por|para)\s+R\$\s*([\d.,]+)", page_text, re.I)
    if explicit_pair:
        price, promo_price = _as_money(explicit_pair.group(1)), _as_money(explicit_pair.group(2))
    elif price is None and money_matches:
        price = _as_money(money_matches[0])
    elif price is not None:
        promo_matches = re.findall(r"(?:por|agora|promo[cç][aã]o)\s*:?\s*R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})?|[0-9]+(?:,[0-9]{2})?)", page_text, re.I)
        if promo_matches:
            promo_price = _as_money(promo_matches[-1])

    benefits = _first_unique([item for item in parser.list_items if len(item) <= 300], 12)
    if not benefits:
        benefits = _first_unique([p for p in parser.paragraphs if any(w in p.lower() for w in ("aprenda", "inclui", "você vai", "benefício", "acesso", "conteúdo"))], 8)
    bonuses = _first_unique([item for item in parser.list_items + parser.paragraphs if re.search(r"\b(b[oô]nus|b[oô]nus extra)\b", item, re.I)], 8)
    guarantee_parts = [item for item in parser.paragraphs + parser.list_items if re.search(r"\b(garantia|reembolso|devolu[cç][aã]o|dias para testar)\b", item, re.I)]
    faq = []
    for item in structured_faq:
        question = _flatten_text(item.get("name") or item.get("question"))
        answer = item.get("acceptedAnswer", {})
        answer_text = _flatten_text(answer.get("text") if isinstance(answer, dict) else answer)
        if question:
            faq.append(f"{question}: {answer_text}" if answer_text else question)
    if not faq:
        faq = list(parser.summaries)
    if not faq:
        for i, heading in enumerate(parser.h2 + parser.h3):
            if re.search(r"\b(perguntas|faq|d[uú]vidas|como funciona)\b", heading, re.I):
                faq.append(heading)
    audience = _first_unique([p for p in parser.paragraphs if re.search(r"\b(para quem|ideal para|indicado para|feito para|p[uú]blico)\b", p, re.I)], 6)
    ctas = _first_unique(parser.buttons + [link.get("text", "") for link in parser.links if re.search(r"compr|come[cç]|quero|acessar|garant|inscre|matric", link.get("text", ""), re.I)], 8)
    differentiators = _first_unique([item for item in parser.list_items + parser.paragraphs if re.search(r"\b(diferencial|m[eé]todo|exclusivo|passo a passo|acesso|material|suporte|m[oó]dulo)\b", item, re.I)], 8)
    testimonial_values = parser.quotes[:8]
    for review in structured_reviews:
        review_text = _flatten_text(review.get("reviewBody"))
        if review_text:
            author = review.get("author", {})
            name_author = _flatten_text(author)
            testimonial_values.append(f"{review_text} — {name_author}" if name_author else review_text)
    relevant_links = []
    for link in parser.links:
        href = urllib.parse.urljoin(final_url, str(link.get("href", "")))
        parsed = urllib.parse.urlsplit(href)
        if parsed.scheme in ("http", "https") and parsed.hostname:
            text = normalize_space(link.get("text", ""))
            if text or re.search(r"checkout|pagamento|comprar|inscri", href, re.I):
                relevant_links.append({"text": text or "Link relevante", "url": href[:1000]})
    link_unique, seen_links = [], set()
    for link in relevant_links:
        if link["url"] not in seen_links:
            seen_links.add(link["url"]); link_unique.append(link)
        if len(link_unique) >= 20:
            break
    checkout = next((l["url"] for l in link_unique if re.search(r"checkout|pagamento", l["url"], re.I)), "")

    fields = {
        "name": name or MISSING,
        "headline": headline or MISSING,
        "subheadline": subheadline or MISSING,
        "description": description or MISSING,
        "benefits": benefits or [MISSING],
        "price": price,
        "promo_price": promo_price,
        "bonuses": bonuses or [MISSING],
        "guarantee": "\n".join(guarantee_parts) if guarantee_parts else MISSING,
        "faq": faq or [MISSING],
        "target_audience": audience or [MISSING],
        "cta": ctas or [MISSING],
        "differentiators": differentiators or [MISSING],
        "testimonials": testimonial_values or [MISSING],
        "relevant_links": link_unique or [MISSING],
        "sales_url": final_url,
        "checkout_url": checkout,
        "niche": MISSING,
        "category": MISSING,
        "mechanism": MISSING,
    }
    # Product type/category may be explicitly stated in JSON-LD only.
    if product.get("category"):
        fields["category"] = _flatten_text(product.get("category"))
    found = sum(value is not None and value != MISSING and value != [MISSING] for value in fields.values())
    return {
        "source_url": final_url,
        "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "success" if found else "no_data",
        "status_message": "Conteúdo público analisado. Confira e edite cada campo antes de continuar." if found else "A página foi acessada, mas não foi possível identificar dados estruturados suficientes. Preencha manualmente.",
        "fields": fields,
        "extraction": {
            "title": title or MISSING,
            "headings": _first_unique(parser.h1 + parser.h2 + parser.h3, 30),
            "paragraphs": _first_unique(parser.paragraphs, 20),
            "structured_data_found": bool(products or offers or structured_faq or structured_reviews),
            "method": "Extração de HTML público, metadados e JSON-LD; sem geração de fatos.",
        },
        "disclaimer": "Extração automática pode falhar em páginas com JavaScript, paywall ou bloqueio. Revise a fonte. Campos ausentes aparecem como ‘Não identificado na página.’",
    }
