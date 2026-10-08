"""Aggregate the user's own manual metrics; never labels a winner from sparse data."""
from __future__ import annotations

from collections import defaultdict
from database import json_load

DIMENSIONS = {
    "creatives": ("creative_id", "creatives"),
    "angles": ("angle_id", "angles"),
    "offers": ("offer_id", "offers"),
    "copies": ("copy_id", "copies"),
}


def _name_for(conn, table, item_id, user_id):
    if not item_id:
        return "Sem associação"
    row = conn.execute(f"SELECT data FROM {table} WHERE id=? AND user_id=?", (item_id, user_id)).fetchone()
    if not row:
        return "Ativo removido"
    data = json_load(row["data"], {})
    return data.get("name") or data.get("label") or data.get("text", "Ativo")[:72]


def _calculate(group, name, item_id):
    spend = round(group["spend"], 2)
    impressions, clicks = group["impressions"], group["clicks"]
    purchases, revenue = group["purchases"], group["revenue"]
    return {
        "id": item_id, "name": name, "records": group["records"],
        "distinct_dates": len(group["dates"]), "spend": spend,
        "impressions": impressions, "clicks": clicks, "purchases": purchases,
        "revenue": round(revenue, 2),
        "ctr": round(clicks / impressions * 100, 2) if impressions else None,
        "cpc": round(spend / clicks, 2) if clicks else None,
        "cpm": round(spend / impressions * 1000, 2) if impressions else None,
        "cpa": round(spend / purchases, 2) if purchases else None,
        "roas": round(revenue / spend, 2) if spend else None,
    }


def _sufficiency(stat, metric):
    if metric == "ctr":
        enough = stat["distinct_dates"] >= 3 and stat["impressions"] >= 1000
        reason = "Comparação operacional: mínimo de 3 datas distintas e 1.000 impressões; ainda não demonstra causalidade nem significância estatística."
    else:
        enough = stat["distinct_dates"] >= 3 and stat["purchases"] >= 10
        reason = "Comparação operacional: mínimo de 3 datas distintas e 10 compras; ainda não demonstra causalidade nem significância estatística."
    return enough, reason


def build_report(conn, user_id):
    rows = conn.execute("SELECT project_id,data FROM metrics WHERE user_id=? ORDER BY created_at", (user_id,)).fetchall()
    grouped = {dimension: defaultdict(lambda: {"spend": 0.0, "impressions": 0, "clicks": 0, "purchases": 0, "revenue": 0.0, "records": 0, "dates": set()}) for dimension in DIMENSIONS}
    total = {"spend": 0.0, "impressions": 0, "clicks": 0, "purchases": 0, "revenue": 0.0, "records": 0}
    for row in rows:
        data = json_load(row["data"], {})
        total["records"] += 1
        for key in ("spend", "revenue"):
            total[key] += float(data.get(key) or 0)
        for key in ("impressions", "clicks", "purchases"):
            total[key] += int(data.get(key) or 0)
        for dimension, (field, table) in DIMENSIONS.items():
            item_id = data.get(field)
            if not item_id:
                continue
            group = grouped[dimension][item_id]
            group["records"] += 1
            group["spend"] += float(data.get("spend") or 0)
            group["revenue"] += float(data.get("revenue") or 0)
            group["impressions"] += int(data.get("impressions") or 0)
            group["clicks"] += int(data.get("clicks") or 0)
            group["purchases"] += int(data.get("purchases") or 0)
            if data.get("date"):
                group["dates"].add(data["date"])
    dimensions = {}
    for dimension, (field, table) in DIMENSIONS.items():
        items = []
        for item_id, group in grouped[dimension].items():
            stat = _calculate(group, _name_for(conn, table, item_id, user_id), item_id)
            ctr_enough, ctr_reason = _sufficiency(stat, "ctr")
            sales_enough, sales_reason = _sufficiency(stat, "sales")
            stat["ctr_comparison_status"] = "Amostra operacional mínima" if ctr_enough else "Dados insuficientes"
            stat["sales_comparison_status"] = "Amostra operacional mínima" if sales_enough else "Dados insuficientes"
            stat["ctr_caveat"] = ctr_reason
            stat["sales_caveat"] = sales_reason
            items.append(stat)
        dimensions[dimension] = sorted(items, key=lambda x: (x["records"], x["impressions"]), reverse=True)
    total_stat = _calculate({**total, "dates": {str(json_load(r["data"], {}).get("date")) for r in rows if json_load(r["data"], {}).get("date")}}, "Todos os registros", None)
    answers = {}
    for dim, title in (("creatives", "criativos"), ("angles", "ângulos"), ("offers", "ofertas"), ("copies", "copies")):
        candidates = dimensions[dim]
        if dim in ("creatives", "angles"):
            eligible = [item for item in candidates if item["ctr_comparison_status"] == "Amostra operacional mínima" and item["ctr"] is not None]
            key = lambda x: x["ctr"]
            metric = "CTR"
        elif dim == "offers":
            eligible = [item for item in candidates if item["sales_comparison_status"] == "Amostra operacional mínima" and item["purchases"] > 0]
            key = lambda x: x["purchases"]
            metric = "compras"
        else:
            eligible = [item for item in candidates if item["sales_comparison_status"] == "Amostra operacional mínima" and item["cpa"] is not None]
            key = lambda x: -x["cpa"]
            metric = "CPA"
        best = max(eligible, key=key) if eligible else None
        metric_value = (f"{best['ctr']:.2f}%" if metric == "CTR" else str(best["purchases"]) if metric == "compras" else f"R$ {best['cpa']:.2f}") if best else ""
        answers[f"best_{dim}"] = {
            "answer": f"Melhor {title} observado por {metric}: {best['name']} ({metric_value})." if best else f"Ainda não há amostra operacional mínima para comparar {title} por {metric}.",
            "candidate": best, "label": "Melhor observado — não é declaração de vencedor.",
        }
    retest = next((item for item in dimensions["creatives"] if item["records"] < 3 or item["impressions"] < 1000), None)
    answers["retest_creative"] = {
        "answer": f"Candidato a novo teste: {retest['name']} (ainda abaixo do critério operacional definido)." if retest else "Não há criativo com dados vinculados abaixo do critério operacional; avalie testar uma hipótese nova.",
        "candidate": retest,
        "label": "Sugestão de priorização, não vencedor nem previsão.",
    }
    return {
        "record_count": len(rows), "total": total_stat,
        "dimensions": dimensions, "answers": answers,
        "method_note": "Somente métricas inseridas manualmente e vinculadas a IDs são agregadas. CTR ponderado = cliques/impressões; CPA = gasto/compras; ROAS = receita/gasto. Nenhum ranking implica causalidade ou significância estatística.",
    }
