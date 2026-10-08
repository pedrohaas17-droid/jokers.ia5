"""Deterministic second-phase automation orchestration.

This is a replaceable workflow service. It ranks with explainable heuristics and
returns hypotheses, not model predictions or factual claims.
"""
from __future__ import annotations

from database import utc_now
from services import creative_pack, generator, policy, strategy


def choose_copy(copies):
    candidates = [copy for copy in copies if copy.get("type") == "primary"]
    if not candidates:
        candidates = list(copies)
    def rank(item):
        review = policy.check_copy(item.get("text", ""))
        risk = {"Baixo": 0, "Médio": 1, "Alto": 2}.get(review.get("risk"), 3)
        complete = 1 if len(item.get("text", "")) > 35 else 0
        style = 1 if item.get("variation") == "Direta" else 0
        return (risk, -complete, -style)
    selected = min(candidates, key=rank) if candidates else None
    if selected:
        selected["recommended"] = True
        selected["recommended_reason"] = "Prioriza texto com menor risco detectado, contexto do produto e leitura direta. É uma hipótese editorial, não evidência de desempenho."
    return selected


def build_automatic_package(project):
    product = project.get("product") or {}
    avatars = generator.generate_avatars(project)
    for avatar in avatars:
        avatar["recommended"] = False
    recommended_avatar = max(avatars, key=lambda item: item.get("potential_score", 0))
    recommended_avatar["recommended"] = True
    project["avatars"] = avatars
    project["selected_avatar_id"] = recommended_avatar["id"]

    angles = generator.generate_angles(project)
    ranked_angles = sorted(angles, key=lambda item: (item.get("score", 0), item.get("avatar_fit", 0)), reverse=True)
    top_angles = ranked_angles[:5]
    top_ids = {item["id"] for item in top_angles}
    for angle in angles:
        angle["top5_recommended"] = angle["id"] in top_ids
    recommended_angle = top_angles[0]
    project["angles"] = angles
    project["selected_angle_id"] = recommended_angle["id"]

    offers = generator.generate_campaign_offers(project, top_angles)
    ranked_offers = sorted(offers, key=lambda item: (item.get("score", 0), -item.get("score_components", {}).get("policy_risk", 100)), reverse=True)
    recommended_offer = ranked_offers[0]
    recommended_offer["recommended"] = True
    recommended_offer["recommendation_reason"] = "Maior score editorial combinado neste conjunto. Pontuação baseada em completude e coerência; não é previsão de vendas."
    recommended_angle = next((item for item in top_angles if item["id"] == recommended_offer.get("angle_id")), recommended_angle)
    project["offers"] = offers
    project["selected_offer_id"] = recommended_offer["id"]
    project["selected_angle_id"] = recommended_angle["id"]

    copies = generator.generate_copies(project)
    recommended_copy = choose_copy(copies)
    project["copies"] = copies
    project["selected_copy_id"] = recommended_copy.get("id") if recommended_copy else None

    creatives = creative_pack.generate_creative_pack(project, top_angles, offers, copies)
    for creative in creatives:
        creative["recommended"] = creative["concept_type"] == "Curiosidade"
    recommended_creative = next((x for x in creatives if x["recommended"]), creatives[0])
    project["creatives"] = creatives
    project["selected_creative_id"] = recommended_creative["id"]

    xray = strategy.build_xray(product, recommended_avatar, top_angles)
    selected_product_price = product.get("promo_price") if product.get("promo_price") not in (None, "") else product.get("price")
    unit_cost = product.get("unit_cost")
    contribution = round(float(selected_product_price) - float(unit_cost), 2) if selected_product_price not in (None, "") and unit_cost not in (None, "") else None
    breakeven_roas = round(float(selected_product_price) / contribution, 2) if contribution and contribution > 0 else None

    primary_texts = [x for x in copies if x.get("type") == "primary"]
    headline_copies = [x for x in copies if x.get("type") == "headline"]
    description_copies = [x for x in copies if x.get("type") == "description"]
    cta_copies = [x for x in copies if x.get("type") == "cta"]
    ads = []
    for index in range(3):
        creative = creatives[index]
        selected_copy = primary_texts[index % len(primary_texts)] if primary_texts else recommended_copy
        headline = headline_copies[index % len(headline_copies)] if headline_copies else {}
        description = description_copies[index % len(description_copies)] if description_copies else {}
        cta = cta_copies[index % len(cta_copies)] if cta_copies else {}
        ads.append({
            "id": creative["id"], "ad_name": f"Anúncio {index + 1:02d} — {creative['concept_type']}",
            "creative_id": creative["id"], "copy_id": selected_copy.get("id") if selected_copy else None,
            "hook": creative.get("hook"), "headline": headline.get("text") or creative.get("headline"),
            "description": description.get("text", ""), "cta": cta.get("text") or creative.get("cta"),
            "url": product.get("checkout_url") or product.get("sales_url") or "",
            "creative_concept": creative.get("concept_type"),
        })

    audience_options = [
        {"key": "broad", "name": "Público amplo", "recommendation": "Candidato inicial; usar localização/idade apenas se pertinentes ao produto e às políticas.", "why": "Evita limitar a entrega sem evidência; validar com dados da conta.", "eligible": True},
        {"key": "interests", "name": "Interesses", "recommendation": f"Testar temas amplos relacionados a {product.get('niche') or 'categoria não identificada'}; conferir opções realmente disponíveis na conta.", "why": "Pode servir como hipótese comparativa ao público amplo; não garante performance.", "eligible": bool(product.get("niche"))},
        {"key": "remarketing", "name": "Remarketing", "recommendation": "Não recomendado até confirmar fonte, consentimento, tamanho e elegibilidade do público personalizado.", "why": "Nenhum evento ou audiência da Meta foi consultado por esta aplicação.", "eligible": False},
        {"key": "lookalike", "name": "Lookalike", "recommendation": "Não recomendado: volume, qualidade e elegibilidade da seed não foram verificados.", "why": "A aplicação não tem acesso à conta Meta nem a dados de clientes.", "eligible": False},
    ]
    campaign_name = project.get("name") or f"Campanha — {product.get('name', 'produto')}"
    ad_policy_reviews = []
    for ad in ads:
        ad_copy = next((c for c in copies if c.get("id") == ad.get("copy_id")), {})
        review = policy.check_copy(ad_copy.get("text", ""))
        ad_policy_reviews.append({"ad_name": ad["ad_name"], "copy_id": ad.get("copy_id"), "risk": review.get("risk"), "findings": review.get("findings", []), "suggested_version": review.get("suggested_version")})
    policy_risks = [r.get("risk") for r in ad_policy_reviews]
    policy_risk = "Alto" if "Alto" in policy_risks else "Médio" if "Médio" in policy_risks else "Baixo"
    campaign = {
        "campaign_name": campaign_name,
        "adset_name": f"Prospecting | {product.get('niche') or 'Público amplo'}",
        "ad_name": "Pacote de 3 anúncios — revisão manual",
        "objective": "Vendas somente se o evento Purchase estiver configurado e testado; caso contrário, revisar rastreamento antes de escolher otimização.",
        "commercial_goal": "Vendas",
        "audience": audience_options[0]["recommendation"], "audiences": audience_options,
        "placements": "Sugestão: avaliar Advantage+ e revisar prévias 1:1, 4:5 e 9:16 no Gerenciador.",
        "budget": "Não informado.", "daily_budget": None,
        "location": "Definir manualmente conforme elegibilidade, logística e legislação do produto.",
        "tracking_status": "Não verificado", "conversion_event": "Purchase — confirmar e testar no Events Manager",
        "tracking_warning": "Este app não consultou seu Pixel/Dataset. Não trate evento ou rastreamento como configurado.",
        "creative_id": recommended_creative["id"], "copy_id": recommended_copy.get("id") if recommended_copy else None,
        "url": product.get("checkout_url") or product.get("sales_url") or "",
        "ads": ads, "campaign_mode": "automatic",
        "recommended_avatar_id": recommended_avatar["id"], "recommended_angle_id": recommended_angle["id"],
        "recommended_offer_id": recommended_offer["id"], "recommended_copy_id": recommended_copy.get("id") if recommended_copy else None,
        "recommended_creative_id": recommended_creative["id"],
        "recommendation_summary": {
            "avatar": {"id": recommended_avatar["id"], "name": recommended_avatar["name"], "score": recommended_avatar["potential_score"], "reason": recommended_avatar["score_reason"]},
            "angle": {"id": recommended_angle["id"], "name": recommended_angle["name"], "score": recommended_angle["score"], "reason": recommended_angle["score_reason"]},
            "offer": {"id": recommended_offer["id"], "name": recommended_offer["name"], "score": recommended_offer["score"], "reason": recommended_offer["recommendation_reason"]},
            "copy": {"id": recommended_copy.get("id"), "name": recommended_copy.get("label"), "reason": recommended_copy.get("recommended_reason")} if recommended_copy else {},
            "creative": {"id": recommended_creative["id"], "name": recommended_creative["name"], "reason": "Conceito de curiosidade escolhido como hipótese criativa inicial; valide com teste."},
        },
        "break_even_cpa": contribution, "break_even_roas": breakeven_roas,
        "budget_simulation_note": "Simulação matemática somente após informar orçamento e custo variável; não é previsão de vendas.",
        "checklist": {"campaign_reviewed": False, "manual_setup_completed": False},
        "policy_review": {
            "risk": policy_risk, "ad_count": len(ad_policy_reviews), "ad_reviews": ad_policy_reviews,
            "findings": [finding for review in ad_policy_reviews for finding in review.get("findings", [])],
            "suggested_version": next((review.get("suggested_version") for review in ad_policy_reviews if review.get("suggested_version")), "Nenhum ajuste sugerido."),
            "note": "Revisão heurística local; não é aprovação Meta.",
        },
        "policy_checked_at": utc_now(), "automation_version": 2,
        "publishing_notice": "Estrutura de planejamento apenas. Nenhuma integração Meta está conectada e nada será publicado automaticamente.",
    }
    return {
        "avatars": avatars, "angles": angles, "top_angles": top_angles,
        "offers": offers, "copies": copies, "creatives": creatives,
        "xray": xray, "campaign": campaign,
        "selected": {
            "avatar_id": recommended_avatar["id"], "angle_id": project["selected_angle_id"],
            "offer_id": recommended_offer["id"], "copy_id": campaign["copy_id"],
            "creative_id": recommended_creative["id"],
        },
        "provider": generator.PROVIDER,
    }
