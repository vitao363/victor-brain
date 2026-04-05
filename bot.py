import os
import json
import logging
from datetime import datetime, date
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# ENV
# ─────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ['TELEGRAM_TOKEN']
ANTHROPIC_KEY   = os.environ['ANTHROPIC_API_KEY']
SUPABASE_URL    = os.environ['SUPABASE_URL']
SUPABASE_KEY    = os.environ['SUPABASE_KEY']
ALLOWED_CHAT_ID = int(os.environ.get('ALLOWED_CHAT_ID', '0'))
CLAUDE_MODEL    = os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-5')

claude   = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─────────────────────────────────────────────
# MEMÓRIA DE CONVERSA
# ─────────────────────────────────────────────
conversation_history: dict[int, list] = {}
MAX_HISTORY = 20  # mais contexto = respostas mais inteligentes


def get_history(chat_id: int) -> list:
    return conversation_history.get(chat_id, [])


def add_to_history(chat_id: int, role: str, content: str):
    if chat_id not in conversation_history:
        conversation_history[chat_id] = []
    conversation_history[chat_id].append({"role": role, "content": content})
    conversation_history[chat_id] = conversation_history[chat_id][-MAX_HISTORY:]


def is_allowed(update: Update) -> bool:
    return not ALLOWED_CHAT_ID or update.effective_chat.id == ALLOWED_CHAT_ID


# ─────────────────────────────────────────────
# RAIO-X (carregado no system prompt)
# ─────────────────────────────────────────────

def get_raio_x() -> str:
    try:
        rows = supabase.table('raio_x').select('chave, valor').execute().data
        return "\n".join(f"[{r['chave']}]: {r['valor']}" for r in rows)
    except Exception as e:
        logger.error(f"Erro raio-x: {e}")
        return "(erro ao carregar raio-x)"


# ─────────────────────────────────────────────
# TOOLS — 4 ferramentas genéricas
# ─────────────────────────────────────────────

TOOLS = [
    {
        "name": "consultar",
        "description": """Consulta dados do banco de dados do Victor. Use SEMPRE que precisar de informações reais — nunca invente.

Tabelas disponíveis:
- items: tarefas, ideias, insights, prioridades
  Campos: id, tipo, texto, categoria, empresa, prioridade (1-5), status (pendente/concluido/cancelado), tags, pessoas, prazo, criado_em, atualizado_em, concluido_em, fonte, msg_original
- raio_x: contexto pessoal/profissional (chave, valor, atualizado_em)
- categorias: categorias do sistema (slug, nome, cor, icone)
- log_mensagens: histórico de processamento (telegram_msg_id, conteudo_raw, items_gerados, criado_em)

Dicas:
- Use contar=true para saber quantos registros existem
- Use busca_texto para buscar por palavras no texto dos items
- Use or_filtro para condições OR no formato Supabase: 'campo1.eq.valor1,campo2.eq.valor2'
- Combine múltiplos filtros para queries complexas""",
        "input_schema": {
            "type": "object",
            "properties": {
                "tabela": {
                    "type": "string",
                    "enum": ["items", "raio_x", "categorias", "log_mensagens"],
                    "description": "Tabela a consultar"
                },
                "select": {
                    "type": "string",
                    "description": "Campos a retornar separados por vírgula. Default: '*'"
                },
                "filtros": {
                    "type": "array",
                    "description": "Filtros AND. Cada filtro: {campo, operador, valor}",
                    "items": {
                        "type": "object",
                        "properties": {
                            "campo": {"type": "string"},
                            "operador": {
                                "type": "string",
                                "enum": ["eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike", "in", "is"]
                            },
                            "valor": {"description": "Valor para comparação (tipo varia)"}
                        },
                        "required": ["campo", "operador", "valor"]
                    }
                },
                "or_filtro": {
                    "type": "string",
                    "description": "Filtro OR no formato Supabase. Ex: 'prazo.eq.2024-01-01,prioridade.eq.1'"
                },
                "busca_texto": {
                    "type": "string",
                    "description": "Busca por texto (ILIKE %termo%). Funciona na tabela items."
                },
                "ordem": {
                    "type": "string",
                    "description": "Campo para ordenar. Prefixe '-' para DESC. Ex: '-criado_em', 'prioridade'"
                },
                "limite": {
                    "type": "integer",
                    "description": "Máximo de registros retornados (default 50)"
                },
                "contar": {
                    "type": "boolean",
                    "description": "Se true, retorna apenas a contagem total sem dados"
                }
            },
            "required": ["tabela"]
        }
    },
    {
        "name": "modificar",
        "description": """Atualiza registros existentes no banco. Use para:
- Marcar tasks como concluídas (status='concluido', concluido_em=datetime atual)
- Cancelar tasks (status='cancelado')
- Mudar prioridade, empresa, categoria, texto, prazo
- Atualizar chaves do raio-x
- Editar categorias

IMPORTANTE: Para concluir items, sempre inclua concluido_em com a data/hora atual nos dados.
Filtros são obrigatórios para segurança (não permite update sem WHERE).""",
        "input_schema": {
            "type": "object",
            "properties": {
                "tabela": {
                    "type": "string",
                    "enum": ["items", "raio_x", "categorias"],
                    "description": "Tabela a modificar"
                },
                "filtros": {
                    "type": "array",
                    "description": "Condições para selecionar registros a atualizar",
                    "items": {
                        "type": "object",
                        "properties": {
                            "campo": {"type": "string"},
                            "operador": {
                                "type": "string",
                                "enum": ["eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike", "in"]
                            },
                            "valor": {}
                        },
                        "required": ["campo", "operador", "valor"]
                    }
                },
                "dados": {
                    "type": "object",
                    "description": "Campos e valores a atualizar. Ex: {'status': 'concluido', 'concluido_em': '2024-01-01T12:00:00'}"
                }
            },
            "required": ["tabela", "filtros", "dados"]
        }
    },
    {
        "name": "inserir",
        "description": """Insere novo registro no banco. Use para:
- Salvar novas tarefas, ideias, insights, perguntas, reuniões
- Atualizar/criar chaves no raio-x (faz upsert automático na chave)
- Criar novas categorias

Para items — classifique automaticamente com base no contexto do Victor:
  tipo: task|idea|insight|question|priority|meeting|financial|health|personal
  empresa: betvip|ng|pwp|pessoal|todos
  categoria: crm|growth|produto|lideranca|saude|financeiro|pessoal|projeto
  prioridade: 1 (crítico) a 5 (baixa)
  status: default 'pendente'
  tags: array de strings
  prazo: 'YYYY-MM-DD' ou null""",
        "input_schema": {
            "type": "object",
            "properties": {
                "tabela": {
                    "type": "string",
                    "enum": ["items", "raio_x", "categorias"],
                    "description": "Tabela onde inserir"
                },
                "dados": {
                    "type": "object",
                    "description": "Dados do novo registro"
                }
            },
            "required": ["tabela", "dados"]
        }
    },
    {
        "name": "deletar",
        "description": """Remove registros permanentemente do banco. Use com CUIDADO.
Prefira cancelar items (status='cancelado') em vez de deletar.
Delete apenas quando Victor pedir explicitamente para remover algo.
Filtros são obrigatórios (proteção contra delete sem WHERE).""",
        "input_schema": {
            "type": "object",
            "properties": {
                "tabela": {
                    "type": "string",
                    "enum": ["items", "raio_x", "categorias"],
                    "description": "Tabela de onde deletar"
                },
                "filtros": {
                    "type": "array",
                    "description": "Condições para selecionar registros a deletar",
                    "items": {
                        "type": "object",
                        "properties": {
                            "campo": {"type": "string"},
                            "operador": {
                                "type": "string",
                                "enum": ["eq", "neq", "like", "ilike"]
                            },
                            "valor": {}
                        },
                        "required": ["campo", "operador", "valor"]
                    }
                }
            },
            "required": ["tabela", "filtros"]
        }
    }
]


# ─────────────────────────────────────────────
# EXECUÇÃO DE TOOLS
# ─────────────────────────────────────────────

def apply_filters(query, filtros):
    if not filtros:
        return query
    for f in filtros:
        campo = f['campo']
        op    = f['operador']
        valor = f['valor']
        if   op == 'eq':    query = query.eq(campo, valor)
        elif op == 'neq':   query = query.neq(campo, valor)
        elif op == 'gt':    query = query.gt(campo, valor)
        elif op == 'gte':   query = query.gte(campo, valor)
        elif op == 'lt':    query = query.lt(campo, valor)
        elif op == 'lte':   query = query.lte(campo, valor)
        elif op == 'like':  query = query.like(campo, valor)
        elif op == 'ilike': query = query.ilike(campo, valor)
        elif op == 'in':    query = query.in_(campo, valor)
        elif op == 'is':    query = query.is_(campo, valor)
    return query


def exec_consultar(params: dict) -> dict:
    tabela  = params['tabela']
    select  = params.get('select', '*')
    filtros = params.get('filtros', [])
    or_filt = params.get('or_filtro', '')
    busca   = params.get('busca_texto', '')
    ordem   = params.get('ordem', '')
    limite  = params.get('limite', 50)
    contar  = params.get('contar', False)

    try:
        if contar:
            query = supabase.table(tabela).select('id', count='exact')
        else:
            query = supabase.table(tabela).select(select)

        query = apply_filters(query, filtros)

        if or_filt:
            query = query.or_(or_filt)

        if busca and tabela == 'items':
            query = query.ilike('texto', f'%{busca}%')

        if ordem:
            if ordem.startswith('-'):
                query = query.order(ordem[1:], desc=True)
            else:
                query = query.order(ordem)

        if contar:
            result = query.limit(1).execute()
            return {"contagem": result.count if result.count is not None else 0, "status": "ok"}
        else:
            result = query.limit(min(limite, 100)).execute()
            dados = result.data or []
            # Truncar se muito grande para não estourar contexto
            if len(json.dumps(dados, ensure_ascii=False, default=str)) > 6000:
                dados = dados[:20]
                return {"dados": dados, "total_retornado": len(dados), "truncado": True, "status": "ok"}
            return {"dados": dados, "total_retornado": len(dados), "status": "ok"}

    except Exception as e:
        logger.error(f"Erro consultar: {e}")
        return {"erro": str(e), "status": "erro"}


def exec_modificar(params: dict) -> dict:
    tabela  = params['tabela']
    filtros = params.get('filtros', [])
    dados   = params.get('dados', {})

    if not filtros:
        return {"erro": "Filtros obrigatórios para segurança", "status": "erro"}

    try:
        query = supabase.table(tabela).update(dados)
        query = apply_filters(query, filtros)
        result = query.execute()
        count = len(result.data) if result.data else 0
        return {"modificados": count, "status": "ok"}
    except Exception as e:
        logger.error(f"Erro modificar: {e}")
        return {"erro": str(e), "status": "erro"}


def exec_inserir(params: dict) -> dict:
    tabela = params['tabela']
    dados  = params.get('dados', {})

    try:
        if tabela == 'raio_x':
            result = supabase.table(tabela).upsert(dados, on_conflict='chave').execute()
        else:
            # Defaults para items
            if tabela == 'items':
                dados.setdefault('status', 'pendente')
                dados.setdefault('fonte', 'telegram')
            result = supabase.table(tabela).insert(dados).execute()

        inserted = result.data[0] if result.data else {}
        return {"inserido": inserted, "status": "ok"}
    except Exception as e:
        logger.error(f"Erro inserir: {e}")
        return {"erro": str(e), "status": "erro"}


def exec_deletar(params: dict) -> dict:
    tabela  = params['tabela']
    filtros = params.get('filtros', [])

    if not filtros:
        return {"erro": "Filtros obrigatórios para segurança", "status": "erro"}

    try:
        query = supabase.table(tabela).delete()
        query = apply_filters(query, filtros)
        result = query.execute()
        count = len(result.data) if result.data else 0
        return {"deletados": count, "status": "ok"}
    except Exception as e:
        logger.error(f"Erro deletar: {e}")
        return {"erro": str(e), "status": "erro"}


def execute_tool(name: str, params: dict) -> dict:
    logger.info(f"🔧 Tool: {name} | {json.dumps(params, ensure_ascii=False, default=str)[:300]}")

    executors = {
        "consultar": exec_consultar,
        "modificar": exec_modificar,
        "inserir":   exec_inserir,
        "deletar":   exec_deletar,
    }

    executor = executors.get(name)
    if not executor:
        return {"erro": f"Tool desconhecida: {name}", "status": "erro"}

    result = executor(params)
    logger.info(f"📦 Resultado: {json.dumps(result, ensure_ascii=False, default=str)[:300]}")
    return result


# ─────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """Você é o assistente pessoal do Victor Silva. Uma secretária inteligente, obcecada por organização, que conhece tudo sobre ele.

Você TEM ACESSO REAL ao banco de dados via tools. Use-as para qualquer operação com dados — NUNCA invente números ou informações.

RAIO-X DO VICTOR:
{raio_x}

DATA DE HOJE: {hoje}

─────────────────────────────────────────────
COMO FUNCIONA:

1. Victor manda mensagem (task, pergunta, desabafo, ideia, comando, qualquer coisa)
2. Você ENTENDE o que ele quer usando contexto + histórico
3. Se precisa de dados → usa tool consultar ANTES de responder
4. Se precisa executar algo → usa a tool adequada
5. Responde naturalmente, como pessoa de confiança

─────────────────────────────────────────────
INTELIGÊNCIA ESPERADA:

- "fiz aquilo do Ícaro" → busca task sobre Ícaro e marca como concluída
- "anota: ligar pro João amanhã" → cria item tipo task, prazo amanhã
- "como tá meu panorama?" → consulta pendentes por empresa, dá resumo
- "esse projeto morreu" → cancela items relacionados
- "quantas tasks tenho?" → usa consultar com contar=true
- "sim", "1", "esse" → usa HISTÓRICO para entender referência
- Desabafo/reflexão → responde com empatia, pergunta se quer registrar

─────────────────────────────────────────────
REGRAS:

- Tom: direto, próximo, inteligente. Pessoa de confiança, não robô
- Máximo 4-5 linhas quando possível
- Sem markdown (sem **, sem #, sem -)
- Emojis com moderação
- NUNCA diga "não consigo" — se tem tool disponível, use
- Classifique tipo/empresa/categoria/prioridade automaticamente
- Se não souber a empresa, pergunte
- Pessoa nova mencionada → sugira adicionar ao raio-x"""


# ─────────────────────────────────────────────
# LOOP PRINCIPAL — Claude com tool_use
# ─────────────────────────────────────────────

async def think(user_text: str, chat_id: int) -> str:
    raio_x  = get_raio_x()
    history = get_history(chat_id)

    messages = history + [{"role": "user", "content": user_text}]

    system = SYSTEM_PROMPT.format(
        raio_x=raio_x,
        hoje=date.today().isoformat()
    )

    max_rounds = 6  # segurança contra loops infinitos

    for round_num in range(max_rounds):
        try:
            response = claude.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1500,
                system=system,
                messages=messages,
                tools=TOOLS
            )
        except Exception as e:
            logger.error(f"Erro Claude API: {e}")
            return "Tive um problema ao processar. Tenta de novo?"

        # Separar blocos de texto e tool_use
        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        text_parts  = [b.text for b in response.content if b.type == "text"]

        if not tool_blocks:
            # Sem tools → resposta final
            return " ".join(text_parts).strip() or "..."

        # Tem tool calls → executar e continuar
        # Serializar resposta do assistant para o histórico
        assistant_content = []
        for block in response.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input
                })

        messages.append({"role": "assistant", "content": assistant_content})

        # Executar cada tool e coletar resultados
        tool_results = []
        for block in tool_blocks:
            result = execute_tool(block.name, block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, ensure_ascii=False, default=str)
            })

        messages.append({"role": "user", "content": tool_results})

        logger.info(f"Round {round_num + 1}: {len(tool_blocks)} tool(s) executada(s), stop_reason={response.stop_reason}")

    return "Processamento ficou complexo. Tenta reformular de forma mais direta?"


# ─────────────────────────────────────────────
# HANDLERS DO TELEGRAM
# ─────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    text    = update.message.text
    chat_id = update.effective_chat.id

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        resposta = await think(text, chat_id)

        add_to_history(chat_id, "user",      text)
        add_to_history(chat_id, "assistant", resposta)

        # Telegram tem limite de 4096 chars por mensagem
        if len(resposta) <= 4096:
            await update.message.reply_text(resposta)
        else:
            for i in range(0, len(resposta), 4096):
                await update.message.reply_text(resposta[i:i + 4096])

    except Exception as e:
        logger.error(f"Erro: {e}", exc_info=True)
        await update.message.reply_text("Algo deu errado. Tenta de novo?")


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"🧠 Victor Brain v2 online.\n\n"
        f"Fala comigo naturalmente. Eu consulto o banco, salvo coisas, "
        f"marco como feito, dou panoramas — tudo por conversa.\n\n"
        f"Comandos rápidos:\n"
        f"/pendentes — P1 e P2\n"
        f"/hoje — vencimentos de hoje\n"
        f"/panorama — visão geral\n"
        f"/limpar — limpa histórico\n\n"
        f"Chat ID: {chat_id}"
    )


async def handle_pendentes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    try:
        rows = (supabase.table('items')
                .select('tipo, texto, empresa, prioridade')
                .eq('status', 'pendente')
                .lte('prioridade', 2)
                .order('prioridade')
                .limit(15)
                .execute().data)

        if not rows:
            await update.message.reply_text("Nada crítico ou urgente no momento. ✅")
            return

        EMP = {"betvip": "BetVIP", "ng": "NG", "pwp": "PWP", "pessoal": "Pessoal", "todos": "Geral"}
        lines = ["Prioridades P1 e P2:\n"]
        for r in rows:
            emp = EMP.get(r.get('empresa', 'pessoal'), '?')
            txt = r.get('texto', '')[:70]
            p   = r.get('prioridade', 2)
            lines.append(f"P{p} [{emp}] {txt}")

        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        logger.error(f"Erro /pendentes: {e}")
        await update.message.reply_text("Erro ao buscar. Tenta de novo.")


async def handle_hoje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    try:
        hoje = date.today().isoformat()
        rows = (supabase.table('items')
                .select('tipo, texto, empresa, prioridade')
                .eq('status', 'pendente')
                .or_(f'prazo.eq.{hoje},prioridade.eq.1')
                .order('prioridade')
                .limit(10)
                .execute().data)

        if not rows:
            await update.message.reply_text("Nada urgente para hoje. ✅")
            return

        EMP = {"betvip": "BetVIP", "ng": "NG", "pwp": "PWP", "pessoal": "Pessoal", "todos": "Geral"}
        lines = [f"Para hoje ({hoje}):\n"]
        for r in rows:
            emp = EMP.get(r.get('empresa', 'pessoal'), '?')
            txt = r.get('texto', '')[:70]
            lines.append(f"[{emp}] {txt}")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        logger.error(f"Erro /hoje: {e}")
        await update.message.reply_text("Erro ao buscar. Tenta de novo.")


async def handle_panorama(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pede ao Claude um panorama completo usando as tools"""
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        resposta = await think(
            "Me dá o panorama geral agora: quantas tarefas pendentes total e por empresa, "
            "quais são P1 e P2, se tem algo vencendo hoje ou atrasado, e um resumo rápido do estado geral.",
            chat_id
        )
        add_to_history(chat_id, "user",      "/panorama")
        add_to_history(chat_id, "assistant", resposta)
        await update.message.reply_text(resposta)
    except Exception as e:
        logger.error(f"Erro /panorama: {e}")
        await update.message.reply_text("Erro ao gerar panorama.")


async def handle_limpar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    conversation_history.pop(chat_id, None)
    await update.message.reply_text("Histórico limpo. Novo começo! 🧹")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",     handle_start))
    app.add_handler(CommandHandler("pendentes", handle_pendentes))
    app.add_handler(CommandHandler("hoje",      handle_hoje))
    app.add_handler(CommandHandler("panorama",  handle_panorama))
    app.add_handler(CommandHandler("limpar",    handle_limpar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info(f"🧠 Victor Brain v2 — modelo: {CLAUDE_MODEL}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
