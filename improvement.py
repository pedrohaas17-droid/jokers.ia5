"""New testing-round suggestions using the account's own recent observations."""
from __future__ import annotations

from statistics import median
from services.generator import clean


def propose_round(project):
    metrics = project.get("metrics", [])
    latest = metrics[0] if metrics else None
    product = project.get("product") or {}
    angle = next((x for x in project.get("angles", []) if x.get("id") == project.get("selected_angle_id")), None) or {}
    creative = next((x for x in project.get("creatives", []) if x.get("id") == project.get("selected_creative_id")), None) or {}
    offer = next((x for x in project.get("offers", []) if x.get("id") == project.get("selected_offer_id")), None) or {}
    other_angles = [x for x in project.get("angles", []) if x.get("id") != angle.get("id")]
    alternative_angle = other_angles[0] if other_angles else {}
    if not latest:
        reason = "Ainda não há métricas neste projeto. Esta é uma hipótese inicial para criar uma linha de base, não um diagnóstico de desempenho."
        tests = [
            {"name": "Rodada inicial — Hook", "type": "Hook", "variant_a": clean(creative.get("hook")), "variant_b": clean(alternative_angle.get("hook"), "Pergunta educativa sobre o tema, baseada na página do produto."), "hypothesis": "Comparar hook atual com uma pergunta editorial, mantendo público e oferta estáveis."},
            {"name": "Rodada inicial — Criativo", "type": "Criativo", "variant_a": clean(creative.get("name")), "variant_b": "Conceito alternativo com demonstração do conteúdo real", "hypothesis": "Comparar dois conceitos mantendo copy, público e orçamento equivalentes."},
        ]
    elif (latest.get("clicks") or 0) > 0 and (latest.get("purchases") or 0) == 0:
        reason = "Há cliques sem compras registradas neste período. Antes de aumentar investimento, confira janela de atribuição, URL, checkout, evento e coerência entre anúncio e página."
        tests = [
            {"name": "Rodada — Alinhamento da oferta", "type": "Oferta", "variant_a": clean(offer.get("headline")), "variant_b": "Versão informativa que destaca escopo, preço e condições reais da página", "hypothesis": "Verificar alinhamento de promessa, escopo, página e checkout; não assumir que a copy é a única causa."},
            {"name": "Rodada — Copy e página", "type": "Copy", "variant_a": clean(creative.get("copy_preview")), "variant_b": "Texto revisado para explicar formato e condições", "hypothesis": "Revisar correspondência entre mensagem, URL e checkout antes de alterar público/orçamento."},
        ]
    else:
        prior = [m.get("ctr") for m in metrics[1:] if m.get("ctr") is not None]
        if latest.get("ctr") is not None and len(prior) >= 2 and latest["ctr"] < median(prior):
            reason = "O CTR do último registro ficou abaixo da mediana dos registros anteriores deste projeto. É um sinal interno para testar hook/criativo, não um benchmark universal."
            tests = [
                {"name": "Nova rodada — Hook", "type": "Hook", "variant_a": clean(creative.get("hook")), "variant_b": clean(alternative_angle.get("hook"), "Hook educativo alternativo baseado no produto."), "hypothesis": "Comparar clareza/curiosidade do hook mantendo o restante constante."},
                {"name": "Nova rodada — Headline", "type": "Headline", "variant_a": clean(creative.get("headline")), "variant_b": clean(alternative_angle.get("name")), "hypothesis": "Testar uma headline alternativa a partir de ângulo já registrado; validar com volume adequado."},
            ]
        else:
            reason = "Os dados não apontam uma causa única. Continue uma comparação controlada e revise atribuição, período, margem e contexto antes de escalar."
            tests = [
                {"name": "Nova rodada — Criativo", "type": "Criativo", "variant_a": clean(creative.get("name")), "variant_b": "Variação de composição e hierarquia visual com os mesmos claims", "hypothesis": "Isolar a apresentação visual, preservando mensagem e público."},
                {"name": "Nova rodada — Ângulo", "type": "Hook", "variant_a": clean(angle.get("hook")), "variant_b": clean(alternative_angle.get("hook"), "Hook alternativo a validar"), "hypothesis": "Comparar dois ângulos existentes sem declarar vencedor prematuramente."},
            ]
    return {
        "reason": reason, "tests": tests,
        "note": "Hipóteses de teste são criadas por regras locais. Só compare resultados com períodos, orçamento, atribuição e volume adequados; não declare vencedor sem evidência suficiente.",
        "metric_snapshot": latest,
    }
