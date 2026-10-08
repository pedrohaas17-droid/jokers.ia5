"""Internal copy review. It highlights wording risk; it never predicts approval."""
from __future__ import annotations

import re

RULES = [
    ("Garantia de resultado", "Alto", re.compile(r"\b(garantid[oa]s?|100\s*%\s*de\s*(sucesso|resultado)|resultado\s+certo|sem\s+risco|resultado\s+imediato|resultado\s+em\s+\d+\s+dias)\b", re.I), "Remova a garantia. Descreva o conteúdo, o processo e as condições reais, sem assegurar um resultado individual."),
    ("Alegação financeira", "Alto", re.compile(r"\b(ganhe|lucre|fature|renda|rendimento|lucro|dinheiro|sal[aá]rio)\b.{0,48}\b(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?|garantid[oa]|por\s+m[eê]s|em\s+\d+\s+dias)\b|\b(fique\s+rico|renda\s+garantida|lucro\s+garantido)\b", re.I), "Evite prometer renda, lucro ou retorno. Informe o que o produto ensina/oferece e deixe claro que resultados variam, se isso corresponder aos termos reais."),
    ("Atributo pessoal", "Alto", re.compile(r"\b(voc[eê]|seu|sua)\b.{0,42}\b(tem|sofre|[eé]|est[aá] com|est[aá] endivid|[eé] ansios|[eé] depress|[eé] diab[eé]tic|est[aá] acima do peso|est[aá] desempregad|tem d[ií]vida|tem ansiedade|tem depress[aã]o)\b", re.I), "Evite afirmar ou sugerir que conhece uma característica pessoal do espectador. Fale do tema de forma geral: 'Conteúdo para quem deseja conhecer…'."),
    ("Antes e depois", "Alto", re.compile(r"\b(antes\s+e\s+depois|antes/depois|resultado\s+antes\s+e\s+depois|transforma[cç][aã]o\s+em)\b", re.I), "Substitua por uma demonstração neutra do conteúdo ou do processo. Não use comparações visuais de transformação sem revisão específica."),
    ("Clickbait / urgência", "Médio", re.compile(r"\b(n[aã]o\s+vai\s+acreditar|segredo\s+que\s+ningu[eé]m|chocante|imperd[ií]vel|[uú]ltima\s+chance|s[oó]\s+hoje|vagas\s+limitadas|corra\s+antes\s+que\s+acabe|m[eé]todo\s+secreto)\b", re.I), "Use uma chamada específica e verificável. Só informe prazo ou escassez quando forem reais, documentados e aplicáveis."),
    ("Prova sem comprovação", "Médio", re.compile(r"\b(comprovad[oa]s?|cientificamente\s+provad[oa]|milhares\s+de\s+(pessoas|alunos|clientes)|n[uú]mero\s+1|o\s+melhor\s+do\s+mercado|\d{1,3}\s*%\s*(dos|de)\s*(alunos|clientes|pessoas|usu[aá]rios)|depoimento[s]?\s+de\s+sucesso)\b", re.I), "Inclua somente prova verificável, autorizada e fornecida pelo responsável pelo produto. Caso contrário, descreva os recursos sem números ou depoimentos."),
    ("Promessa exagerada", "Médio", re.compile(r"\b(sem\s+esfor[cç]o|f[aá]cil\s+demais|automaticamente\s+sem|nunca\s+mais\s+precisar|elimine\s+de\s+vez|cura\s+garantida|cura\s+definitiva|qualquer\s+pessoa\s+consegue)\b", re.I), "Prefira uma descrição proporcional do conteúdo e do esforço esperado, sem universalizar ou prometer facilidade."),
]


def suggest_safe_rewrite(text: str, product_name=""):
    review = check_copy(text)
    if not review.get("findings"):
        return {"suggestion": text, "changed": False, "note": "Nenhum padrão foi sinalizado; preserve o texto e faça revisão humana."}
    name = str(product_name or "[produto]").strip()
    suggestion = f"Conheça {name}: confira o conteúdo, o formato, o preço e as condições informadas na página. Avalie se a proposta faz sentido para seus objetivos e consulte os detalhes antes de decidir."
    return {
        "suggestion": suggestion, "changed": True,
        "note": "Sugestão por template local, não por modelo LLM. Confirme a página, a oferta e a adequação antes de usar.",
    }


def check_copy(text: str):
    original = (text or "").strip()
    if not original:
        return {
            "risk": "Sem análise", "findings": [],
            "suggested_version": "Insira uma copy para revisar.",
            "note": "O verificador é um apoio interno e não garante aprovação ou conformidade pelo Meta.",
        }
    findings = []
    for label, risk, pattern, suggestion in RULES:
        match = pattern.search(original)
        if match:
            findings.append({
                "risk": risk, "issue": label, "reason": f"Trecho identificado: ‘{match.group(0)}’. Revise o contexto e a comprovação.",
                "suggestion": suggestion,
            })
    if any(x["risk"] == "Alto" for x in findings):
        overall = "Alto"
    elif findings:
        overall = "Médio"
    else:
        overall = "Baixo"
    if findings:
        suggested = "Conheça os detalhes de [produto] e avalie se a proposta faz sentido para seus objetivos. Confira conteúdo, preço, condições e informações verificáveis antes de decidir."
    else:
        suggested = original
    return {
        "risk": overall, "findings": findings,
        "suggested_version": suggested,
        "note": "Verificador heurístico interno. Não substitui revisão humana, jurídica ou das políticas da plataforma; não há garantia de aprovação pelo Meta.",
    }
