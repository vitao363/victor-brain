import os
import json
import re
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

TELEGRAM_TOKEN  = os.environ['TELEGRAM_TOKEN']
ANTHROPIC_KEY   = os.environ['ANTHROPIC_API_KEY']
SUPABASE_URL    = os.environ['SUPABASE_URL']
SUPABASE_KEY    = os.environ['SUPABASE_KEY']
ALLOWED_CHAT_ID = int(os.environ.get('ALLOWED_CHAT_ID', '0'))

claude   = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─────────────────────────────────────────────
# MEMÓRIA DE CONVERSA (em memória, por sessão)
# ─────────────────────────────────────────────
conversation_history: dict[int, list] = {}
MAX_HISTORY = 10  # últimas N mensagens


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
# CONTEXTO COMPLETO (Raio-X + Tasks reais do banco)
# ─────────────────────────────────────────────

def get_full_context() -> str:
    try:
        raio_x_rows = supabase.table('raio_x').select('chave, valor').execute().data
        raio_x = {r['chave']: r['valor'] for r in raio_x_rows}

        tasks = (supabase.table('items')
                 .select('id, texto, empresa, categoria, prioridade, tipo, tags, prazo, status')
                 .eq('status', 'pendente')
                 .order('prioridade')
                 .limit(40)
                 .execute().data)

        return json.dumps({
            "raio_x": raio_x,
            "tasks_pendentes": tasks,
            "hoje": date.today().isoformat()
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erro ao buscar contexto: {e}")
        return "{}"


# ─────────────────────────────────────────────
# PROMPT DO ASSISTENTE
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """Você é a secretária pessoal e inteligente do Victor Silva, especialista em CRM de iGaming.

Você tem acesso completo ao contexto dele:
- Raio-X: quem ele é, o que faz em cada empresa, prioridades
- Tasks pendentes reais do banco de dados
- Histórico da conversa atual

CONTEXTO ATUAL:
{context}

─────────────────────────────────────────────
COMO VOCÊ FUNCIONA:

1. Você lê o histórico + a nova mensagem e ENTENDE o que Victor quer
2. Você responde de forma natural, como uma secretária próxima e inteligente
3. Se precisar executar uma ação (salvar, concluir, cancelar, atualizar), inclua no JSON

SEMPRE retorne um JSON válido (sem markdown, sem texto extra):
{{
  "resposta": "sua resposta em texto natural, sem markdown, sem asteriscos",
  "acao": null,
  "dados": {{}}
}}

Valores possíveis para "acao":

→ "salvar_item": quando Victor registrar algo novo
  "dados": {{
    "tipo": "task|idea|insight|question|priority|meeting|financial|health|personal",
    "texto": "texto limpo do item",
    "empresa": "betvip|ng|pwp|pessoal|todos",
    "categoria": "crm|growth|produto|lideranca|saude|financeiro|pessoal|projeto",
    "prioridade": 1,
    "tags": ["tag"],
    "prazo": "YYYY-MM-DD ou null"
  }}

→ "concluir_busca": marcar item específico como concluído por texto
  "dados": {{"busca": "palavras-chave do item"}}

→ "cancelar_busca": cancelar item específico por texto
  "dados": {{"busca": "palavras-chave do item"}}

→ "bulk_concluir": concluir todos de uma empresa ou categoria
  "dados": {{"filtro_empresa": "pwp|ng|betvip|pessoal|null", "filtro_categoria": "crm|saude|...|null"}}

→ "bulk_cancelar": cancelar todos de uma empresa ou categoria
  "dados": {{"filtro_empresa": "pwp|ng|betvip|pessoal|null", "filtro_categoria": "crm|saude|...|null"}}

→ "atualizar_raiox": atualizar chave do Raio-X
  "dados": {{"chave": "nome_da_chave", "valor": "novo valor completo"}}

─────────────────────────────────────────────
REGRAS DE COMPORTAMENTO:

- Use o HISTÓRICO para entender respostas curtas ("1", "sim", "todas", "isso")
- Use as TASKS REAIS do banco para responder perguntas sobre o que está pendente
- Se perguntarem sobre uma empresa/projeto que não existe no contexto, diga que não está cadastrado e pergunte se quer adicionar
- Quando Victor disser que concluiu algo, execute a ação E confirme
- Quando Victor perguntar sobre prioridades, use os dados reais de tasks_pendentes
- Tom: direto, próximo, sem enrolação. Máximo 4 linhas na resposta quando possível
- Sem markdown na resposta (sem **, sem #, sem -)
- Emojis com moderação, só quando fizer sentido"""


# ─────────────────────────────────────────────
# CHAMADA AO CLAUDE (com histórico)
# ─────────────────────────────────────────────

async def think(user_text: str, chat_id: int) -> dict:
    context  = get_full_context()
    history  = get_history(chat_id)
    messages = history + [{"role": "user", "content": user_text}]

    response = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        system=SYSTEM_PROMPT.format(context=context),
        messages=messages
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


# ─────────────────────────────────────────────
# EXECUÇÃO DE AÇÕES
# ─────────────────────────────────────────────

def do_salvar_item(dados: dict, original: str, msg_id: int):
    row = {
        "tipo":         dados.get("tipo", "idea"),
        "texto":        dados.get("texto", original),
        "empresa":      dados.get("empresa", "pessoal"),
        "categoria":    dados.get("categoria", "pessoal"),
        "prioridade":   dados.get("prioridade", 3),
        "status":       "pendente",
        "tags":         dados.get("tags", []),
        "prazo":        dados.get("prazo"),
        "fonte":        "telegram",
        "msg_original": original,
    }
    supabase.table('items').insert(row).execute()
    supabase.table('log_mensagens').insert({
        "telegram_msg_id":     msg_id,
        "conteudo_raw":        original,
        "conteudo_processado": json.dumps(dados, ensure_ascii=False),
        "items_gerados":       1,
    }).execute()


def do_busca_update(busca: str, novo_status: str) -> int:
    words = [w for w in busca.lower().split() if len(w) > 3]
    if not words:
        return 0
    rows = (supabase.table('items')
            .select('id, texto')
            .eq('status', 'pendente')
            .order('criado_em', desc=True)
            .limit(100)
            .execute().data)

    matches = []
    for row in rows:
        txt = (row.get('texto') or '').lower()
        score = sum(1 for w in words if w in txt)
        if score >= max(1, len(words) // 2):
            matches.append((score, row['id']))

    matches.sort(key=lambda x: -x[0])
    count = 0
    patch = {"status": novo_status}
    if novo_status == "concluido":
        patch["concluido_em"] = datetime.utcnow().isoformat()

    for _, item_id in matches[:5]:
        supabase.table('items').update(patch).eq('id', item_id).execute()
        count += 1
    return count


def do_bulk_update(novo_status: str, filtro_empresa: str | None, filtro_categoria: str | None) -> int:
    patch = {"status": novo_status}
    if novo_status == "concluido":
        patch["concluido_em"] = datetime.utcnow().isoformat()

    query = supabase.table('items').update(patch).eq('status', 'pendente')
    if filtro_empresa and filtro_empresa != "null":
        query = query.eq('empresa', filtro_empresa)
    if filtro_categoria and filtro_categoria != "null":
        query = query.eq('categoria', filtro_categoria)

    res = query.execute()
    return len(res.data) if res.data else 0


def do_atualizar_raiox(chave: str, valor: str) -> bool:
    try:
        supabase.table('raio_x').upsert(
            {"chave": chave, "valor": valor},
            on_conflict="chave"
        ).execute()
        return True
    except Exception as e:
        logger.error(f"Erro raio-x: {e}")
        return False


# ─────────────────────────────────────────────
# HANDLER PRINCIPAL
# ─────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    text    = update.message.text
    msg_id  = update.message.message_id
    chat_id = update.effective_chat.id

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        result   = await think(text, chat_id)
        resposta = result.get("resposta", "...")
        acao     = result.get("acao")
        dados    = result.get("dados", {})

        # executa ação se houver
        extra = ""
        if acao == "salvar_item":
            do_salvar_item(dados, text, msg_id)
            extra = " ✅"

        elif acao == "concluir_busca":
            count = do_busca_update(dados.get("busca", text), "concluido")
            extra = f" ({count} item{'s' if count != 1 else ''} concluído{'s' if count != 1 else ''})"

        elif acao == "cancelar_busca":
            count = do_busca_update(dados.get("busca", text), "cancelado")
            extra = f" ({count} item{'s' if count != 1 else ''} cancelado{'s' if count != 1 else ''})"

        elif acao == "bulk_concluir":
            count = do_bulk_update("concluido", dados.get("filtro_empresa"), dados.get("filtro_categoria"))
            extra = f" ({count} itens concluídos)"

        elif acao == "bulk_cancelar":
            count = do_bulk_update("cancelado", dados.get("filtro_empresa"), dados.get("filtro_categoria"))
            extra = f" ({count} itens cancelados)"

        elif acao == "atualizar_raiox":
            do_atualizar_raiox(dados.get("chave", ""), dados.get("valor", ""))

        # adiciona ao histórico
        add_to_history(chat_id, "user",      text)
        add_to_history(chat_id, "assistant", resposta + extra)

        await update.message.reply_text(resposta + extra)

    except json.JSONDecodeError as e:
        logger.error(f"JSON inválido: {e}")
        await update.message.reply_text("Tive um problema interno. Tenta de novo?")
    except Exception as e:
        logger.error(f"Erro: {e}", exc_info=True)
        await update.message.reply_text("Algo deu errado. Já olho isso.")


# ─────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"🧠 Victor Brain online.\n\n"
        f"Fala comigo como fala com uma pessoa. Registro tasks, ideias, insights, "
        f"marco coisas como feitas, busco o que está pendente — tudo por conversa natural.\n\n"
        f"Comandos rápidos:\n"
        f"/pendentes — prioridades P1 e P2\n"
        f"/hoje — o que vence hoje\n"
        f"/limpar — limpa histórico da conversa\n\n"
        f"Seu chat ID: {chat_id}"
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

        EMPRESA = {"betvip":"BetVIP","ng":"NG","pwp":"PWP","pessoal":"Pessoal","todos":"Geral"}
        lines = ["Prioridades P1 e P2:\n"]
        for r in rows:
            emp = EMPRESA.get(r.get('empresa','pessoal'), '?')
            txt = r.get('texto','')[:70]
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
            await update.message.reply_text("Nada com vencimento hoje.")
            return

        EMPRESA = {"betvip":"BetVIP","ng":"NG","pwp":"PWP","pessoal":"Pessoal","todos":"Geral"}
        lines = [f"Para hoje ({hoje}):\n"]
        for r in rows:
            emp = EMPRESA.get(r.get('empresa','pessoal'),'?')
            txt = r.get('texto','')[:70]
            lines.append(f"[{emp}] {txt}")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        logger.error(f"Erro /hoje: {e}")
        await update.message.reply_text("Erro ao buscar. Tenta de novo.")


async def handle_limpar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    conversation_history.pop(chat_id, None)
    await update.message.reply_text("Histórico da conversa limpo. Novo começo! 🧹")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",     handle_start))
    app.add_handler(CommandHandler("pendentes", handle_pendentes))
    app.add_handler(CommandHandler("hoje",      handle_hoje))
    app.add_handler(CommandHandler("limpar",    handle_limpar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🧠 Victor Brain — modo assistente iniciado.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
