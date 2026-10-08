"""First-phase, deterministic generation provider.

This local provider keeps every workflow usable without exposing API keys or
making claims that the product owner did not supply. It is deliberately behind
one service interface so a hosted LLM can replace it in phase two.
"""
from __future__ import annotations

from auth import new_id

PROVIDER = "local-templates"


def clean(value, fallback="Informação não fornecida."):
    text = str(value or "").strip()
    return text if text else fallback


def lines(value):
    return [item.strip(" •\t") for item in str(value or "").splitlines() if item.strip(" •\t")]


def money(value):
    if value is None or value == "":
        return "Informação não fornecida."
    try:
        return "R$ {:,.2f}".format(float(value)).replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return clean(value)


def context(project):
    product = project.get("product") or {}
    return product, clean(product.get("name"), "este produto"), clean(product.get("niche"), "seu tema")


def generate_avatars(project):
    product, product_name, niche = context(project)
    audience = clean(product.get("target_audience"), "pessoas interessadas no tema")
    variants = [
        ("Marina", "aproximadamente 28–34", "profissional em fase de organização", "Está comparando caminhos para avançar em {niche} e procura uma opção que caiba na rotina.", "Entender os próximos passos e avaliar uma solução com clareza.", "Investir tempo ou dinheiro em algo que não combine com suas prioridades.", "Excesso de informação e dificuldade para transformar ideias em ação.", "Ainda não sabe se {product} corresponde ao que precisa.", "Pesquisou conteúdos gratuitos e comparou abordagens disponíveis.", "Prática, curiosa e cuidadosa com promessas."),
        ("Rafael", "aproximadamente 35–42", "autônomo ou pequeno empreendedor", "Quer organizar melhor iniciativas ligadas a {niche}, sem assumir que existe uma fórmula única.", "Encontrar um processo compreensível para tomar decisões melhores.", "Escolher uma alternativa sem conhecer escopo, esforço e limitações.", "Orientações fragmentadas e falta de um roteiro claro.", "Questiona o que está incluído e como o método se encaixa no contexto dele.", "Testou materiais introdutórios e soluções diferentes, sem uma comparação estruturada.", "Direta, objetiva e orientada a exemplos."),
        ("Camila", "aproximadamente 23–30", "estudante ou profissional em início de carreira", "Está explorando {niche} e precisa distinguir conceitos úteis de excesso de informação.", "Aprender com uma sequência simples e aplicar no próprio ritmo.", "Sentir que o conteúdo é avançado demais ou não explica os fundamentos.", "Dificuldade em saber por onde começar e o que priorizar.", "Quer conhecer o nível de experiência necessário antes de decidir.", "Acompanhou tutoriais, artigos e aulas abertas sobre o assunto.", "Leve, explicativa e atenta a exemplos concretos."),
        ("André", "aproximadamente 43–50", "gestor ou especialista experiente", "Busca atualizar sua abordagem em {niche} e comparar métodos com o que já conhece.", "Ganhar clareza sobre possibilidades e adaptar ideias ao cenário atual.", "Adotar uma solução genérica que ignore seu contexto e experiência.", "Pouco tempo para separar conteúdo aplicável de recomendações vagas.", "Precisa compreender o diferencial real e os limites do produto.", "Leu materiais do setor e avaliou ferramentas ou métodos alternativos.", "Analítica, concisa e receptiva a comparações honestas."),
        ("Joana", "aproximadamente 31–39", "profissional que concilia várias responsabilidades", "Tem interesse em {niche}, mas precisa avaliar compatibilidade com sua disponibilidade.", "Ter uma visão organizada do conteúdo, do ritmo e dos próximos passos.", "Começar algo que exija mais tempo ou recursos do que consegue dedicar.", "Rotina concorrida e dificuldade de manter continuidade.", "Pergunta quanto tempo demanda e o que está efetivamente incluído.", "Salvou conteúdos e iniciou estudos curtos em momentos diferentes.", "Acolhedora, simples e sem exageros."),
    ]
    result = []
    scores = [92, 87, 83, 79, 75]
    score_reasons = [
        "Maior aderência editorial ao público descrito e a objeções genéricas; hipótese, não dado de audiência.",
        "Boa conexão entre contexto do produto e critérios de decisão; valide com pesquisa.",
        "Perfil útil para explicar fundamentos sem pressupor experiência.",
        "Perfil comparativo para testar linguagem e escopo do produto.",
        "Hipótese para testar mensagem de rotina e compatibilidade; não representa dados observados.",
    ]
    for index, (name, age, occupation, situation, desire, fear, frustration, objection, previous, language) in enumerate(variants):
        result.append({
            "id": new_id(), "name": f"{name} (perfil fictício)", "age": age,
            "occupation": occupation, "situation": situation.format(niche=niche, product=product_name),
            "problem": f"Quer avançar em {niche}, mas ainda precisa organizar informações e escolher um caminho compatível com seu contexto.",
            "desire": desire.format(niche=niche, product=product_name),
            "fears": fear.format(niche=niche, product=product_name),
            "frustrations": frustration.format(niche=niche, product=product_name),
            "objections": objection.format(niche=niche, product=product_name),
            "previous_attempts": previous, "language": language,
            "motivations": f"A busca por mais clareza sobre {niche}; público de referência informado: {audience}.",
            "audience_context": audience,
            "fictional_notice": "Hipótese de trabalho criada por templates locais; valide com pesquisa real. Não representa uma pessoa real.",
            "potential_score": scores[index], "score_reason": score_reasons[index],
            "score_type": "prioridade heurística editorial 0–100; não é previsão de conversão nem resultado observado.",
        })
    return result[:5]


ANGLE_TEMPLATES = [
    ("Dor", "Nomear uma dificuldade comum ao organizar {niche}, sem atribuí-la pessoalmente ao leitor.", "Quando {challenge} parece confuso, um próximo passo claro ajuda a organizar a decisão.", "Conheça uma forma estruturada de explorar {product}.", "Partir do problema descrito no briefing e apresentar o escopo real do produto."),
    ("Desejo", "Apresentar o objetivo de ganhar clareza e autonomia em {niche}, sem prometer um resultado garantido.", "Mais clareza para decidir seus próximos passos em {niche}.", "Avalie recursos que podem apoiar seu processo de aprendizagem.", "Conectar os benefícios cadastrados pelo usuário a uma necessidade declarada."),
    ("Curiosidade", "Abrir uma pergunta educativa sobre escolhas e fundamentos de {niche}.", "O que vale observar antes de escolher um caminho em {niche}?", "Entenda os pontos que compõem {product}.", "Responder à pergunta com informações verificáveis sobre o conteúdo, sem suspense artificial."),
    ("Erro", "Explicar um equívoco comum de processo sem culpar ou constranger o público.", "Um ponto que costuma passar despercebido ao planejar {niche}.", "Veja como {product} aborda o tema, conforme o material cadastrado.", "Usar uma explicação educativa e revisar qualquer afirmação antes de publicar."),
    ("Oportunidade", "Mostrar uma possibilidade de aprender ou organizar o trabalho em {niche}, condicionada à adequação individual.", "Uma possibilidade para incluir {niche} no seu plano de aprendizagem.", "Conheça o formato e decida se faz sentido para seu momento.", "Descrever o que está incluído, sem alegar escassez ou retorno financeiro."),
    ("Demonstração", "Apresentar um trecho, módulo ou recurso real do produto; inserir o material visual fornecido pelo usuário.", "Veja um exemplo do conteúdo de {product}.", "Avalie a abordagem antes de decidir.", "Demonstração real e autorizada. Nenhum módulo ou recurso foi inventado."),
    ("História", "Contar uma situação hipotética de organização de ideias, identificada como exemplo ilustrativo.", "Um exemplo ilustrativo: transformar dúvidas de {niche} em perguntas mais claras.", "Conheça uma estrutura que pode ajudar a organizar esse estudo.", "Narrativa fictícia identificada como exemplo; não usar como depoimento."),
    ("Comparação", "Comparar critérios de avaliação entre abordagens sem declarar superioridade não comprovada.", "Compare formato, escopo e suporte antes de escolher um material de {niche}.", "Confira os detalhes de {product} e compare com suas necessidades.", "Comparação baseada apenas em critérios verificáveis e dados fornecidos."),
    ("Objeção", "Responder dúvidas sobre escopo, formato ou adequação usando apenas informações cadastradas.", "Antes de decidir, veja o que está — e o que não está — incluído.", "Confira as informações disponíveis de {product}.", "Ser transparente sobre lacunas; não inventar suporte, acesso ou resultados."),
    ("Prova", "Reservar espaço para uma evidência real, verificável e autorizada fornecida pelo usuário.", "Que evidência ajuda a avaliar uma solução de {niche}?", "Consulte os detalhes e inclua apenas provas documentadas.", "Nenhuma prova foi fornecida. Não inserir depoimentos, números ou resultados inventados."),
    ("Facilidade", "Explicar a experiência e os passos de uso somente quando descritos pelo responsável pelo produto.", "Entenda o formato de {product} antes de começar.", "Veja se a estrutura combina com sua rotina.", "Não presumir rapidez, facilidade ou ausência de esforço sem informação de origem."),
    ("Método", "Apresentar a sequência ou método do produto usando o escopo confirmado pelo proprietário.", "Uma visão organizada para estudar {niche}, passo a passo.", "Explore a proposta de {product}.", "Substituir qualquer detalhe do método pela descrição real cadastrada."),
    ("Checklist", "Organizar critérios públicos que ajudam a avaliar uma solução de {niche}.", "Checklist: o que conferir antes de escolher um material de {niche}?", "Compare o checklist com o conteúdo real de {product}.", "Cada item deve ser verificável na página; não acrescentar recursos presumidos."),
    ("Bastidores", "Mostrar uma demonstração transparente do processo ou material, sem alegar resultado.", "Uma visão por dentro da proposta de {product}.", "Veja o formato e o escopo antes de decidir.", "Usar apenas tela ou material real fornecido; sem simular produto inexistente."),
    ("Adequação", "Explicar para quem o conteúdo pode ou não fazer sentido com base nos pré-requisitos informados.", "Antes de começar, confira se {product} combina com seus objetivos e disponibilidade.", "Leia o escopo, requisitos e limitações cadastrados.", "Não afirmar adequação universal; declarar requisitos apenas quando fornecidos."),
]


def generate_angles(project):
    product, product_name, niche = context(project)
    avatar = next((a for a in project.get("avatars", []) if a.get("id") == project.get("selected_avatar_id")), None)
    avatar = avatar or (project.get("avatars") or [None])[0]
    avatar_id = avatar.get("id") if avatar else None
    challenge = "organizar as informações e definir prioridades"
    if avatar:
        challenge = str(avatar.get("problem") or challenge).rstrip(".")
    prior_count = len(project.get("angles", []))
    version = prior_count // len(ANGLE_TEMPLATES) + 1
    prefixes = ["", "Uma pergunta para considerar: ", "Um ponto de partida: ", "Veja este critério: "]
    ctr_scores = [84, 79, 93, 73, 77, 71, 82, 75, 78, 68, 74, 80, 76, 78, 72]
    conversion_scores = [78, 86, 72, 75, 78, 80, 71, 82, 88, 67, 73, 85, 76, 73, 84]
    curiosity_scores = [71, 76, 96, 70, 74, 72, 85, 74, 78, 75, 66, 73, 79, 84, 70]
    result = []
    for index, (category, idea, hook, promise, mechanism) in enumerate(ANGLE_TEMPLATES):
        variation = prefixes[(version - 1) % len(prefixes)] if version > 1 else ""
        ctr = max(40, min(98, ctr_scores[index] + ((version - 1) % 5 - 2)))
        conversion = max(40, min(98, conversion_scores[index] + ((version - 1) % 3 - 1)))
        curiosity = max(40, min(98, curiosity_scores[index] + ((version - 1) % 7 - 3)))
        avatar_fit = min(96, max(55, 72 + (index * 9 % 23) + (8 if avatar else 0)))
        combined = round(0.25 * ctr + 0.30 * conversion + 0.20 * curiosity + 0.25 * avatar_fit)
        hook_text = hook.format(niche=niche, product=product_name, challenge=challenge)
        result.append({
            "id": new_id(), "name": f"{category}: {product_name}" + (f" · variação {version}" if version > 1 else ""), "category": category,
            "idea": idea.format(niche=niche, product=product_name),
            "hook": variation + hook_text,
            "promise": promise.format(niche=niche, product=product_name),
            "mechanism": mechanism.format(niche=niche, product=product_name),
            "cta": "Conheça os detalhes" if category in ("Prova", "Objeção", "Comparação") else "Ver a proposta",
            "avatar_id": avatar_id, "avatar_name": avatar.get("name") if avatar else "Avatar a selecionar",
            "potential_score": round(combined / 10, 1), "score": combined,
            "ctr_potential": ctr, "conversion_potential": conversion,
            "curiosity_score": curiosity, "avatar_fit": avatar_fit,
            "top5_recommended": False,
            "score_reason": "Prioridade editorial calculada por heurística: CTR potencial 25%, conversão potencial 30%, curiosidade 20% e adequação ao avatar 25%. Não é previsão nem desempenho observado.",
            "potential_basis": "Pontuação heurística 0–100; validação por teste real é necessária.",
        })
    ranked = sorted(result, key=lambda item: (item.get("score", 0), item.get("avatar_fit", 0)), reverse=True)
    top_ids = {item["id"] for item in ranked[:5]}
    for item in result:
        item["top5_recommended"] = item["id"] in top_ids
    return result


def generate_offers(project):
    """Return five editable offer briefs, linked to five saved angles when available."""
    from services.strategy import score_offer
    product, product_name, niche = context(project)
    avatar = next((a for a in project.get("avatars", []) if a.get("id") == project.get("selected_avatar_id")), None) or {}
    angles = project.get("angles") or []
    ranked_angles = sorted(angles, key=lambda item: (item.get("score", 0), item.get("avatar_fit", 0)), reverse=True)
    if len(ranked_angles) >= 5:
        result = generate_campaign_offers(project, ranked_angles[:5])
    else:
        selected = next((a for a in angles if a.get("id") == project.get("selected_angle_id")), None)
        angle_pool = ranked_angles or ([selected] if selected else [{}])
        benefits = lines(product.get("benefits")) or ["Não identificado na página."]
        price_value = product.get("promo_price") if product.get("promo_price") not in (None, "") else product.get("price")
        price_label = money(price_value)
        frames = [
            ("Informativa", "Conheça o conteúdo, o formato e os detalhes disponíveis."),
            ("Benefícios cadastrados", "Confira os benefícios informados pelo responsável pelo produto."),
            ("Escopo", "Veja o escopo, os requisitos e as condições cadastradas."),
            ("Preço e condições", "Consulte o preço e as condições apresentadas na página."),
            ("Acesso à página", "Acesse a página para revisar as informações antes de decidir."),
        ]
        result = []
        for index, (frame, subheadline) in enumerate(frames):
            angle = angle_pool[index % len(angle_pool)]
            headline = clean(product.get("headline"), clean(angle.get("hook"), f"Conheça {product_name}"))
            offer = {
                "id": new_id(), "name": f"Oferta {index + 1} — {frame}", "headline": headline,
                "headline_source": "Headline confirmada pelo usuário." if product.get("headline") else "Headline editorial; revise antes de publicar.",
                "subheadline": subheadline, "promise": subheadline, "benefits": benefits,
                "bonuses": clean(product.get("bonuses")), "guarantee": clean(product.get("guarantee")),
                "mechanism": clean(product.get("mechanism")), "differentiators": lines(product.get("differentiators")) or ["Não identificado na página."],
                "price": price_label, "price_value": price_value, "cta": clean(product.get("cta")),
                "avatar_id": avatar.get("id"), "avatar_name": avatar.get("name", "Avatar a selecionar"),
                "angle_id": angle.get("id"), "angle_name": angle.get("name", "Ângulo a selecionar"),
                "angle_category": angle.get("category"), "angle_hook": angle.get("hook"),
                "positioning_context": angle.get("idea", "Ângulo ainda não selecionado."),
                "source_note": "Bônus, garantia, preço e promessa vêm dos dados confirmados. Campos ausentes não são inventados.",
                "disclaimer": "Variação editorial, não é previsão de conversão. Confira as condições reais antes de publicar.",
            }
            offer.update(score_offer(product, avatar, angle, offer))
            result.append(offer)
    ranked = sorted(result, key=lambda item: item.get("score", 0), reverse=True)
    best_id = ranked[0].get("id") if ranked else None
    for item in result:
        item["recommended"] = item.get("id") == best_id
        if item["recommended"]:
            item["recommendation_reason"] = "Maior score editorial entre as opções geradas, baseado em completude e coerência. Não é previsão de vendas nem evidência de desempenho."
    return result


VARIATIONS = ["Direta", "Curiosidade", "Dor", "Desejo", "Educacional", "Storytelling"]


def generate_copies(project):
    product, product_name, niche = context(project)
    avatar = next((a for a in project.get("avatars", []) if a.get("id") == project.get("selected_avatar_id")), None) or {}
    angle = next((a for a in project.get("angles", []) if a.get("id") == project.get("selected_angle_id")), None) or {}
    offer = next((o for o in project.get("offers", []) if o.get("id") == project.get("selected_offer_id")), None) or {}
    audience_context = clean(avatar.get("audience_context") or product.get("target_audience"), "pessoas interessadas no tema")
    problem = clean(avatar.get("problem"), f"organizar ideias e prioridades em {niche}")
    angle_idea = clean(angle.get("idea"), f"Explorar {niche} com informações claras")
    hook = clean(angle.get("hook"), f"Conheça uma forma de explorar {niche}.")
    promise = clean(angle.get("promise"), f"Conheça os detalhes de {product_name}.")
    offer_headline = clean(offer.get("headline"), f"Conheça {product_name}")
    benefit_list = lines(product.get("benefits"))
    benefit = benefit_list[0] if benefit_list else "os benefícios descritos na página do produto"
    formats = [
        ("Direta", f"Conheça {product_name}: uma proposta sobre {niche}. Veja o conteúdo, o formato e os detalhes antes de decidir se combina com seus objetivos."),
        ("Curiosidade", f"{hook} Em {product_name}, a proposta é: {angle_idea} Confira as informações disponíveis e avalie com calma."),
        ("Dor", f"Quando {problem.rstrip('.').lower()}, organizar as opções pode ajudar. {product_name} apresenta uma proposta sobre {niche}; conheça o escopo antes de decidir."),
        ("Desejo", f"Se você busca mais clareza em {niche}, conheça {product_name}. A proposta é explorar {benefit.lower()}; confira os detalhes para avaliar a adequação ao seu momento."),
        ("Educacional", f"Um bom primeiro passo é entender o que está incluído. {product_name} aborda {niche}; veja o conteúdo, os recursos informados e os limites da proposta."),
        ("Storytelling", f"Exemplo ilustrativo: ao organizar dúvidas sobre {niche}, fica mais fácil comparar caminhos. {product_name} pode ser avaliado pelos detalhes e benefícios informados — sem prometer um resultado específico."),
    ]
    result = []
    for index in range(5):
        style, text = formats[index]
        result.append({"id": new_id(), "type": "primary", "label": f"Primary Text {index + 1}", "variation": style, "text": text, "avatar_id": avatar.get("id"), "angle_id": angle.get("id"), "offer_id": offer.get("id"), "audience_context": audience_context})
    headline_bases = [
        ("Direta", f"Conheça {product_name}"),
        ("Curiosidade", f"O que observar em {niche}?"),
        ("Dor", f"Organize suas ideias sobre {niche}"),
        ("Desejo", f"Mais clareza para explorar {niche}"),
        ("Educacional", f"Entenda a proposta de {product_name}"),
        ("Storytelling", f"Uma nova perspectiva sobre {niche}"),
        ("Direta", offer_headline),
        ("Curiosidade", f"Veja o que está incluído em {product_name}"),
        ("Educacional", f"Conteúdo e detalhes em um só lugar"),
        ("Desejo", f"Descubra se {product_name} combina com você"),
    ]
    for i, (style, text) in enumerate(headline_bases):
        result.append({"id": new_id(), "type": "headline", "label": f"Headline {i + 1}", "variation": style, "text": text, "avatar_id": avatar.get("id"), "angle_id": angle.get("id"), "offer_id": offer.get("id")})
    descriptions = [
        f"Confira o conteúdo de {product_name}.",
        f"Veja os detalhes da proposta sobre {niche}.",
        "Consulte formato, preço e condições informadas.",
        f"Avalie se {product_name} faz sentido para seus objetivos.",
        "Informações claras para uma decisão consciente.",
    ]
    for i, text in enumerate(descriptions):
        result.append({"id": new_id(), "type": "description", "label": f"Description {i + 1}", "variation": VARIATIONS[i % len(VARIATIONS)], "text": text, "avatar_id": avatar.get("id"), "angle_id": angle.get("id"), "offer_id": offer.get("id")})
    ctas = ["Conheça os detalhes", "Ver a proposta", "Acessar a página", "Explorar o conteúdo", "Saiba mais"]
    for i, text in enumerate(ctas):
        result.append({"id": new_id(), "type": "cta", "label": f"CTA {i + 1}", "variation": VARIATIONS[i % len(VARIATIONS)], "text": text, "avatar_id": avatar.get("id"), "angle_id": angle.get("id"), "offer_id": offer.get("id")})
    return result


def generate_creatives(project):
    """Create five concept briefs via the phase-two provider; no media is rendered."""
    from services.creative_pack import generate_creative_pack
    angles = sorted(project.get("angles", []), key=lambda item: item.get("score", 0), reverse=True)[:5]
    return generate_creative_pack(project, angles, project.get("offers", []), project.get("copies", []))


def generate_campaign_offers(project, top_angles):
    """Create one fact-safe offer brief for each of the selected top angles."""
    from services.strategy import score_offer
    product, product_name, niche = context(project)
    avatar = next((a for a in project.get("avatars", []) if a.get("id") == project.get("selected_avatar_id")), None) or {}
    benefit_list = lines(product.get("benefits")) or ["Não identificado na página."]
    price_value = product.get("promo_price") if product.get("promo_price") not in (None, "") else product.get("price")
    price_label = money(price_value)
    result = []
    for index, angle in enumerate(top_angles[:5]):
        page_headline = clean(product.get("headline"), "")
        generated_headline = page_headline or clean(angle.get("hook"), f"Conheça {product_name}")
        offer = {
            "id": new_id(), "name": f"Oferta {index + 1} — {angle.get('category', 'Ângulo')}",
            "headline": generated_headline,
            "headline_source": "Headline extraída e confirmada pelo usuário." if page_headline else "Headline editorial sugerida a partir do ângulo; revise antes de publicar.",
            "subheadline": clean(product.get("subheadline")),
            "promise": page_headline or clean(product.get("subheadline")),
            "benefits": benefit_list, "mechanism": clean(product.get("mechanism")),
            "bonuses": clean(product.get("bonuses")), "guarantee": clean(product.get("guarantee")),
            "price": price_label, "price_value": price_value,
            "cta": clean(product.get("cta")),
            "angle_id": angle.get("id"), "angle_name": angle.get("name"),
            "angle_category": angle.get("category"), "angle_hook": angle.get("hook"),
            "avatar_id": avatar.get("id"), "avatar_name": avatar.get("name", "Avatar recomendado"),
            "differentiators": lines(product.get("differentiators")) or ["Não identificado na página."],
            "score_reason": "Score editorial, não uma previsão de conversão. "
                "A recomendação combina clareza, diferenciação, adequação ao avatar, clique, conversão, coerência com os dados da página e risco textual.",
            "source_note": "Bônus, garantia, preço e promessa vêm do cadastro confirmado. Campos ausentes permanecem como não identificados.",
        }
        offer.update(score_offer(product, avatar, angle, offer))
        result.append(offer)
    return result


def generate(stage, project):
    functions = {
        "avatars": generate_avatars,
        "angles": generate_angles,
        "offers": generate_offers,
        "copies": generate_copies,
        "creatives": generate_creatives,
    }
    if stage not in functions:
        raise ValueError("Etapa de geração desconhecida.")
    return functions[stage](project)
