import os
import json
import logging
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
# HELPERS
# ─────────────────────────────────────────────

def get_raio_x() -> str:
    try:
        rows = supabase.table('raio_x').select('chave, valor').execute().data
        return json.dumps({r['chave']: r['valor'] for r in rows}, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erro ao buscar Raio-X: {e}")
        return "{}"

def is_allowed(update: Update) -> bool:
    return not ALLOWED_CHAT_ID or update.effective_chat.id == ALLOWED_CHAT_ID

# ─────────────────────────────────────────────
# CLASSIFICAÇÃO VIA CLAUDE
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """Você é o assistente pessoal do Victor Silva, especialista em CRM de iGaming.

CONTEXTO ATUAL DO VICTOR (Raio-X):
{raio_x}

Sua tarefa: analisar a mensagem e retornar APENAS um JSON válido (sem markdown, sem texto extra):

{{
  "tipo": "task|idea|insight|question|priority|meeting|financial|health|personal",
  "texto": "versão limpa e estruturada da mensagem",
  "categoria": "crm|growth|produto|lideranca|saude|financeiro|pessoal|projeto",
  "empresa": "betvip|ng|pwp|pessoal|todos",
  "prioridade": 1,
  "status": "pendente",
  "tags": ["tag-exemplo"],
  "pessoas": ["nome se mencionado"],
  "prazo": "YYYY-MM-DD ou null",
  "resumo_confirmacao": "frase curta e direta confirmando o que foi salvo"
}}

Regras de classificação:
- tipo "priority"  → algo urgente que Victor mencionou explicitamente
- tipo "task"      → algo a fazer
- tipo "idea"      → ideia, insight, pensamento
- tipo "financial" → qualquer menção a dinheiro, receita, custo
- tipo "health"    → academia, saúde, bem-estar
- prioridade 1     = crítico (faz hoje), 5 = talvez um dia
- empresa: menciona BetVIP/betvip → "betvip" | NG/ng → "ng" | PWP → "pwp"
- tags: lowercase com hífen, máximo 5
- resumo_confirmacao: tom de assistente próximo, direto ao ponto"""

async def classify(text: str, raio_x: str) -> dict:
    response = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system=SYSTEM_PROMPT.format(raio_x=raio_x),
        messages=[{"role": "user", "content": text}]
    )
    raw = response.content[0].text.strip()
    # limpar markdown caso venha com ```json
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

# ─────────────────────────────────────────────
# SUPABASE
# ─────────────────────────────────────────────

def save_item(c: dict, original: str, msg_id: int) -> str | None:
    row = {
        "tipo":         c.get("tipo", "idea"),
        "texto":        c.get("texto", original),
        "categoria":    c.get("categoria", "pessoal"),
        "empresa":      c.get("empresa", "pessoal"),
        "prioridade":   c.get("prioridade", 3),
        "status":       "pendente",
        "tags":         c.get("tags", []),
        "pessoas":      c.get("pessoas", []),
        "prazo":        c.get("prazo"),
        "fonte":        "telegram",
        "msg_original": original,
    }
    res = supabase.table('items').insert(row).execute()

    supabase.table('log_mensagens').insert({
        "telegram_msg_id":    msg_id,
        "conteudo_raw":       original,
        "conteudo_processado": json.dumps(c, ensure_ascii=False),
        "items_gerados":      1,
    }).execute()

    return res.data[0]['id'] if res.data else None

# ─────────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────────

TIPO_EMOJI = {
    "task": "✅", "idea": "💡", "insight": "🧠", "question": "❓",
    "priority": "🚨", "meeting": "📅", "financial": "💰",
    "health": "💪", "personal": "👤",
}
EMPRESA_LABEL = {
    "betvip": "BetVIP", "ng": "NG", "pwp": "PWP",
    "pessoal": "Pessoal", "todos": "Geral",
}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    text   = update.message.text
    msg_id = update.message.message_id

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        raio_x     = get_raio_x()
        classified = await classify(text, raio_x)
        save_item(classified, text, msg_id)

        emoji   = TIPO_EMOJI.get(classified.get("tipo", "idea"), "📝")
        empresa = EMPRESA_LABEL.get(classified.get("empresa", "pessoal"), "Geral")
        p       = classified.get("prioridade", 3)
        stars   = "⭐" * (6 - p)
        confirm = classified.get("resumo_confirmacao", "Salvo!")

        reply = f"{emoji} <b>Salvo!</b>\n\n{confirm}\n\n<code>{empresa}</code> {stars}"

        if classified.get("prazo"):
            reply += f"\n📅 Prazo: <code>{classified['prazo']}</code>"

        if classified.get("tags"):
            tags_str = " ".join([f"#{t}" for t in classified["tags"][:3]])
            reply += f"\n{tags_str}"

        await update.message.reply_text(reply, parse_mode="HTML")

    except json.JSONDecodeError as e:
        logger.error(f"JSON inválido do Claude: {e}")
        await update.message.reply_text("⚠️ Erro ao classificar. Tenta de novo com mais detalhes?")
    except Exception as e:
        logger.error(f"Erro geral: {e}")
        await update.message.reply_text("⚠️ Algo deu errado. Já anoto e sigo.")


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"🧠 <b>Victor Brain online.</b>\n\n"
        f"Joga qualquer coisa aqui — task, ideia, insight, prioridade.\n"
        f"Eu classifico, organizo e salvo automaticamente.\n\n"
        f"<b>Comandos:</b>\n"
        f"/pendentes — lista suas prioridades 🔥\n"
        f"/hoje — o que fazer hoje\n"
        f"/raio_x — resumo do seu contexto atual\n\n"
        f"Seu chat ID: <code>{chat_id}</code>\n"
        f"Coloca esse ID no Railway como <code>ALLOWED_CHAT_ID</code>",
        parse_mode="HTML"
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
                .limit(10)
                .execute().data)

        if not rows:
            await update.message.reply_text("✅ Nada crítico ou urgente no momento!")
            return

        lines = ["🚨 <b>Prioridades altas:</b>\n"]
        for r in rows:
            emp   = EMPRESA_LABEL.get(r.get('empresa', 'pessoal'), '?')
            txt   = r.get('texto', '')[:70]
            p     = r.get('prioridade', 3)
            emoji = TIPO_EMOJI.get(r.get('tipo', 'task'), '📝')
            lines.append(f"P{p} {emoji} <code>[{emp}]</code> {txt}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    except Exception as e:
        logger.error(f"Erro em /pendentes: {e}")
        await update.message.reply_text("⚠️ Erro ao buscar pendentes.")


async def handle_hoje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    try:
        from datetime import date
        hoje = date.today().isoformat()

        rows = (supabase.table('items')
                .select('tipo, texto, empresa, prioridade')
                .eq('status', 'pendente')
                .or_(f'prazo.eq.{hoje},prioridade.eq.1')
                .order('prioridade')
                .limit(10)
                .execute().data)

        if not rows:
            await update.message.reply_text("🎯 Nada específico pra hoje. Foca nas prioridades gerais!")
            return

        lines = [f"📅 <b>Para hoje ({hoje}):</b>\n"]
        for r in rows:
            emp   = EMPRESA_LABEL.get(r.get('empresa', 'pessoal'), '?')
            txt   = r.get('texto', '')[:70]
            emoji = TIPO_EMOJI.get(r.get('tipo', 'task'), '📝')
            lines.append(f"{emoji} <code>[{emp}]</code> {txt}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    except Exception as e:
        logger.error(f"Erro em /hoje: {e}")
        await update.message.reply_text("⚠️ Erro ao buscar tasks de hoje.")


async def handle_raio_x(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    try:
        rows = supabase.table('raio_x').select('chave, valor').execute().data
        if not rows:
            await update.message.reply_text("Raio-X vazio. Popula ele primeiro!")
            return

        # mostrar só chaves principais
        chaves_resumo = ['identidade', 'prioridades_agora', 'estado_atual']
        lines = ["🔍 <b>Raio-X atual:</b>\n"]
        for r in rows:
            if r['chave'] in chaves_resumo:
                val = r['valor'][:200] + ("..." if len(r['valor']) > 200 else "")
                lines.append(f"<b>{r['chave']}</b>\n{val}\n")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    except Exception as e:
        logger.error(f"Erro em /raio_x: {e}")
        await update.message.reply_text("⚠️ Erro ao buscar Raio-X.")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",     handle_start))
    app.add_handler(CommandHandler("pendentes", handle_pendentes))
    app.add_handler(CommandHandler("hoje",      handle_hoje))
    app.add_handler(CommandHandler("raio_x",    handle_raio_x))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🧠 Victor Brain iniciado.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
