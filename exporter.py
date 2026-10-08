"""Self-contained TXT/CSV/JSON and a simple text PDF campaign export."""
from __future__ import annotations

import csv
import io
import json
import textwrap
import unicodedata


def _value(value, fallback="Não informado."):
    if value is None or value == "" or value == []:
        return fallback
    if isinstance(value, list):
        return "; ".join(str(x) for x in value)
    return str(value)


def campaign_lines(project):
    product = project.get("product") or {}
    campaign = project.get("campaign") or {}
    lines = [
        "CAKTO ADS AI — PACOTE DE CAMPANHA",
        f"Projeto: {_value(project.get('name'))}",
        f"Produto: {_value(product.get('name'))}",
        f"Página: {_value(product.get('sales_url'))}",
        f"Preço: {_value(product.get('promo_price') or product.get('price'))}",
        "",
        "ESTRUTURA META ADS (CONFIGURAÇÃO MANUAL)",
        f"Campanha: {_value(campaign.get('campaign_name'))}",
        f"Objetivo sugerido: {_value(campaign.get('objective'))}",
        f"Conjunto: {_value(campaign.get('adset_name'))}",
        f"Público: {_value(campaign.get('audience'))}",
        f"Posicionamentos: {_value(campaign.get('placements'))}",
        f"Orçamento diário: {_value(campaign.get('daily_budget'))}",
        f"Evento / status do rastreamento: {_value(campaign.get('conversion_event'))} / {_value(campaign.get('tracking_status'))}",
        f"URL final: {_value(campaign.get('url'))}",
        "",
        "AVATAR PRINCIPAL",
    ]
    avatar = next((x for x in project.get("avatars", []) if x.get("id") == project.get("selected_avatar_id")), None)
    lines.extend([f"Nome: {_value((avatar or {}).get('name'))}", f"Problema: {_value((avatar or {}).get('problem'))}", f"Desejo: {_value((avatar or {}).get('desire'))}", "", "OFERTA RECOMENDADA"])
    offer = next((x for x in project.get("offers", []) if x.get("id") == project.get("selected_offer_id")), None)
    if offer:
        for key, label in (("name", "Nome"), ("headline", "Headline"), ("subheadline", "Subheadline"), ("promise", "Promessa"), ("benefits", "Benefícios"), ("mechanism", "Mecanismo"), ("bonuses", "Bônus"), ("guarantee", "Garantia"), ("price", "Preço"), ("cta", "CTA"), ("score", "Score editorial")):
            lines.append(f"{label}: {_value(offer.get(key))}")
    else:
        lines.append("Não selecionada.")
    lines.extend(["", "COPY RECOMENDADA"])
    copy = next((x for x in project.get("copies", []) if x.get("id") == project.get("selected_copy_id")), None)
    lines.append(_value((copy or {}).get("text")))
    lines.extend(["", "ANÚNCIOS"])
    ads = campaign.get("ads") or []
    if not ads:
        creative = next((x for x in project.get("creatives", []) if x.get("id") == project.get("selected_creative_id")), None)
        ads = [{"ad_name": (creative or {}).get("name"), "creative_id": (creative or {}).get("id"), "copy_id": (copy or {}).get("id"), "hook": (creative or {}).get("hook"), "headline": (creative or {}).get("headline"), "cta": (creative or {}).get("cta")}]
    for index, ad in enumerate(ads, 1):
        creative = next((x for x in project.get("creatives", []) if x.get("id") == ad.get("creative_id")), {})
        ad_copy = next((x for x in project.get("copies", []) if x.get("id") == ad.get("copy_id")), {})
        lines.extend([f"Anúncio {index}: {_value(ad.get('ad_name'))}", f"Hook: {_value(ad.get('hook'))}", f"Headline: {_value(ad.get('headline'))}", f"Copy: {_value(ad_copy.get('text') or ad.get('text'))}", f"CTA: {_value(ad.get('cta'))}", f"Formato: {_value(creative.get('format'))}", f"Prompt de imagem: {_value(creative.get('image_prompt'))}", ""])
    lines.extend(["NOTAS DE SEGURANÇA", "Briefings e prompts não são imagens/vídeos renderizados. Não há integração/publicação Meta nesta fase.", "Simulações de orçamento não são previsões. Revise preço, condições, rastreamento e política antes de configurar."])
    return lines


def render_txt(project):
    return ("\n".join(campaign_lines(project)) + "\n").encode("utf-8")


def render_csv(project):
    campaign = project.get("campaign") or {}
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=["campaign", "adset", "ad_name", "objective", "audience", "daily_budget", "creative", "hook", "primary_text", "headline", "description", "cta", "url"])
    writer.writeheader()
    ads = campaign.get("ads") or [{}]
    for ad in ads:
        creative = next((x for x in project.get("creatives", []) if x.get("id") == ad.get("creative_id")), {})
        copy = next((x for x in project.get("copies", []) if x.get("id") == ad.get("copy_id")), {})
        writer.writerow({
            "campaign": campaign.get("campaign_name", ""), "adset": campaign.get("adset_name", ""),
            "ad_name": ad.get("ad_name", ""), "objective": campaign.get("objective", ""),
            "audience": campaign.get("audience", ""), "daily_budget": campaign.get("daily_budget", ""),
            "creative": creative.get("name", ""), "hook": ad.get("hook", creative.get("hook", "")),
            "primary_text": copy.get("text", ""), "headline": ad.get("headline", creative.get("headline", "")),
            "description": ad.get("description", ""), "cta": ad.get("cta", creative.get("cta", "")),
            "url": campaign.get("url", ""),
        })
    return output.getvalue().encode("utf-8-sig")


def _pdf_escape(text):
    ascii_text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    return ascii_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def render_pdf(project):
    source = campaign_lines(project)
    wrapped = []
    for line in source:
        if not line:
            wrapped.append("")
        else:
            wrapped.extend(textwrap.wrap(line, width=92, replace_whitespace=True, drop_whitespace=True) or [""])
    pages = [wrapped[i:i + 58] for i in range(0, max(1, len(wrapped)), 58)]
    objects = {}
    page_refs = []
    for index, lines in enumerate(pages):
        page_id = 4 + index * 2
        content_id = page_id + 1
        page_refs.append(f"{page_id} 0 R")
        commands = ["BT", "/F1 10 Tf", "50 790 Td", "13 TL"]
        for line in lines:
            commands.append(f"({_pdf_escape(line)}) Tj")
            commands.append("T*")
        commands.append("ET")
        stream = "\n".join(commands).encode("ascii", "ignore")
        objects[page_id] = f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode("ascii")
        objects[content_id] = b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(page_refs)}] /Count {len(pages)} >>".encode("ascii")
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id in range(1, 4 + len(pages) * 2):
        offsets.append(len(pdf))
        pdf.extend(f"{object_id} 0 obj\n".encode("ascii"))
        pdf.extend(objects[object_id])
        pdf.extend(b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode("ascii"))
    return bytes(pdf)
