-- ============================================================
-- VICTOR BRAIN - RAIO-X INICIAL
-- Execute APÓS o script 01_supabase_tables.sql
-- Este é o contexto base do Victor que o bot vai usar
-- ============================================================

INSERT INTO raio_x (chave, valor) VALUES

('identidade',
'Victor Silva, 26 anos. Especialista em CRM de iGaming com sede em Lisboa, Portugal. Trajetória: 4 anos em tráfego pago → pivotou para CRM em 2024 e se especializou rapidamente. Atualmente trabalha em 3 empresas simultâneas: BetVIP (principal), NG e PWP. Aprendendo Python e automação. Mentalidade data-driven, resolve problemas com tecnologia.'),

('betvip_contexto',
'BetVIP é a empresa principal e maior prioridade. Victor atua como CRM Manager/Head em transição. Responsável por: segmentação de jogadores, automação de campanhas, comunicação multicanal (email, SMS, push notificação). Plataforma: Smartico CRM. Maior resultado já entregado: 3.002 jogadores inativos reativados em campanha de reativação, gerando R$162k de receita com investimento de R$6k (ROI 2.600%). Pico de 500-700 reativações/dia. Equipe ainda pequena, Victor ainda muito operacional — objetivo é elevar para função mais estratégica.'),

('ng_contexto',
'Segunda empresa em ordem de prioridade. Victor tem papel de suporte/consultoria em CRM. Menos horas dedicadas comparado à BetVIP. Foco: entregar o essencial sem deixar nada cair.'),

('pwp_contexto',
'Terceira empresa, menor prioridade e papel ainda indefinido. Avaliar se a relação custo-benefício (tempo vs retorno) justifica continuidade. Decisão pendente.'),

('projeto_brain',
'Victor Brain: sistema pessoal de organização mental. Arquitetura: Telegram bot → Claude API → Supabase → Dashboard estático. Status atual (Phase 1): bot criado no Telegram, Supabase configurado, Railway conectado ao GitHub. Próximo passo: deploy do bot.py e popular banco. Objetivo: capturar tudo que passa pela cabeça do Victor e transformar em itens organizados automaticamente. Futuramente migrar para Obsidian.'),

('prioridades_agora',
'1. Victor Brain: finalizar Phase 1 (deploy do bot funcionando) 2. BetVIP: manter operação CRM + evoluir para posição mais estratégica 3. Saúde: proteger rotina de academia 4. NG: entregar o mínimo viável bem feito 5. PWP: decidir continuidade 6. LinkedIn: atualizar perfil com conquistas recentes'),

('rotina_5_blocos',
'Bloco 1 (manhã cedo): saúde e academia — prioridade máxima, não negociável. Bloco 2 (manhã): BetVIP estratégia — foco profundo, sem reuniões. Bloco 3 (tarde): BetVIP operacional + reuniões + comunicação. Bloco 4 (fim da tarde): NG e PWP — tasks objetivas. Bloco 5 (noite): Victor Brain, aprendizado Python, projetos pessoais.'),

('habilidades_core',
'Segmentação avançada de players, automação de campanhas CRM, campanhas multicanal (email/SMS/push), análise de dados e KPIs, plataforma Smartico, Python (nível iniciante-intermediário em evolução), automação de processos, liderança em desenvolvimento, ramp-up rápido (60 dias para dominar CRM from scratch).'),

('resultado_key_numeros',
'3.002 jogadores inativos reativados | Receita: R$162.000 | Investimento: R$6.000 | ROI: 2.600% | Pico operacional: 500-700 reativações/dia | Tempo para dominar CRM: 60 dias.'),

('estado_atual',
'Saindo de período de burnout causado por sobrecarga em 3 empresas. Victor Brain é o sistema para recuperar clareza mental e organização. Prioridade imediata: ter o bot funcionando para parar de perder ideias e tasks no caos do dia a dia.'),

('ferramentas_que_usa',
'Smartico (CRM principal), Telegram (comunicação), Supabase (banco de dados), Railway (hosting), Claude API (AI), GitHub (código), Python (automação). HostGator já tem domínio para o dashboard futuro.'),

('pessoas_relevantes',
'Equipe BetVIP: stakeholders de produto, growth e dados (nomes a serem adicionados). NG: contatos principais (a mapear). Investidores/sócios PWP (a mapear). ATUALIZAR esta chave conforme Victor for citando pessoas nas mensagens.'),

('objetivos_carreira',
'Posição: CRM Manager → Head of CRM/Retention em empresa séria de iGaming. Perfil buscado: empresa com produto sólido, cultura data-driven, espaço para crescer estrategicamente. Não mais tráfego pago. Construir reputação como referência em CRM de iGaming.')

ON CONFLICT (chave) DO UPDATE SET
    valor = EXCLUDED.valor,
    atualizado_em = NOW();
