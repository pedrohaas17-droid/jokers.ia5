"""Five local creative briefs and ratio-aware prompt/video storyboards.

There is no image/video model configured in phase two. This service therefore
returns production-ready instructions, never fabricated rendered media.
"""
from __future__ import annotations

from auth import new_id
from services.generator import clean, lines

CONCEPTS = [
    ("Dor", "Clareza sem direcionar atributo pessoal", "Feed 4:5", "Contraste entre informação dispersa e uma sequência visual simples; não usar expressões faciais de sofrimento nem 'antes/depois'."),
    ("Curiosidade", "Pergunta educativa que abre o assunto", "Stories/Reels 9:16", "Pergunta em tipografia grande, espaço negativo e resposta visual em dois passos; sem suspense falso."),
    ("Desejo", "Mostrar uma aspiração de aprendizagem sem prometer resultado", "Feed 4:5", "Cena cotidiana neutra, materiais de estudo autorizados e texto curto; não representar transformação garantida."),
    ("Demonstração", "Mostrar apenas o conteúdo real enviado pelo usuário", "Quadrado 1:1", "Captura de tela ou página real, enquadrada com legendas; placeholder identificado se não houver material."),
    ("Oferta", "Explicar escopo, preço e condições confirmadas", "Feed 4:5", "Produto/tela real no centro, preço apenas se confirmado, benefícios verificáveis e CTA de consulta."),
]
ASPECTS = [("1080x1080", "1:1 quadrado"), ("1080x1350", "4:5 retrato de feed"), ("1080x1920", "9:16 vertical de stories/reels")]


def _video_script(duration, hook, problem, solution, offer, cta, product_name):
    timings = {15: [2, 3, 4, 3, 3], 30: [3, 6, 8, 7, 6], 45: [4, 9, 12, 10, 10]}[duration]
    beats = [
        ("Hook", hook, "Texto curto em tela; abrir sem alegação sobre o espectador.", "Tipografia cinética simples; corte direto."),
        ("Problema", problem, "B-roll genérico de organização/planejamento, sem sugerir atributo pessoal.", "Transição por corte ou match cut."),
        ("Solução", solution, "Captura ou material real do produto; se não houver, placeholder visual claramente abstrato.", "Mostrar sequência, não resultado garantido."),
        ("Oferta", offer, "Mostrar escopo, preço e condições somente quando confirmados.", "Sobreposição limpa; sem selos ou escassez não informados."),
        ("CTA", cta, f"Tela final com nome de {product_name} e link/CTA conferidos.", "Encerramento estável com área para marca autorizada."),
    ]
    scenes = []
    start = 0
    for index, (name, narration, text_screen, direction) in enumerate(beats):
        end = start + timings[index]
        scenes.append({
            "scene": index + 1, "title": name, "start_seconds": start, "end_seconds": end,
            "hook_or_beat": narration, "narration": narration,
            "on_screen_text": text_screen, "visual_direction": direction,
            "b_roll": direction, "transition": direction,
        })
        start = end
    return {"duration_seconds": duration, "scenes": scenes, "full_narration": " ".join(s["narration"] for s in scenes)}


def generate_creative_pack(project, top_angles, offers, copies):
    product = project.get("product") or {}
    product_name = clean(product.get("name"), "produto digital")
    niche = clean(product.get("niche"), "tema do produto")
    avatar = next((a for a in project.get("avatars", []) if a.get("id") == project.get("selected_avatar_id")), {})
    avatar_name = clean(avatar.get("name"), "avatar principal a definir")
    main_copy = next((c for c in copies if c.get("id") == project.get("selected_copy_id")), None)
    primary_texts = [c for c in copies if c.get("type") == "primary"]
    headlines = [c for c in copies if c.get("type") == "headline"]
    ctas = [c for c in copies if c.get("type") == "cta"]
    result = []
    for index, (concept, objective, fmt, art_direction) in enumerate(CONCEPTS):
        angle = top_angles[index % len(top_angles)] if top_angles else {}
        offer = next((o for o in offers if o.get("angle_id") == angle.get("id")), None) or (offers[0] if offers else {})
        copy = primary_texts[index % len(primary_texts)] if primary_texts else (main_copy or {})
        headline = headlines[index % len(headlines)] if headlines else {}
        cta = ctas[index % len(ctas)] if ctas else {}
        hook_text = clean(angle.get("hook"), f"Uma pergunta para explorar {niche}.")
        headline_text = clean(offer.get("headline") or headline.get("text"), f"Conheça {product_name}")
        copy_text = clean(copy.get("text"), f"Confira os detalhes de {product_name}.")
        cta_text = clean(offer.get("cta") or cta.get("text"), "Conheça os detalhes")
        benefits = lines(product.get("benefits"))
        benefit_text = benefits[0] if benefits else "benefícios não identificados na página"
        source_note = "Use somente claims e condições identificadas na página e confirmadas pelo usuário."
        image_prompts = []
        for size, ratio in ASPECTS:
            image_prompts.append({
                "size": size, "ratio": ratio,
                "prompt": (
                    f"Create a campaign layout brief for {size} ({ratio}) for {product_name}, concept {concept}. "
                    f"Hook: {hook_text} Headline: {headline_text}. Use art direction: {art_direction}. "
                    f"Keep typography inside safe margins and recompose specifically for this ratio; do not crop a different layout. "
                    f"Use authorized product assets only. Do not invent testimonials, logos, numbers, bonuses, guarantees, before/after, or outcomes. {source_note}"
                ),
            })
        offer_text = f"{headline_text}. {offer.get('subheadline') or 'Confira o escopo informado.'}"
        problem = clean(angle.get("idea"), f"Organizar dúvidas sobre {niche}.")
        solution = clean(angle.get("mechanism"), "Mecanismo não identificado na página; apresentar apenas o conteúdo confirmado.")
        cta_line = f"{cta_text}. Acesse a página e confira as condições de {product_name}."
        scripts = {duration: _video_script(duration, hook_text, problem, solution, offer_text, cta_line, product_name) for duration in (15, 30, 45)}
        result.append({
            "id": new_id(), "name": f"Criativo {index + 1:02d} — {concept}", "concept_type": concept,
            "objective": objective, "avatar_id": avatar.get("id"), "avatar_name": avatar_name,
            "angle_id": angle.get("id"), "angle_name": clean(angle.get("name"), "Ângulo a definir"),
            "offer_id": offer.get("id"), "copy_id": copy.get("id"),
            "hook": hook_text, "headline": headline_text,
            "subheadline": clean(offer.get("subheadline"), "Não identificado na página."),
            "text": copy_text, "copy_preview": copy_text,
            "visual": art_direction, "composition": art_direction,
            "elements": f"Benefício de origem: {benefit_text}. Captura real/ativo autorizado se fornecido.",
            "art_direction": art_direction, "cta": cta_text, "format": fmt,
            "recommended_format": fmt + "; gerar variações com composição própria em 1:1, 4:5 e 9:16.",
            "image_prompt": image_prompts[1]["prompt"], "image_prompts": image_prompts,
            "video_prompt": scripts[15]["full_narration"], "video_scripts": scripts,
            "image_generated": False, "video_generated": False,
            "media_status": "Briefing e prompts prontos. Nenhum modelo de geração de mídia está conectado nesta implantação.",
            "model_available": False,
            "page_evidence_note": source_note,
        })
    return result
