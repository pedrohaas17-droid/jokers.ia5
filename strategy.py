"""Product x-ray and offer prioritization using transparent local heuristics."""
from __future__ import annotations

from services.generator import clean, lines

MISSING = "Não identificado na página."


def build_xray(product, avatar=None, top_angles=None):
    product = product or {}
    avatar = avatar or {}
    benefit_list = lines(product.get("benefits"))
    differentiators = lines(product.get("differentiators"))
    faq = lines(product.get("faq"))
    bonus = lines(product.get("bonuses"))
    testimonials = lines(product.get("testimonials"))
    source_promise = product.get("headline") or product.get("subheadline") or MISSING
    mechanism = product.get("mechanism") or MISSING
    pains = [avatar.get("problem"), avatar.get("frustrations"), avatar.get("objections")]
    pains = [x for x in pains if x and x != MISSING]
    desires = [avatar.get("desire"), avatar.get("motivations")]
    desires = [x for x in desires if x and x != MISSING]
    objections = [avatar.get("objections") or MISSING]
    opportunities = []
    risks = []
    if not benefit_list or benefit_list == [MISSING]:
        opportunities.append("Completar benefícios com informações verificáveis da página/produto.")
    if not faq or faq == [MISSING]:
        opportunities.append("Adicionar FAQ real para responder dúvidas antes de criar novas promessas.")
    if not product.get("mechanism"):
        opportunities.append("Descrever o mecanismo ou formato real do produto, se essa informação estiver disponível.")
    if not product.get("headline"):
        risks.append("Headline/promessa principal não foi identificada; confirmar manualmente antes de usar na copy.")
    if product.get("price") in (None, ""):
        risks.append("Preço não identificado; anúncios e checkout precisam exibir condições consistentes.")
    if not product.get("guarantee") or product.get("guarantee") == MISSING:
        risks.append("Garantia não identificada. Não mencionar garantia até confirmar os termos reais.")
    if not testimonials or testimonials == [MISSING]:
        risks.append("Depoimentos não identificados. Não criar prova social para preencher essa lacuna.")
    if product.get("target_audience") in (None, "", MISSING):
        risks.append("Público-alvo não identificado na página; avatar é hipótese editorial, não pesquisa.")
    angles = top_angles or []
    if angles:
        opportunities.append(f"Testar os cinco ângulos priorizados por heurística: {', '.join(a.get('category','') for a in angles[:5])}.")
    return {
        "product": {
            "name": clean(product.get("name")), "niche": clean(product.get("niche")),
            "category": clean(product.get("category")), "description": clean(product.get("description")),
            "price": product.get("promo_price") if product.get("promo_price") not in (None, "") else product.get("price"),
            "source_url": clean(product.get("sales_url")),
        },
        "avatar": {
            "name": clean(avatar.get("name")), "potential_score": avatar.get("potential_score"),
            "score_reason": clean(avatar.get("score_reason")),
        },
        "pains": pains or [MISSING], "desires": desires or [MISSING],
        "objections": objections or [MISSING], "mechanism": clean(mechanism),
        "promise": clean(source_promise), "benefits": benefit_list or [MISSING],
        "differentiators": differentiators or [MISSING],
        "bonuses": bonus or [MISSING], "guarantee": clean(product.get("guarantee")),
        "opportunities": opportunities or ["Nenhuma oportunidade adicional foi identificada automaticamente."],
        "risks": risks or ["Revise a página, preço, condições e direitos dos materiais antes de anunciar."],
        "evidence": "Dados da página/cadastro aparecem como extraídos; dores e desejos do avatar são hipóteses fictícias.",
    }


def score_offer(product, avatar, angle, offer):
    """Scores completeness and message coherence, never sales outcome probability."""
    has_promise = bool(product.get("headline") or product.get("subheadline"))
    clarity = 90 if has_promise else 62
    clarity += 5 if offer.get("headline") and offer.get("headline") != MISSING else 0
    differentiation = min(96, 58 + 10 * len(lines(product.get("differentiators"))))
    fit = min(98, max(55, int(angle.get("avatar_fit", 65))))
    click = min(98, max(55, int(angle.get("ctr_potential", 65))))
    conversion = min(98, max(55, int(angle.get("conversion_potential", 65))))
    coherence = 92 if has_promise and product.get("description") else 65
    policy_risk = 15
    score = round(.16 * clarity + .14 * differentiation + .15 * fit + .13 * click + .15 * conversion + .17 * coherence + .10 * (100 - policy_risk))
    rationale = "Heurística editorial pondera clareza (16%), diferenciação (14%), aderência ao avatar (15%), clique (13%), conversão (15%), coerência com a página (17%) e risco textual (10%). Não prevê vendas nem substitui testes."
    return {
        "score": score, "score_components": {
            "clarity": clarity, "differentiation": differentiation, "avatar_fit": fit,
            "click_potential": click, "conversion_potential": conversion,
            "page_coherence": coherence, "policy_risk": policy_risk,
        }, "score_reason": rationale,
    }
