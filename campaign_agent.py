"""Deterministic local campaign-agent workflows.

This service deliberately does not call an LLM, Meta, Cakto, or a media model.
Briefings and scores are explainable heuristics; public-page facts and user
assumptions remain distinct, and no output is a performance prediction.
"""
from __future__ import annotations

import re
from statistics import mean

from auth import new_id
from services import policy
from services.creative_pack import _video_script

MISSING_PAGE = "Não identificado na página."
MISSING_USER = "Não informado pelo usuário."
OBJECTIVES = ("Vender", "Leads", "Mensagens", "Tráfego", "Reconhecimento", "Validar produto", "Outro")
PRODUCT_FIELDS = (
    "name", "description", "niche", "category", "sales_url", "checkout_url",
    "benefits", "differentiators", "target_audience", "guarantee", "bonuses", "notes",
    "headline", "subheadline", "faq", "testimonials", "cta", "mechanism", "relevant_links",
)


def _string(value, limit=10000):
    return str(value or "").strip()[:limit]


def _list_text(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(_string(item, 2000) for item in value if _string(item, 2000))
    if isinstance(value, dict):
        return "\n".join(f"{_string(k, 120)}: {_string(v, 1000)}" for k, v in value.items())
    return _string(value)


def _number(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        raw = re.sub(r"[^0-9,.\-]", "", str(value).strip())
        if not raw:
            return None
        if "," in raw and "." in raw:
            if raw.rfind(",") > raw.rfind("."):
                raw = raw.replace(".", "").replace(",", ".")
            else:
                raw = raw.replace(",", "")
        elif "," in raw:
            raw = raw.replace(",", ".")
        elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
            raw = raw.replace(".", "")
        number = float(raw)
    if number < 0 or number > 100_000_000:
        raise ValueError("Valor numérico fora do intervalo permitido.")
    return round(number, 2)


def _bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "sim", "on", "checked")


def _money_mentions(text):
    candidates = []
    for match in re.finditer(r"R\$\s*([0-9][0-9.,]*)", text or "", re.I):
        try:
            value = _number(match.group(1))
        except (ValueError, TypeError):
            continue
        if value is None:
            continue
        before = (text[max(0, match.start() - 48):match.start()] or "").lower()
        after = (text[match.end():min(len(text), match.end() + 34)] or "").lower()
        context = before + " " + after
        near_before = before[-36:]
        budget_signal = bool(re.search(r"(?:orçamento|invest(?:ir|imento)|gasto|diári[oa]s?).{0,32}$", near_before)) or bool(re.match(r"\s*(?:/|por\s+|ao\s+)dia\b", after))
        price_signal = bool(re.search(r"(?:preço|custa|ticket|valor|por\s+apenas).{0,32}$", near_before)) or bool(re.search(r"\bpor\s*$", near_before))
        price_signal = price_signal and not budget_signal
        candidates.append({"value": value, "budget": budget_signal, "price": price_signal, "context": context.strip()})
    budget = next((item["value"] for item in candidates if item["budget"]), None)
    price = next((item["value"] for item in candidates if item["price"]), None)
    if budget is None and len(candidates) == 1 and re.search(r"R\$\s*[0-9][0-9.,]*\s*(?:/\s*dia|ao\s+dia|diariamente)", text or "", re.I):
        budget = candidates[0]["value"]
    return price, budget


def _infer_objective(prompt):
    text = (prompt or "").lower()
    if any(term in text for term in ("whatsapp", "mensagem", "conversar", "direct")):
        return "Mensagens"
    if any(term in text for term in ("lead", "cadastro", "lista de e-mail", "inscrição")):
        return "Leads"
    if any(term in text for term in ("reconhecimento", "alcance", "conhecer a marca")):
        return "Reconhecimento"
    if any(term in text for term in ("tráfego", "trafego", "visitas", "acessos")):
        return "Tráfego"
    if any(term in text for term in ("validar", "validação", "validacao", "testar o produto")):
        return "Validar produto"
    if any(term in text for term in ("vender", "vendas", "compra", "compras", "checkout")):
        return "Vender"
    return ""


def _infer_market(prompt):
    text = (prompt or "").lower()
    if re.search(r"\bbrasil\b|\bbrasileir[oa]s?\b", text):
        return "Brasil"
    if re.search(r"\bportugal\b", text):
        return "Portugal"
    if re.search(r"\b(estados unidos|eua|usa)\b", text):
        return "Estados Unidos"
    return ""


def normalize_product(body, existing_product=None):
    """Return a product-shaped object and the page evidence supplied to the user."""
    source = _string(body.get("source") or body.get("product_source") or "manual", 24).lower()
    if source not in ("existing", "url", "manual"):
        source = "manual"
    prompt = _string(body.get("goal_prompt"), 4000)
    raw_fields = body.get("product") if isinstance(body.get("product"), dict) else {}
    raw_fields = dict(raw_fields)
    page_analysis = body.get("page_analysis") if isinstance(body.get("page_analysis"), dict) else {}
    page_fields = page_analysis.get("fields") if isinstance(page_analysis.get("fields"), dict) else {}

    if source == "existing" and existing_product:
        product = dict(existing_product)
    else:
        product = {}
        if source == "url":
            for field in PRODUCT_FIELDS:
                value = page_fields.get(field)
                if value not in (None, "", [], {}):
                    product[field] = _list_text(value)
        for field in PRODUCT_FIELDS:
            if field in raw_fields:
                submitted = _list_text(raw_fields.get(field))
                if submitted.strip() or source == "url":
                    product[field] = submitted
                else:
                    product.setdefault(field, "")
            else:
                product.setdefault(field, "")
        for field in ("price", "promo_price", "unit_cost"): 
            raw = raw_fields.get(field)
            if raw in (None, "") and source == "url":
                raw = page_fields.get(field)
            product[field] = _number(raw) if raw not in (None, "") else None

    for field in PRODUCT_FIELDS:
        product[field] = _string(product.get(field), 10000)
    for field in ("price", "promo_price", "unit_cost"):
        if product.get(field) not in (None, ""):
            product[field] = _number(product[field])
        else:
            product[field] = None

    quick = body.get("quick") if isinstance(body.get("quick"), dict) else {}
    quick = dict(quick)
    price_from_prompt, budget_from_prompt = _money_mentions(prompt)
    explicit_price = _number(quick.get("price")) if quick.get("price") not in (None, "") else price_from_prompt
    user_corrected_fields = []
    if source == "url":
        for field in PRODUCT_FIELDS:
            if field in raw_fields:
                submitted_value, page_value = _list_text(raw_fields.get(field)), _list_text(page_fields.get(field))
                if submitted_value != page_value and (submitted_value or page_value):
                    user_corrected_fields.append(field)
        public_price = _number(page_fields.get("promo_price")) if page_fields.get("promo_price") not in (None, "") else _number(page_fields.get("price"))
        if explicit_price is not None and (public_price is None or explicit_price != public_price):
            user_corrected_fields.append("preço considerado")
    if source != "existing" and product.get("price") is None and explicit_price is not None:
        product["price"] = explicit_price
    product_price = product.get("promo_price") if product.get("promo_price") not in (None, "") else product.get("price")
    considered_price = explicit_price if explicit_price is not None else product_price
    if source == "url":
        page_price = _number(page_fields.get("promo_price")) if page_fields.get("promo_price") not in (None, "") else _number(page_fields.get("price"))
        if considered_price is None:
            price_origin = MISSING_PAGE
        elif page_price is not None and considered_price == page_price:
            price_origin = "Preço identificado na página; confirme no checkout"
        else:
            price_origin = "Preço informado/corrigido pelo usuário"
    elif source == "existing" and existing_product:
        catalog_price = _number(existing_product.get("promo_price")) if existing_product.get("promo_price") not in (None, "") else _number(existing_product.get("price"))
        if considered_price is None:
            price_origin = MISSING_USER
        elif catalog_price is not None and considered_price == catalog_price:
            price_origin = "Preço do produto cadastrado; confirme no checkout"
        else:
            price_origin = "Preço informado/corrigido nesta estratégia"
    elif considered_price is None:
        price_origin = MISSING_USER
    else:
        price_origin = "Preço informado pelo usuário"
    daily_budget = _number(quick.get("daily_budget")) if quick.get("daily_budget") not in (None, "") else budget_from_prompt

    objective = _string(quick.get("objective"), 80)
    if objective not in OBJECTIVES:
        objective = _infer_objective(prompt) or "Vender"
        objective_source = "Inferido do texto" if _infer_objective(prompt) else "Padrão provisório; revise"
    else:
        objective_source = "Informado no modo rápido"
    market = _string(quick.get("market"), 120) or _infer_market(prompt)
    market_source = "Informado no modo rápido" if _string(quick.get("market"), 120) else ("Identificado no texto" if market else "Não informado")

    reported_data = []
    data_choices = quick.get("data_available") or quick.get("data") or []
    if isinstance(data_choices, str):
        data_choices = [part.strip() for part in data_choices.split(",") if part.strip()]
    if isinstance(data_choices, dict):
        data_choices = [key for key, value in data_choices.items() if _bool(value)]
    allowed_data = {"pixel_event": "Pixel/evento", "sales_history": "Histórico de vendas", "customer_list": "Lista de clientes", "custom_audience": "Público personalizado existente"}
    reported_data = [allowed_data.get(str(value), str(value)[:80]) for value in data_choices if str(value) in allowed_data or str(value) in allowed_data.values()]

    has_creatives = _bool(quick.get("has_creatives"))
    has_sales_page = _bool(quick.get("has_sales_page")) or bool(product.get("sales_url"))
    has_checkout = _bool(quick.get("has_checkout")) or bool(product.get("checkout_url"))
    tracking_reported = "Pixel/evento" in reported_data
    if source == "url":
        page_fields_used = [field for field in PRODUCT_FIELDS if page_fields.get(field) not in (None, "", [], {})]
        page_evidence = {
            "source_url": _string(page_analysis.get("source_url") or body.get("url"), 2000),
            "status": _string(page_analysis.get("status") or "Não analisado", 80),
            "fields_found": page_fields_used,
            "user_corrected_fields": sorted(set(user_corrected_fields)),
            "disclaimer": "A análise pública é a evidência de origem. Qualquer campo corrigido pelo usuário é um dado informado, não uma extração da página. Campos ausentes permanecem não identificados até serem preenchidos.",
        }
    else:
        page_evidence = {"source_url": "", "status": "Não aplicável", "fields_found": [], "disclaimer": "Dados do produto informados pelo usuário; não foi feita pesquisa pública."}

    unit_cost = product.get("unit_cost")
    fees = _number(quick.get("fees_per_sale")) if quick.get("fees_per_sale") not in (None, "") else None
    if unit_cost is None and quick.get("unit_cost") not in (None, ""):
        unit_cost = _number(quick.get("unit_cost"))
    if unit_cost is not None:
        product["unit_cost"] = unit_cost

    brief = {
        "goal_prompt": prompt,
        "product": {
            "name": product.get("name") or (MISSING_PAGE if source == "url" else "Produto a confirmar"),
            "description": product.get("description") or (MISSING_PAGE if source == "url" else MISSING_USER),
            "niche": product.get("niche") or (MISSING_PAGE if source == "url" else MISSING_USER),
            "category": product.get("category") or (MISSING_PAGE if source == "url" else MISSING_USER),
            "price": considered_price,
            "price_origin": price_origin,
            "benefits": product.get("benefits") or (MISSING_PAGE if source == "url" else MISSING_USER),
            "differentiators": product.get("differentiators") or (MISSING_PAGE if source == "url" else MISSING_USER),
            "target_audience": product.get("target_audience") or (MISSING_PAGE if source == "url" else MISSING_USER),
            "headline": product.get("headline") or (MISSING_PAGE if source == "url" else MISSING_USER),
            "mechanism": product.get("mechanism") or (MISSING_PAGE if source == "url" else MISSING_USER),
            "sales_url": product.get("sales_url") or _string(page_analysis.get("source_url") or body.get("url"), 2000),
            "checkout_url": product.get("checkout_url") or "",
            "unit_cost": unit_cost,
            "fees_per_sale": fees,
            "guarantee": product.get("guarantee") or (MISSING_PAGE if source == "url" else MISSING_USER),
            "bonuses": product.get("bonuses") or (MISSING_PAGE if source == "url" else MISSING_USER),
            "faq": product.get("faq") or (MISSING_PAGE if source == "url" else MISSING_USER),
            "testimonials": product.get("testimonials") or (MISSING_PAGE if source == "url" else MISSING_USER),
        },
        "source": source,
        "page_evidence": page_evidence,
        "objective": objective,
        "objective_origin": objective_source,
        "market": market or "Não informado",
        "market_origin": market_source,
        "location": {
            "country": _string(quick.get("country"), 120),
            "region": _string(quick.get("region"), 120),
            "city": _string(quick.get("city"), 120),
            "market_type": _string(quick.get("market_type"), 80) or "Não informado",
        },
        "daily_budget": daily_budget,
        "data_available_reported": reported_data,
        "tracking_status": "Informado pelo usuário; não verificado" if tracking_reported else "Não informado/não verificado",
        "tracking_verified": False,
        "assets": {"creatives_available_reported": has_creatives, "sales_page_available": has_sales_page, "checkout_available": has_checkout},
        "time_horizon_days": int(_number(quick.get("time_horizon_days")) or 0) or None,
        "notes": _string(quick.get("notes"), 1000),
        "unknowns": [],
        "assumptions": [],
        "heuristic_notice": "Briefing interpretado por regras locais. Inferências são hipóteses da IA; nenhuma pesquisa de concorrentes ou conexão externa foi realizada.",
    }
    if not brief["product"]["name"] or brief["product"]["name"] in (MISSING_PAGE, "Produto a confirmar"):
        brief["unknowns"].append("Informe ou confirme o nome do produto antes de criar estratégias.")
    if considered_price is None:
        brief["unknowns"].append("Preço não informado; cálculos econômicos não serão apresentados.")
    if daily_budget is None:
        brief["unknowns"].append("Orçamento diário não informado; distribuição numérica não será calculada.")
    if market == "":
        brief["unknowns"].append("Mercado/localização não informado; segmentação geográfica permanece indefinida.")
    if not tracking_reported:
        brief["unknowns"].append("Rastreamento Pixel/evento não foi confirmado; não otimizar para vendas até verificar manualmente.")
    if not has_sales_page:
        brief["unknowns"].append("Página de vendas não informada/disponível.")
    if not has_checkout:
        brief["unknowns"].append("Checkout não informado/disponível.")
    if not reported_data:
        brief["unknowns"].append("Não há dados de audiência ou vendas informados.")
    return product, brief


def _fact(text, source="Informado pelo usuário"):
    return {"classification": source, "text": text}


def diagnose(brief, product):
    p = brief.get("product", {})
    assets = brief.get("assets", {})
    strengths, weaknesses, opportunities, risks, bottlenecks = [], [], [], [], []
    if p.get("name") and p.get("name") not in (MISSING_PAGE, "Produto a confirmar"):
        source = "Extraído de página pública; confirme" if brief.get("source") == "url" else "Informado/cadastrado pelo usuário"
        strengths.append(_fact(f"Produto identificado: {p['name']}.", source))
    if p.get("description") not in (MISSING_PAGE, MISSING_USER, ""):
        strengths.append(_fact("Há descrição do produto para orientar a mensagem.", "Texto da página" if brief.get("source") == "url" else "Informado pelo usuário"))
    if p.get("price") is not None:
        strengths.append(_fact(f"Preço considerado na estratégia: R$ {p['price']:.2f}; confirmar consistência entre anúncio e checkout.", "Informado/cadastrado; não validado contra checkout"))
    if p.get("benefits") not in (MISSING_PAGE, MISSING_USER, ""):
        strengths.append(_fact("Existem benefícios cadastrados/extraídos que podem ser usados somente após revisão.", "Conteúdo fornecido; evidência comercial não verificada"))
    if assets.get("creatives_available_reported"):
        strengths.append(_fact("O usuário informou que já dispõe de criativos; qualidade, direitos e conteúdo não foram inspecionados."))
    if assets.get("sales_page_available"):
        strengths.append(_fact("O usuário indicou uma página de vendas/URL disponível; carregamento e checkout não foram verificados."))
    if assets.get("checkout_available"):
        strengths.append(_fact("O usuário indicou checkout disponível; condições, pagamento e rastreamento não foram testados."))
    if not strengths:
        strengths.append(_fact("Nenhuma força comercial verificável além da identificação do briefing foi confirmada.", "Limitação dos dados disponíveis"))

    if p.get("price") is None:
        weaknesses.append(_fact("Preço não informado; preço, margem e CPA de equilíbrio não podem ser avaliados.", "Lacuna de dados"))
        bottlenecks.append(_fact("Preço e condições: confirmar preço final, descontos e consistência com checkout."))
    if p.get("target_audience") in (MISSING_PAGE, MISSING_USER, ""):
        weaknesses.append(_fact("Público-alvo não identificado; avatar permanecerá uma hipótese ampla.", "Lacuna de dados"))
    if not assets.get("creatives_available_reported"):
        weaknesses.append(_fact("Nenhum criativo existente foi informado; os conceitos gerados serão briefings, não mídia final.", "Lacuna de dados"))
        bottlenecks.append(_fact("Material criativo: validar direitos e produzir as peças; modelo de imagem/vídeo não está conectado."))
    if not assets.get("sales_page_available"):
        weaknesses.append(_fact("Página de vendas não foi informada/disponível.", "Lacuna de dados"))
        bottlenecks.append(_fact("Página: disponibilizar e revisar antes de direcionar tráfego."))
    if not assets.get("checkout_available"):
        weaknesses.append(_fact("Checkout não foi informado/disponível.", "Lacuna de dados"))
        bottlenecks.append(_fact("Checkout: confirmar URL e fluxo de compra, sem presumir integração."))
    if not brief.get("data_available_reported"):
        weaknesses.append(_fact("Sem histórico, lista ou audiência informados para análise.", "Lacuna de dados"))
    if brief.get("tracking_verified") is False:
        weaknesses.append(_fact("Pixel/evento não verificado. O usuário não deve otimizar para vendas até confirmar manualmente.", "Rastreamento não verificado"))
        bottlenecks.append(_fact("Rastreamento: verificar Pixel/Dataset, evento e fluxo de teste na conta Meta; este app não consultou a conta."))
    if not weaknesses:
        weaknesses.append(_fact("Nenhuma lacuna adicional identificada no briefing; validação humana continua necessária.", "Revisão local"))

    if p.get("benefits") not in (MISSING_PAGE, MISSING_USER, ""):
        opportunities.append(_fact("Testar a apresentação de um benefício realmente fornecido, sem extrapolar o que está descrito.", "Hipótese da IA"))
    opportunities.extend([
        _fact("Comparar as três lógicas criativas com uma variável por teste, sem escolher vencedores por score.", "Plano de experimentação; hipótese da IA"),
        _fact("Não há pesquisa real de concorrentes. Ângulos alternativos são hipóteses conceituais, não evidência de mercado.", "Pesquisa não realizada"),
    ])
    prompt_and_claims = " ".join([brief.get("goal_prompt", ""), _string(product.get("description")), _string(product.get("headline")), _string(product.get("benefits"))]).lower()
    if re.search(r"\b(ganhe|fature|renda extra|renda garantida|emagreça|cura|resultado garantido|lucro garantido)\b", prompt_and_claims):
        risks.append(_fact("O material menciona alegação de resultado/financeira ou saúde. Exija comprovação e revise políticas antes de usar; a geração evita transformar isso em promessa.", "Risco de linguagem; avaliação heurística"))
    if not p.get("guarantee") or p.get("guarantee") in (MISSING_PAGE, MISSING_USER):
        risks.append(_fact("Garantia não confirmada. Não anunciar garantia até conferir os termos reais.", "Ausência de evidência"))
    if not p.get("testimonials") or p.get("testimonials") in (MISSING_PAGE, MISSING_USER):
        risks.append(_fact("Depoimentos/provas não confirmados. Não inventar prova social.", "Ausência de evidência"))
    risks.append(_fact("Não há análise real de concorrência, tendências ou regras atuais de interface Meta nesta execução.", "Limite da pesquisa"))

    return {
        "strengths": strengths,
        "weaknesses": weaknesses,
        "opportunities": opportunities,
        "risks": risks,
        "bottlenecks": bottlenecks or [_fact("Nenhum gargalo isolado por evidência; coletar baseline antes de otimizar.", "Sem dados comparáveis")],
        "market_research": {"status": "Não realizada", "note": "Nenhuma pesquisa de concorrentes, audiência ou mercado foi executada."},
        "awareness_stage": {"value": "Não determinado", "classification": "Não há pesquisa de audiência; qualquer estágio é hipótese."},
        "evidence_note": "Fatos informados/extraídos aparecem identificados. Inferências são hipóteses da IA. Ausência de evidência não foi preenchida com fatos inventados.",
    }


def _contains_data(brief, label):
    return label in (brief.get("data_available_reported") or [])


def _clamp(value):
    return max(0, min(100, int(round(value))))


def _score_components(brief, variant, complexity):
    p = brief.get("product", {})
    assets = brief.get("assets", {})
    has_name = bool(p.get("name") and p.get("name") not in (MISSING_PAGE, "Produto a confirmar"))
    has_description = p.get("description") not in ("", MISSING_PAGE, MISSING_USER)
    has_benefits = p.get("benefits") not in ("", MISSING_PAGE, MISSING_USER)
    has_audience = p.get("target_audience") not in ("", MISSING_PAGE, MISSING_USER)
    product_fit = 43 + 12 * has_name + 13 * has_description + 10 * has_benefits + 8 * bool(p.get("niche") not in ("", MISSING_PAGE, MISSING_USER))
    avatar_fit = 50 + 24 * has_audience + 10 * bool(p.get("niche") not in ("", MISSING_PAGE, MISSING_USER)) + 5 * bool(brief.get("market") not in ("", "Não informado"))
    offer_strength = 43 + 13 * (p.get("price") is not None) + 15 * has_benefits + 10 * bool(p.get("guarantee") not in ("", MISSING_PAGE, MISSING_USER)) + 8 * bool(p.get("bonuses") not in ("", MISSING_PAGE, MISSING_USER))
    clarity = 48 + 15 * has_name + 12 * has_description + 9 * (p.get("price") is not None) + 7 * bool(brief.get("goal_prompt"))
    creative = 50 + 15 * bool(assets.get("creatives_available_reported")) + 12 * has_benefits + 8 * has_description + (8 if variant == "B" else 3)
    ctr_potential = {"A": 62, "B": 68, "C": 65}[variant] + 6 * bool(assets.get("creatives_available_reported")) + 5 * bool(brief.get("goal_prompt"))
    conversion_potential = {"A": 67, "B": 56, "C": 60}[variant] + 8 * bool(assets.get("sales_page_available")) + 8 * bool(assets.get("checkout_available")) + 5 * (p.get("price") is not None)
    budget_fit = 61 if brief.get("daily_budget") is not None else 42
    execution = {"baixa": 86, "média": 73, "alta": 65}[complexity]
    policy_safety = 82
    scan = " ".join([brief.get("goal_prompt", ""), str(p.get("headline", "")), str(p.get("description", ""))]).lower()
    if re.search(r"\b(ganhe|fature|renda extra|cura|emagreça|garantido)\b", scan):
        policy_safety -= 18
    components = {
        "product_fit": _clamp(product_fit), "avatar_fit": _clamp(avatar_fit),
        "offer_strength": _clamp(offer_strength), "message_clarity": _clamp(clarity),
        "creative_potential": _clamp(creative), "ctr_potential_heuristic": _clamp(ctr_potential),
        "conversion_potential_heuristic": _clamp(conversion_potential), "budget_fit": _clamp(budget_fit),
        "execution_ease": _clamp(execution), "policy_safety_heuristic": _clamp(policy_safety),
    }
    weights = {
        "product_fit": .14, "avatar_fit": .10, "offer_strength": .12, "message_clarity": .12,
        "creative_potential": .10, "ctr_potential_heuristic": .07, "conversion_potential_heuristic": .09,
        "budget_fit": .08, "execution_ease": .10, "policy_safety_heuristic": .08,
    }
    score = _clamp(sum(components[key] * weight for key, weight in weights.items()))
    components["weights"] = weights
    return score, components


def _avatar_hypothesis(brief):
    audience = brief.get("product", {}).get("target_audience")
    if audience and audience not in (MISSING_PAGE, MISSING_USER):
        return f"Público informado: {audience}. Validar com dados reais; sem inferir idade, gênero ou atributos sensíveis."
    niche = brief.get("product", {}).get("niche") or "o tema informado"
    return f"Hipótese ampla: pessoas que já demonstram interesse em {niche}. Sem pesquisa, segmentação demográfica ou garantia de intenção."


def _creative_angle_c(brief):
    p = brief.get("product", {})
    if p.get("faq") not in ("", MISSING_PAGE, MISSING_USER):
        return "Objeção esclarecida", "Selecionar uma dúvida real do FAQ e respondê-la com os termos cadastrados.", "Antes de decidir, confira a informação sobre [pergunta real do FAQ]."
    if p.get("differentiators") not in ("", MISSING_PAGE, MISSING_USER):
        return "Diferencial verificável", "Apresentar somente um diferencial que esteja descrito pelo responsável; comparação externa não foi pesquisada.", "O que o cadastro informa sobre [diferencial real]?"
    if p.get("mechanism") not in ("", MISSING_PAGE, MISSING_USER):
        return "Método/estrutura", "Mostrar a sequência ou formato descrito, sem atribuir resultado ao método.", "Como está organizada a proposta de [produto]?"
    if p.get("benefits") not in ("", MISSING_PAGE, MISSING_USER):
        return "Benefício específico informado", "Focar em um benefício cadastrado e explicar seu escopo, sem ampliar a promessa.", "Confira o benefício descrito para [produto]."
    return "Critério de escolha", "Diferenciar por transparência de escopo, formato e condições — sem alegar superioridade competitiva.", "O que vale conferir antes de escolher [produto]?"


def generate_strategies(brief):
    """Always return exactly three distinct strategy architectures."""
    product = brief.get("product", {})
    name = product.get("name") or "produto"
    niche = product.get("niche") if product.get("niche") not in ("", MISSING_PAGE, MISSING_USER) else "o tema do produto"
    avatar = _avatar_hypothesis(brief)
    benefit = product.get("benefits") if product.get("benefits") not in ("", MISSING_PAGE, MISSING_USER) else "benefício não identificado; usar descrição factual do escopo"
    price = f"Preço considerado: R$ {product['price']:.2f}, confirmar na página e no checkout." if product.get("price") is not None else "Preço não informado; não criar âncora ou desconto."
    market = brief.get("market") or "Não informado"
    c_name, c_description, c_hook = _creative_angle_c(brief)
    budget = brief.get("daily_budget")
    budget_note = f"Planejar até R$ {budget:.2f}/dia (valor informado; validar capacidade e limites na conta)." if budget is not None else "Orçamento não informado; definir antes de iniciar."
    base = [
        {
            "id": "A", "code": "A", "name": "Performance Direta",
            "logic": "Problema → solução → oferta → CTA",
            "avatar": avatar,
            "angle": {"name": "Problema → solução", "description": "Partir de uma situação relevante sem atribuir condição pessoal ao espectador e conectar ao escopo confirmado do produto."},
            "hook": f"Uma forma de explorar {niche}: conheça o escopo de {name}.",
            "offer": f"Apresentar o conteúdo/benefício informado: {benefit}. {price} Sem bônus, garantia ou prova não confirmados.",
            "creative": "Peça clara com problema contextual, explicação factual do produto e CTA informativo; usar material real ou placeholder identificado.",
            "audience": f"Hipótese de público com intenção existente em {niche}; mercado: {market}. Não usar Lookalike sem volume e elegibilidade verificados.",
            "campaign": {"commercial_goal": brief.get("objective"), "structure": "Uma campanha de planejamento; público e evento devem ser revisados manualmente.", "optimization": "Vendas apenas depois de verificar Pixel/Dataset, evento e checkout; rastreamento está como não verificado."},
            "budget_hint": budget_note,
            "complexity": "Baixa", "risk": "Médio" if brief.get("tracking_verified") is False else "Baixo",
            "risk_reason": "A conversão depende de preço, página e evento validados; não há verificação de conta nesta implantação.",
        },
        {
            "id": "B", "code": "B", "name": "Curiosidade + Educação",
            "logic": "Pergunta → explicação útil → contexto do produto → CTA",
            "avatar": avatar,
            "angle": {"name": "Educação antes da oferta", "description": f"Abrir uma pergunta de avaliação sobre {niche}, responder sem suspense falso e apresentar o conteúdo disponível."},
            "hook": f"O que vale conferir antes de escolher um material sobre {niche}?",
            "offer": f"Convide a revisar o formato e o escopo real de {name}. {price} Não alegar resultado ou urgência.",
            "creative": "Carrossel ou vídeo explicativo por etapas; cada quadro acrescenta informação verificável e termina com convite para consultar detalhes.",
            "audience": f"Hipótese de descoberta para pessoas interessadas em {niche}; sem pesquisa de audiência e sem atributos sensíveis. Mercado: {market}.",
            "campaign": {"commercial_goal": brief.get("objective"), "structure": "Uma peça educativa como ponto de entrada; validar objetivo/evento com a configuração real da conta.", "optimization": "Se o objetivo for venda, só otimizar para o evento após verificação manual do rastreamento."},
            "budget_hint": budget_note,
            "complexity": "Média", "risk": "Baixo",
            "risk_reason": "Mensagem informativa reduz dependência de alegações, mas todo claim e material visual requer revisão humana.",
        },
        {
            "id": "C", "code": "C", "name": "Ângulo Diferenciado",
            "logic": f"{c_name} → explicação específica → produto → CTA",
            "avatar": avatar,
            "angle": {"name": c_name, "description": c_description},
            "hook": c_hook.replace("[produto]", name),
            "offer": f"Mostrar o aspecto verificável de {name}; preço e condições: {price} Não afirmar superioridade sobre concorrentes.",
            "creative": "Conceito editorial específico baseado em FAQ, diferencial, mecanismo ou benefício realmente informado; se nenhum estiver disponível, usar checklist de critérios.",
            "audience": f"Hipótese contextual alinhada a {niche}; sem pesquisa de concorrência ou público. Mercado: {market}.",
            "campaign": {"commercial_goal": brief.get("objective"), "structure": "Um ângulo editorial único; validar as opções atuais na conta Meta antes da execução.", "optimization": "Não presumir Pixel, CAPI ou evento. Confirmar rastreamento antes de otimizar para compra."},
            "budget_hint": budget_note,
            "complexity": "Alta", "risk": "Médio",
            "risk_reason": "Depende da disponibilidade e fidelidade do material factual usado para sustentar o ângulo.",
        },
    ]
    for item in base:
        score, components = _score_components(brief, item["code"], item["complexity"].lower())
        item["score"] = score
        item["score_components"] = components
        item["ctr_potential_heuristic"] = components["ctr_potential_heuristic"]
        item["conversion_potential_heuristic"] = components["conversion_potential_heuristic"]
        item["score_type"] = "heurística editorial 0–100; não é previsão estatística nem resultado observado."
        item["score_explanation"] = "Score editorial heurístico 0–100, combinação ponderada de aderência, clareza, potencial criativo, execução e segurança. CTR/conversão são apenas dimensões heurísticas; não são previsões nem resultados observados."
        item["assumptions"] = ["Avatar e ângulo são hipóteses até validar com dados reais.", "A pesquisa de concorrentes não foi realizada.", "Nenhuma opção/interface atual do Meta foi consultada."]
        item["different_from_others"] = {
            "A": "Oferta direta para contexto de intenção; problema → solução → oferta → CTA.",
            "B": "Entrega de contexto educativo antes de apresentar o produto.",
            "C": "Foco em uma evidência específica do produto ou critério de escolha, sem comparação não comprovada.",
        }[item["code"]]

    # The recommendation considers readiness, stated objective and evidence, not just the score.
    if brief.get("objective") in ("Reconhecimento", "Tráfego"):
        recommended = "B"
        why = "O objetivo informado favorece descoberta/educação antes de pressionar uma conversão. Isso é uma escolha de planejamento, não previsão de desempenho."
    elif brief.get("objective") in ("Vender", "Leads") and product.get("price") is not None and brief.get("assets", {}).get("sales_page_available") and brief.get("assets", {}).get("checkout_available"):
        recommended = "A"
        why = "Produto, preço, página e checkout foram informados, então a lógica direta pode ser revisada em contexto de intenção. Ainda assim, o rastreamento não está verificado; não otimizar para vendas antes dessa checagem."
    elif product.get("faq") not in ("", MISSING_PAGE, MISSING_USER) or product.get("differentiators") not in ("", MISSING_PAGE, MISSING_USER) or product.get("mechanism") not in ("", MISSING_PAGE, MISSING_USER):
        recommended = "C"
        why = "Há conteúdo específico cadastrado que pode sustentar um ângulo distinto, sem pesquisa externa. Validar se o material realmente comprova a mensagem."
    else:
        recommended = "B"
        why = "Com poucos dados de audiência, prova e histórico, a abordagem educativa permite explicar o escopo sem exigir alegações fortes. É a recomendação de menor dependência de pressupostos, não um vencedor estatístico."
    for item in base:
        item["recommended"] = item["id"] == recommended
    return {
        "items": base,
        "recommended_id": recommended,
        "recommendation_reason": why,
        "comparison_note": "Scores servem somente para organizar a revisão. Não escolha apenas pelo score; compare aderência aos dados, risco, esforço, tracking e objetivo.",
    }


def combine_strategies(strategies, ids):
    selected = [next((item for item in strategies if item.get("id") == key), None) for key in ids]
    if len(selected) != 2 or any(item is None for item in selected) or selected[0]["id"] == selected[1]["id"]:
        raise ValueError("Selecione exatamente duas estratégias diferentes para combinar.")
    first, second = selected
    label = f"Combinação {first['code']} → {second['code']}"
    codes = {first["code"], second["code"]}
    if codes == {"A", "B"}:
        logic = "Pergunta educativa → explicação útil → oferta direta → CTA; sem duplicar promessas."
        angle = first["angle"] if first["code"] == "A" else second["angle"]
        hook = second["hook"] if second["code"] == "B" else first["hook"]
    elif "C" in codes:
        distinct = first if first["code"] == "C" else second
        education = next((x for x in selected if x["code"] == "B"), None)
        direct = next((x for x in selected if x["code"] == "A"), None)
        angle = distinct["angle"]
        hook = education["hook"] if education else distinct["hook"]
        logic = f"Usar o ângulo factual '{angle['name']}' dentro de uma sequência coerente: {education['logic'] if education else direct['logic'] if direct else 'apresentação do produto'}"
    else:
        logic, angle, hook = first["logic"], first["angle"], first["hook"]
    scores = [_score for _score in (first.get("score", 0), second.get("score", 0))]
    item = {
        "id": "COMBINED", "code": "COMBINED", "name": label, "logic": logic,
        "avatar": first["avatar"], "angle": angle, "hook": hook,
        "offer": f"Usar as condições reais da oferta escolhida; combinar somente estrutura e ângulo, não criar preço, bônus ou garantia.",
        "creative": f"{first['creative']} Sequência complementar: {second['creative']}",
        "audience": first["audience"], "campaign": first["campaign"], "budget_hint": first["budget_hint"],
        "complexity": "Alta", "risk": "Médio", "risk_reason": "Combina duas abordagens e exige revisão de coerência; executar uma variável por teste.",
        "score": _clamp(mean(scores) - 3),
        "score_components": {"combined_component_mean": _clamp(mean(scores)), "combination_complexity_adjustment": -3},
        "score_explanation": "Média heurística dos dois scores escolhidos, com ajuste de complexidade; não é previsão de resultado.",
        "assumptions": ["A combinação usa somente os elementos escolhidos.", "Não é recomendado testar duas lógicas simultaneamente sem desenho controlado.", "Pesquisa de concorrentes não realizada."],
        "different_from_others": f"Combina elementos de {first['name']} e {second['name']} em uma sequência única.",
        "combined_from": [first["id"], second["id"]], "recommended": False,
    }
    return item


def _valid_text(value, fallback):
    if not value or value in (MISSING_PAGE, MISSING_USER):
        return fallback
    return value


def _copy_sets(brief, strategy_item, avatar_id, angle_id, offer_id):
    product = brief.get("product", {})
    name = _valid_text(product.get("name"), "o produto")
    niche = _valid_text(product.get("niche"), "o tema informado")
    benefit = _valid_text(product.get("benefits"), "o escopo disponível na página")
    description = _valid_text(product.get("description"), "as informações cadastradas")
    hooks = [
        f"O que vale conferir antes de escolher uma proposta sobre {niche}?",
        f"Conheça o escopo informado de {name}.",
        f"Uma pergunta útil para explorar {niche}: por onde começar?",
        f"Antes de decidir, revise conteúdo, formato e condições de {name}.",
        f"{strategy_item.get('hook', f'Confira os detalhes de {name}.')}",
    ]
    headlines = [
        f"Conheça o conteúdo de {name}",
        f"Explore {niche} com informações claras",
        f"Confira o escopo de {name}",
        f"Veja formato e condições antes de decidir",
        _valid_text(product.get("headline"), f"Um ponto de partida para explorar {niche}"),
    ]
    ctas = ["Ver detalhes", "Conhecer o conteúdo", "Conferir a proposta", "Acessar a página", "Saiba mais"]
    primary = []
    for index, hook in enumerate(hooks):
        body = [hook, f"{name} apresenta: {benefit}.", f"Confira descrição, preço e condições atuais na página antes de decidir."]
        if index == 2:
            body = [hook, f"{description}.", f"Veja se o formato e o escopo de {name} correspondem ao que procura."]
        if index == 3:
            body = [hook, f"Os dados disponíveis descrevem {name} como: {description}.", "Consulte a página e o checkout para confirmar as condições." ]
        if index == 4:
            body = [hook, f"{benefit}.", f"Acesse a página de {name} para conferir os detalhes. A adequação depende do contexto individual."]
        primary.append({
            "id": new_id(), "type": "primary", "label": f"Copy {index + 1} — {['Direta informativa','Educativa','Exploratória','Transparência','Ângulo escolhido'][index]}",
            "variation": ["Direta", "Educativa", "Exploratória", "Transparência", "Estratégia escolhida"][index],
            "text": "\n\n".join(body), "hook": hook, "avatar_id": avatar_id, "angle_id": angle_id,
            "offer_id": offer_id, "source_note": "Texto editorial local. Claims e condições devem ser confirmados; não há prova social ou promessa de resultado inventada.",
        })
    headline_items = [{"id": new_id(), "type": "headline", "label": f"Headline {index + 1}", "variation": "Heurística editorial", "text": text, "avatar_id": avatar_id, "angle_id": angle_id, "offer_id": offer_id} for index, text in enumerate(headlines)]
    cta_items = [{"id": new_id(), "type": "cta", "label": f"CTA {index + 1}", "variation": "Informativo", "text": text, "avatar_id": avatar_id, "angle_id": angle_id, "offer_id": offer_id} for index, text in enumerate(ctas)]
    return primary + headline_items + cta_items, hooks, headlines, ctas


def _image_concepts(brief, strategy_item, avatar, angle, offer, copies):
    product = brief.get("product", {})
    name = _valid_text(product.get("name"), "produto")
    concepts = [
        ("Imagem 1 — Explicação", "Explicar a sequência problema/tema → conteúdo real → CTA informativo", "Feed 4:5"),
        ("Imagem 2 — Escopo", "Mostrar somente módulo, página ou material real fornecido; se indisponível, usar placeholder abstrato rotulado", "Quadrado 1:1"),
        ("Imagem 3 — Critérios", "Organizar conteúdo, preço/condições e pergunta frequente apenas quando confirmados", "Stories/Reels 9:16"),
    ]
    result = []
    for index, (concept, objective, fmt) in enumerate(concepts):
        copy_item = copies[index]
        prompt = (
            f"Briefing de layout para {name}; conceito {concept}. Estratégia: {strategy_item['name']}. "
            f"Hook: {copy_item['hook']} Headline: {copy_item['text'].splitlines()[0]}. Direção: {objective}. "
            "Usar somente ativos autorizados e fatos confirmados; não inventar números, depoimentos, logos, garantias, bônus, escassez, antes/depois ou resultados. "
            "Se não houver captura real, identificar como placeholder. Recompôr para o formato indicado; não entregar mídia renderizada."
        )
        result.append({
            "id": new_id(), "name": concept, "media_type": "image", "concept_type": concept.split("—", 1)[-1].strip(),
            "objective": objective, "avatar_id": avatar["id"], "avatar_name": avatar["name"],
            "angle_id": angle["id"], "angle_name": angle["name"], "offer_id": offer["id"], "copy_id": copy_item["id"],
            "hook": copy_item["hook"], "headline": copy_item["text"].splitlines()[0], "subheadline": offer["subheadline"],
            "text": copy_item["text"], "copy_preview": copy_item["text"][:260], "visual_direction": objective,
            "visual": objective, "composition": objective, "elements": "Ativo de produto autorizado (se disponível) + texto editorial; placeholder rotulado quando necessário.",
            "cta": "Ver detalhes", "format": fmt, "recommended_format": fmt,
            "image_prompt": prompt, "image_prompts": [{"size": "1080x1350", "ratio": "4:5", "prompt": prompt}],
            "video_prompt": "", "video_scripts": {}, "image_generated": False, "video_generated": False,
            "media_status": "Conceito/prompt apenas; nenhum serviço de geração de mídia está configurado.", "model_available": False,
            "source_note": "Briefing local baseado somente nos dados informados; revisar direitos e claims.",
        })
    return result


def _video_concepts(brief, strategy_item, avatar, angle, offer, copies):
    product = brief.get("product", {})
    name = _valid_text(product.get("name"), "produto")
    niche = _valid_text(product.get("niche"), "tema informado")
    result = []
    for index, concept in enumerate(("Vídeo 1 — Pergunta educativa", "Vídeo 2 — Demonstração factual", "Vídeo 3 — Condições transparentes")):
        copy_item = copies[index + 2]
        hook = copy_item["hook"]
        problem = f"Contexto informativo sobre {niche}; sem atribuir problemas pessoais a quem assiste."
        solution = f"Mostrar somente a descrição ou o conteúdo real de {name}; se não houver material, usar texto em tela sem simular uma interface real."
        offer_text = f"Confira o escopo e as condições de {name}: preço/condições não presumidos."
        cta = f"Acesse a página de {name} e confira os detalhes."
        scripts = {duration: _video_script(duration, hook, problem, solution, offer_text, cta, name) for duration in (15, 30, 45)}
        result.append({
            "id": new_id(), "name": concept, "media_type": "video", "concept_type": concept.split("—", 1)[-1].strip(),
            "objective": "Story/roteiro; validar texto, duração e formato na conta antes de produzir.",
            "avatar_id": avatar["id"], "avatar_name": avatar["name"], "angle_id": angle["id"], "angle_name": angle["name"],
            "offer_id": offer["id"], "copy_id": copy_item["id"], "hook": hook,
            "headline": copy_item["text"].splitlines()[0], "subheadline": offer["subheadline"], "text": copy_item["text"],
            "copy_preview": copy_item["text"][:260], "visual_direction": "Storyboard com tipografia, cortes simples e ativo real/placeholder identificado.",
            "visual": "Usar somente material autorizado; sem depoimentos ou resultados simulados.",
            "composition": "Hook → explicação → escopo → CTA; revisar a montagem e a legibilidade.",
            "elements": "Roteiro de 15/30/45s; sem locução ou mídia gerada neste sistema.", "cta": "Ver detalhes", "format": "Vídeo vertical · storyboard 15/30/45s",
            "recommended_format": "Revisar proporção e posicionamento na interface Meta atual; não consultada.",
            "image_prompt": "", "image_prompts": [], "video_prompt": scripts[15]["full_narration"], "video_scripts": scripts,
            "image_generated": False, "video_generated": False,
            "media_status": "Conceito e roteiro apenas; nenhum serviço de geração de vídeo está configurado.", "model_available": False,
            "source_note": "Não fingir vídeo pronto. Gravar/produzir somente após aprovação humana do roteiro.",
        })
    return result


def unit_economics(brief):
    p = brief.get("product", {})
    price, cost, fees = p.get("price"), p.get("unit_cost"), p.get("fees_per_sale")
    missing = []
    if price is None: missing.append("preço de venda")
    if cost is None: missing.append("custo variável por venda")
    if fees is None: missing.append("taxas por venda")
    if missing:
        return {
            "status": "Dados insuficientes", "price": price, "unit_cost": cost, "fees_per_sale": fees,
            "break_even_cpa": None, "break_even_roas": None, "missing": missing,
            "note": "Unit economics não calculados com custos ausentes. Não foram presumidas taxas, impostos, reembolsos ou margem.",
        }
    contribution = round(price - cost - fees, 2)
    return {
        "status": "Calculado com valores informados" if contribution > 0 else "Revisar valores informados",
        "price": price, "unit_cost": cost, "fees_per_sale": fees,
        "contribution_before_ads": contribution,
        "break_even_cpa": contribution if contribution > 0 else None,
        "break_even_roas": round(price / contribution, 2) if contribution > 0 and price > 0 else None,
        "missing": [],
        "note": "Cálculo apenas com preço, custo e taxa informados. Exclui custos fixos, impostos não informados, reembolsos e chargebacks; não é recomendação financeira.",
    }


def build_selected_package(brief, strategy_item, product_record):
    """Build the post-selection package only. No output is generated by generate_strategies."""
    p = brief.get("product", {})
    name = _valid_text(p.get("name"), product_record.get("name", "Produto"))
    avatar_id, angle_id = new_id(), new_id()
    avatar = {
        "id": avatar_id, "name": f"Perfil de hipótese — {_valid_text(p.get('niche'), 'tema a confirmar')}",
        "age": "Não inferida", "occupation": "Não inferida", "situation": strategy_item.get("avatar", ""),
        "problem": "A validar por pesquisa e dados próprios; nenhum problema pessoal foi atribuído ao espectador.",
        "desire": "Entender escopo, formato e condições da proposta; hipótese de mensagem, não dado observado.",
        "objections": "Adequação, formato, preço e condições — confirmar com pesquisa/atendimento real.",
        "fictional_notice": "Perfil hipotético editorial criado localmente. Não representa pesquisa nem pessoa real; sem idade, gênero ou atributos sensíveis inventados.",
        "source_note": "Baseado no público informado pelo usuário quando disponível; caso contrário, hipótese ampla.",
        "potential_score": None, "score_type": "Não avaliado com dados de audiência.",
    }
    angle = {
        "id": angle_id, "name": strategy_item.get("angle", {}).get("name", strategy_item.get("name", "Ângulo escolhido")),
        "category": strategy_item.get("code", "COMBINED"), "idea": strategy_item.get("angle", {}).get("description", ""),
        "hook": strategy_item.get("hook", ""), "promise": "Não criar promessa além dos fatos confirmados.",
        "mechanism": p.get("mechanism") if p.get("mechanism") not in ("", MISSING_PAGE, MISSING_USER) else "Não identificado; usar somente a descrição real.",
        "cta": "Ver detalhes", "avatar_id": avatar_id, "avatar_name": avatar["name"],
        "potential_score": strategy_item.get("score"), "score": strategy_item.get("score"),
        "potential_basis": "Score heurístico de estratégia, não desempenho observado.", "top5_recommended": True,
    }
    price = p.get("price")
    price_text = f"R$ {price:.2f} (confirmar na página e checkout)" if price is not None else "Preço não informado"
    benefit = _valid_text(p.get("benefits"), "benefício não identificado; apresentar o escopo cadastrado")
    offer_ids = [new_id(), new_id()]
    offers = [
        {
            "id": offer_ids[0], "name": "Oferta base — escopo transparente", "recommended": True,
            "headline": _valid_text(p.get("headline"), f"Conheça {name}"),
            "subheadline": f"Confira o conteúdo, formato, preço e condições cadastradas. {price_text}.",
            "promise": "Sem promessa de resultado; a adequação depende do contexto individual.",
            "benefits": [benefit], "bonuses": p.get("bonuses") if p.get("bonuses") not in ("", MISSING_PAGE, MISSING_USER) else "Não identificado; não adicionar.",
            "guarantee": p.get("guarantee") if p.get("guarantee") not in ("", MISSING_PAGE, MISSING_USER) else "Não identificada; não anunciar garantia.",
            "price": price_text, "price_value": price, "cta": "Ver detalhes", "avatar_id": avatar_id, "avatar_name": avatar["name"],
            "angle_id": angle_id, "angle_name": angle["name"], "source_note": "Preço/termos vêm do briefing e devem coincidir com a página e o checkout.",
            "disclaimer": "Oferta editorial; sem bônus, garantia, urgência ou prova inventados.",
        },
        {
            "id": offer_ids[1], "name": "Variação de mensagem — conteúdo e condições", "recommended": False,
            "headline": _valid_text(p.get("headline"), f"Veja o que está incluído em {name}"),
            "subheadline": f"Uma variação editorial para explicar o escopo antes do CTA. {price_text}.",
            "promise": "Mesmas condições da oferta base; varia apenas a forma de apresentar o escopo.",
            "benefits": [benefit], "bonuses": "Não acrescentar; nenhum bônus novo foi informado.",
            "guarantee": "Não acrescentar; verificar termos reais.", "price": price_text, "price_value": price,
            "cta": "Conhecer o conteúdo", "avatar_id": avatar_id, "avatar_name": avatar["name"],
            "angle_id": angle_id, "angle_name": angle["name"], "source_note": "Variação de comunicação; as condições comerciais são idênticas e dependem de confirmação.",
            "disclaimer": "Teste hipotético, não oferta ativa nem recomendação financeira.",
        },
    ]
    for offer in offers:
        offer["score"] = strategy_item.get("score")
        offer["score_reason"] = "Nota emprestada da estratégia escolhida, não score de oferta ou conversão."
    copies, hooks, headlines, ctas = _copy_sets(brief, strategy_item, avatar_id, angle_id, offer_ids[0])
    primary_copies = [item for item in copies if item["type"] == "primary"]
    creatives = _image_concepts(brief, strategy_item, avatar, angle, offers[0], primary_copies) + _video_concepts(brief, strategy_item, avatar, angle, offers[0], primary_copies)
    image_assets = [item for item in creatives if item.get("media_type") == "image"]
    campaign_url = p.get("checkout_url") or p.get("sales_url") or ""
    tracking_reported = "Pixel/evento" in (brief.get("data_available_reported") or [])
    ads = []
    for index, asset in enumerate(image_assets):
        copy_item = primary_copies[index]
        ads.append({
            "id": new_id(), "ad_name": f"Anúncio {index + 1:02d} — {asset['name']}",
            "creative_id": asset["id"], "copy_id": copy_item["id"], "hook": hooks[index],
            "headline": headlines[index], "description": "", "cta": ctas[index], "url": campaign_url,
        })
    daily_budget = brief.get("daily_budget")
    budget_note = (
        "Manter o orçamento diário informado na estrutura inicial; três anúncios compartilham a entrega. Não há divisão fixa por anúncio e não se presume como a plataforma distribuirá a verba."
        if daily_budget is not None else "Orçamento diário não informado; não criar valor sugerido. Definir manualmente antes de qualquer execução."
    )
    budget_distribution = {
        "daily_budget": daily_budget,
        "suggested_structure": "Uma campanha/conjunto de planejamento com três anúncios; evitar pulverizar antes de ter baseline.",
        "allocation": [{"label": "Conjunto inicial", "percentage": 100 if daily_budget is not None else None, "daily_amount": daily_budget, "rationale": budget_note}],
        "controlled_test": "Se comparar duas células, desenhar 50/50 somente como hipótese matemática e apenas se orçamento e janela escolhidos pelo anunciante comportarem as duas. Não é regra universal nem previsão.",
        "rationale": budget_note,
        "notice": "A verba é sugestão de planejamento; valores e orçamento final devem ser aprovados pelo usuário na conta Meta.",
    }
    tests = [
        {"id": new_id(), "name": "Teste 1 — Hook", "type": "Hook", "variant_a": hooks[0], "variant_b": hooks[1], "hypothesis": "Comparar abertura mantendo criativo, oferta, público, destino e período tão constantes quanto possível. Hipótese, sem vencedor definido.", "status": "Pausado"},
        {"id": new_id(), "name": "Teste 2 — Conceito visual", "type": "Criativo", "variant_a": image_assets[0]["name"], "variant_b": image_assets[1]["name"], "hypothesis": "Mudar somente o conceito visual; manter hook/copy/oferta e condições equivalentes quando possível.", "status": "Pausado"},
        {"id": new_id(), "name": "Teste 3 — Enquadramento da oferta", "type": "Oferta", "variant_a": offers[0]["subheadline"], "variant_b": offers[1]["subheadline"], "hypothesis": "As condições permanecem iguais; testar somente a forma de explicar o escopo. Não há previsão de conversão.", "status": "Pausado"},
    ]
    matrix = []
    for ci, creative in enumerate(image_assets):
        for hi, hook in enumerate(hooks[:3]):
            matrix.append({
                "creative": creative["name"], "hook": hook,
                "priority": "Prioridade inicial" if ci == hi else "Alternativa para teste sequencial",
                "selected": ci == hi,
            })
    tracking_check = {
        "status": "Não verificado", "user_reported": tracking_reported,
        "required_before_sales_optimization": ["Conferir Pixel/Dataset e permissões na conta Meta.", "Verificar o evento adequado no Events Manager.", "Testar o evento e confirmar consistência com página/checkout."],
        "note": "O sistema não consultou Pixel, CAPI, eventos, domínio, conta ou interface Meta. O relato do usuário não é verificação técnica.",
    }
    meta_plan = {
        "campaign_name": f"{name} — {strategy_item['name']}",
        "commercial_goal": "Vendas" if brief.get("objective") == "Vender" else (brief.get("objective") or "A definir"),
        "optimization": "Confirmar manualmente na conta. Para vendas, aguardar verificação do evento; o app não presume opções atuais da interface Meta.",
        "audience": "Hipótese ampla no mercado informado; sem Lookalike até validar volume, elegibilidade e consentimento.",
        "location": ", ".join(x for x in (brief.get("location", {}).get("city"), brief.get("location", {}).get("region"), brief.get("location", {}).get("country")) if x) or brief.get("market", "Não informado"),
        "placements": "Revisar na interface Meta atual; não consultada nesta implantação.",
        "budget": daily_budget,
        "tracking": tracking_check,
        "ads": ads,
        "publishing_notice": "Planejamento local. Nenhum anúncio foi criado na Meta, enviado ou publicado.",
    }
    simulator = unit_economics(brief)
    policy_reviews = []
    for ad in ads:
        copy_item = next((item for item in primary_copies if item["id"] == ad.get("copy_id")), {})
        review_text = "\n".join(part for part in (copy_item.get("text", ""), ad.get("hook", ""), ad.get("headline", ""), ad.get("description", ""), ad.get("cta", "")) if str(part or "").strip())
        policy_reviews.append(policy.check_copy(review_text))
    overall_policy = "Alto" if any(item["risk"] == "Alto" for item in policy_reviews) else "Médio" if any(item["risk"] == "Médio" for item in policy_reviews) else "Baixo"
    policy_check = {
        "risk": overall_policy,
        "ad_count": len(ads),
        "ad_reviews": [{"ad_name": ads[index]["ad_name"], "risk": review["risk"], "findings": review.get("findings", []), "suggested_version": review.get("suggested_version")} for index, review in enumerate(policy_reviews)],
        "findings": [finding | {"ad_name": ads[index]["ad_name"]} for index, review in enumerate(policy_reviews) for finding in review.get("findings", [])],
        "note": "Policy Check heurístico local em copy, hook, headline e CTA. Não é aprovação Meta e não garante aprovação. Execute novamente após qualquer edição antes da aprovação explícita.",
    }
    return {
        "avatar": avatar, "angle": angle, "offers": offers, "copies": copies, "creatives": creatives,
        "hooks": hooks, "headlines": headlines, "ctas": ctas, "creative_matrix": matrix,
        "budget_distribution": budget_distribution, "test_plan": tests, "tracking_check": tracking_check,
        "unit_economics": simulator, "meta_plan": meta_plan, "policy_check": policy_check,
        "selected_strategy": strategy_item,
        "generation_notice": "Saídas locais geradas somente após a seleção da estratégia. Conceitos, prompts e roteiros não são mídia renderizada.",
    }


def simulate_scenarios(daily_budget, days, price, cpa_scenarios, unit_cost=None, fees_per_sale=None):
    budget = _number(daily_budget)
    days_value = int(_number(days) or 0)
    price_value = _number(price)
    if budget is None or budget <= 0:
        raise ValueError("Informe orçamento diário maior que zero para simular.")
    if days_value < 1 or days_value > 365:
        raise ValueError("O período da simulação deve estar entre 1 e 365 dias.")
    if price_value is None or price_value <= 0:
        raise ValueError("Informe o preço considerado maior que zero.")
    if not isinstance(cpa_scenarios, list) or not 1 <= len(cpa_scenarios) <= 5:
        raise ValueError("Informe de 1 a 5 cenários hipotéticos de CPA.")
    spend = round(budget * days_value, 2)
    results = []
    cost = _number(unit_cost) if unit_cost not in (None, "") else None
    fees = _number(fees_per_sale) if fees_per_sale not in (None, "") else None
    for index, raw_cpa in enumerate(cpa_scenarios):
        cpa = _number(raw_cpa)
        if cpa is None or cpa <= 0:
            raise ValueError("Cada CPA hipotético deve ser maior que zero.")
        purchases_equiv = spend / cpa
        revenue_equiv = purchases_equiv * price_value
        result = {"label": f"Cenário {index + 1}", "hypothetical_cpa": cpa, "spend": spend, "equivalent_purchases": round(purchases_equiv, 2), "mathematical_revenue": round(revenue_equiv, 2), "mathematical_roas": round(revenue_equiv / spend, 2) if spend else None}
        if cost is not None and fees is not None:
            result["mathematical_contribution_after_variable_costs"] = round(purchases_equiv * (price_value - cost - fees) - spend, 2)
        else:
            result["mathematical_contribution_after_variable_costs"] = None
        results.append(result)
    return {
        "label": "SIMULAÇÃO MATEMÁTICA — NÃO É PREVISÃO",
        "assumptions": {"daily_budget": budget, "days": days_value, "price": price_value, "unit_cost": cost, "fees_per_sale": fees},
        "scenarios": results,
        "note": "Vendas equivalentes, receita, ROAS e contribuição são divisões/multiplicações hipotéticas usando CPAs digitados pelo usuário. Não são estimativa estatística, previsão, promessa, nem dados observados. Impostos, custos fixos, reembolsos e atribuição só entram se informados.",
    }


def diagnose_feedback(brief, project):
    metrics = project.get("metrics") or []
    campaign = project.get("campaign") or {}
    latest = metrics[0] if metrics else None
    if campaign.get("tracking_status") != "Configurado e testado":
        return {
            "status": "Rastreamento não verificado",
            "gargalo": "Não interpretar conversões ou otimizar para vendas ainda.",
            "prioridade": "Verificar manualmente Pixel/Dataset, evento e checkout; só então coletar baseline.",
            "variable_to_change": "Rastreamento/verificação (não variável criativa)",
            "test": None,
            "evidence": ["Estado de tracking salvo no projeto: " + _string(campaign.get("tracking_status") or "Não informado")],
            "note": "Não é possível identificar um gargalo de performance sem rastreamento e dados comparáveis.",
        }
    if not latest:
        return {
            "status": "Sem métricas salvas",
            "gargalo": "Não há baseline para atribuir desempenho a hook, criativo, página ou público.",
            "prioridade": "Registrar métricas por período e ativo; manter a estratégia atual sem declarar vencedor.",
            "variable_to_change": "Nenhuma até registrar baseline",
            "test": None,
            "evidence": [],
            "note": "Sem amostra, a recomendação é coletar dados, não gerar uma explicação causal.",
        }
    spend = float(latest.get("spend") or 0)
    impressions = int(latest.get("impressions") or 0)
    clicks = int(latest.get("clicks") or 0)
    purchases = int(latest.get("purchases") or 0)
    evidence = [f"Registro mais recente ({latest.get('date') or latest.get('created_at','')})", f"Impressões: {impressions}; cliques: {clicks}; compras: {purchases}; gasto: R$ {spend:.2f}."]
    test = None
    proposed_change = None
    if impressions and clicks == 0:
        status, bottleneck, priority, variable = "Revisar atração", "Impressões sem cliques registrados; isso não identifica sozinho a causa.", "Priorizar um teste de hook mantendo criativo, oferta e público constantes.", "Hook"
        ads = campaign.get("ads") or []
        current_hook = _string((ads[0] or {}).get("hook"), 240) if ads else ""
        current_hook = current_hook or "Hook atual não registrado"
        product_name = _string(brief.get("product", {}).get("name"), 120) or "produto"
        next_hook = f"Confira o que {product_name} inclui e como funciona antes de decidir."
        if next_hook.casefold() == current_hook.casefold():
            next_hook = f"Veja o escopo e o formato de {product_name} antes de decidir."
        test = {"name": "Próximo teste — Hook", "type": "Hook", "variant_a": current_hook, "variant_b": next_hook, "hypothesis": "Mudar somente o hook; manter criativo, oferta, público e condições constantes. A variação fica pausada até revisão humana; nenhum vencedor presumido."}
        proposed_change = {"variable": "Hook", "current": current_hook, "suggested": next_hook, "status": "Proposta concreta salva como teste pausado; não aplicada nem publicada automaticamente."}
    elif clicks and purchases == 0:
        status, bottleneck, priority, variable = "Revisar conversão", "Há cliques sem compras registradas; conferir URL, página, checkout, janela e atribuição.", "Primeiro validar checkout e atribuição; se estiverem corretos, testar uma mudança de página por vez.", "Página/checkout ou atribuição — confirmar antes de mudar copy"
        test = {"name": "Próxima verificação — Página/checkout", "type": "Página", "variant_a": "Fluxo atual (documentado)", "variant_b": "Uma correção confirmada do gargalo (a definir após inspeção)", "hypothesis": "Não mudar criativo antes de verificar checkout e atribuição; não inferir erro sem inspeção."}
    else:
        status, bottleneck, priority, variable = "Monitorar com cautela", "Os dados não isolam um único gargalo causal.", "Comparar ativos associados e mudar apenas uma variável na próxima hipótese.", "Uma variável por teste; escolher após revisão manual"
        if purchases:
            test = {"name": "Próximo teste — Uma variável", "type": "Teste", "variant_a": "Configuração atual", "variant_b": "Uma única alteração documentada pelo responsável", "hypothesis": "Registrar condição, período e atribuição; não declarar significância sem análise apropriada."}
    return {"status": status, "gargalo": bottleneck, "prioridade": priority, "variable_to_change": variable, "proposed_change": proposed_change, "test": test, "evidence": evidence, "note": "Diagnóstico descritivo, não causal. Compare mesma janela/atribuição e amostra suficiente; não existe vencedor estatístico calculado."}


def chat_reply(message, run_data, project):
    text = _string(message, 2000)
    low = text.lower()
    brief = run_data.get("brief", {})
    strategy_item = run_data.get("selected_strategy") or {}
    metrics = project.get("metrics") or []
    campaign = project.get("campaign") or {}
    latest = metrics[0] if metrics else None
    history = []
    if any(word in low for word in ("ctr", "clique", "criativo", "hook")):
        if latest:
            history.append(f"Último registro salvo: CTR {latest.get('ctr') if latest.get('ctr') is not None else 'não calculado'}%, {latest.get('clicks', 0)} cliques e {latest.get('impressions', 0)} impressões.")
            reply = "Esses números descrevem apenas o registro informado; sem comparação equivalente, não concluem que um hook/criativo é melhor. Eu priorizaria um único teste de hook ou conceito visual e manteria as demais variáveis constantes."
        else:
            reply = "Ainda não há métricas salvas neste projeto para dizer se o CTR/criativo é bom ou ruim. Registre impressões, cliques, janela e ativo associado; evito usar benchmark universal."
    elif any(word in low for word in ("cpa", "roas", "compra", "venda", "vendas")):
        if campaign.get("tracking_status") != "Configurado e testado":
            reply = "O rastreamento do projeto está como não verificado. Antes de otimizar para compra, confira manualmente Pixel/Dataset, evento e checkout. Este sistema não consulta a conta Meta."
        elif latest:
            reply = f"Último registro salvo: CPA {latest.get('cpa') if latest.get('cpa') is not None else 'não calculado'} e ROAS {latest.get('roas') if latest.get('roas') is not None else 'não calculado'}x. Compare com custos realmente informados e a mesma janela; esses dados não provam causalidade."
        else:
            reply = "Não há métricas de compra registradas. Não posso inferir CPA/ROAS. Registre gasto, compras, receita e atribuição; custos unitários ausentes impedem ponto de equilíbrio."
    elif any(word in low for word in ("pixel", "evento", "capi", "rastreamento", "tracking")):
        reply = f"Status salvo da campanha: {campaign.get('tracking_status') or 'Não informado'}. Relatos do briefing: {brief.get('tracking_status', 'Não informado')}. Nada foi verificado tecnicamente; teste evento e checkout manualmente antes de otimizar para vendas."
    elif any(word in low for word in ("orçamento", "orcamento", "budget", "investimento")):
        budget = brief.get("daily_budget")
        reply = (f"Orçamento diário registrado: R$ {budget:.2f}. O plano usa esse valor como teto de planejamento; os três anúncios compartilham a entrega, sem prever divisão ou resultado." if budget is not None else "Não há orçamento diário informado. Posso estruturar a lógica, mas não calcular uma distribuição numérica sem o valor escolhido pelo usuário.")
    elif any(word in low for word in ("por que", "recomend", "estratégia", "estrategia", "score")):
        reply = f"Estratégia selecionada: {strategy_item.get('name', 'não selecionada')}. Motivo registrado: {run_data.get('recommendation_reason') or strategy_item.get('recommendation_reason') or 'revisar os critérios e hipóteses no blueprint'}. O score é heurístico e não decide sozinho."
    elif any(word in low for word in ("concorrente", "concorrência", "concorrencia", "mercado")):
        reply = "Nenhuma pesquisa externa de concorrentes/mercado foi feita nesta implantação. Posso sugerir ângulos conceituais e marcar hipóteses, mas não afirmar posicionamento real sem fontes públicas analisadas."
    else:
        reply = f"Vou usar o contexto salvo do produto '{brief.get('product', {}).get('name', 'não identificado')}', a estratégia {strategy_item.get('name', 'ainda não escolhida')} e os dados registrados neste projeto. Para diagnosticar desempenho, informe período, métrica, ativo e se o tracking/checkout foi verificado. Como este ambiente não tem provedor de LLM configurado, a resposta é local e baseada em regras, não uma conversa com modelo externo."
    return {"role": "assistant", "text": reply, "evidence": history, "created_at": None}
