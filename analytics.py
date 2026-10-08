"""Metric calculations and transparent, rules-based campaign diagnosis."""
from __future__ import annotations


def number(value, field):
    if value in (None, ""):
        return 0.0
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{field}' deve ser um número válido.")
    if n < 0:
        raise ValueError(f"'{field}' não pode ser negativo.")
    return n


def whole_count(value, field):
    result = number(value, field)
    if not result.is_integer():
        raise ValueError(f"'{field}' deve ser um número inteiro.")
    return int(result)


def calculate_metrics(data):
    spend = number(data.get("spend"), "Valor gasto")
    impressions = whole_count(data.get("impressions"), "Impressões")
    reach = whole_count(data.get("reach"), "Alcance")
    clicks = whole_count(data.get("clicks"), "Cliques")
    purchases = whole_count(data.get("purchases"), "Compras")
    revenue = number(data.get("revenue"), "Receita")
    return {
        "date": str(data.get("date") or "").strip(),
        "spend": round(spend, 2), "impressions": int(impressions), "reach": int(reach),
        "clicks": int(clicks), "purchases": int(purchases), "revenue": round(revenue, 2),
        "ctr": round(clicks / impressions * 100, 2) if impressions else None,
        "cpc": round(spend / clicks, 2) if clicks else None,
        "cpm": round(spend / impressions * 1000, 2) if impressions else None,
        "cpa": round(spend / purchases, 2) if purchases else None,
        "roas": round(revenue / spend, 2) if spend else None,
    }


def diagnose_metrics(metric):
    if not metric:
        return {
            "status": "Aguardando dados",
            "summary": "Adicione métricas para receber um diagnóstico baseado nos números informados.",
            "observations": ["O diagnóstico é heurístico e não substitui a análise do contexto, atribuição ou políticas da plataforma."],
        }
    observations = []
    spend = metric.get("spend") or 0
    impressions = metric.get("impressions") or 0
    clicks = metric.get("clicks") or 0
    purchases = metric.get("purchases") or 0
    revenue = metric.get("revenue") or 0
    ctr = metric.get("ctr")
    cpc = metric.get("cpc")
    cpa = metric.get("cpa")
    roas = metric.get("roas")
    if not impressions:
        observations.append("Sem impressões registradas: confirme período, entrega e preenchimento dos dados.")
    if impressions and not clicks:
        observations.append("Há impressões, mas nenhum clique registrado. Revise a relevância do criativo e a leitura da oferta; compare com seu histórico, não com um benchmark universal.")
    elif ctr is not None:
        observations.append(f"CTR calculado: {ctr:.2f}%. Compare com campanhas equivalentes da sua conta e janela de atribuição.")
    if clicks and not purchases:
        observations.append("Há cliques e nenhuma compra registrada. Confira checkout, rastreamento, período de atribuição e coerência da página.")
    if purchases:
        observations.append(f"CPA calculado: R$ {cpa:.2f}. Avalie em relação à margem e ao limite econômico definido para o produto.")
    if spend and revenue:
        observations.append(f"ROAS calculado: {roas:.2f}x. Compare com ponto de equilíbrio, impostos, taxas e reembolsos antes de concluir rentabilidade.")
    elif spend and not revenue:
        observations.append("Sem receita informada: o ROAS não pode ser calculado. Confirme atribuição e registro de receita.")
    if not observations:
        observations.append("Dados registrados. Considere comparar criativo, público e janela de atribuição antes de alterar o orçamento.")
    if purchases and revenue and roas is not None and roas >= 1:
        status = "Dados positivos — validar margem"
    elif impressions and not clicks:
        status = "Revisar atração do anúncio"
    elif clicks and not purchases:
        status = "Revisar conversão da página"
    else:
        status = "Monitorar e comparar"
    return {
        "status": status,
        "summary": "Leitura automática baseada exclusivamente nas métricas manuais desta conta; não é recomendação financeira nem garantia de desempenho.",
        "observations": observations,
    }
