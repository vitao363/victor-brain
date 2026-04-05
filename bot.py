import os
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes
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
# INTENT DETECTION + CLASSIFICATION
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """Você é o assistente pessoal do Victor Silva, especialista em CRM de iGaming.

CONTEXTO ATUAL DO VICTOR (Raio-X):
{raio_x}

Analise a mensagem e retorne APENAS um JSON válido (sem markdown, sem texto extra).

PRIMEIRO determine a "intencao":
- "novo_item"     → Victor quer registrar task, ideia, insight, reunião, etc.
- "concluir"      → Victor diz que terminou/fez/concluiu algo ("concluí X", "feito X", "terminei X", "done X")
- "cancelar"      → Victor quer cancelar/remover/excluir algo
- "atualizar_raiox" → Victor atualiza seu contexto ("agora estou", "parei com X", "mudei de X")
- "consulta"      → Victor pergunta algo ("o que tenho", "quanto falta", "quais são")

Para "novo_item" retorne:
{{
  "intencao": "novo_item",
  "tipo": "task|idea|insight|question|priority|meeting|financial|health|personal",
  "texto": "versão limpa e estruturada",
  "categoria": "crm|growth|produto|lideranca|saude|financeiro|pessoal|projeto",
  "empresa": "betvip|ng|pwp|pessoal|todos",
  "prioridade": 1,
  "tags": ["tag"],
  "pessoas": [],
  "prazo": "YYYY-MM-DD ou null",
  "resumo_confirmacao": "frase curta confirmando o que foi salvo"
}}

Para "concluir" retorne:
{{
  "intencao": "concluir",
  "busca": "texto-chave do item a marcar como feito (palavras principais)",
  "resumo_confirmacao": "frase confirmando conclusão"
}}

Para "cancelar" retorne:
{{
  "intencao": "cancelar",
  "busca": "texto-chave do item a cancelar",
  "resumo_confirmacao": "frase confirmando cancelamento"
}}

Para "atualizar_raiox" retorne:
{{
  "intencao": "atualizar_raiox",
  "chave": "chave existente no raio-x a atualizar (ex: pwp_contexto, prioridades_agora)",
  "novo_valor": "novo valor completo para essa chave",
  "resumo_confirmacao": "frase confirmando atualização"
}}

Para "consulta" retorne:
{{
  "intencao": "consulta",
  "resposta": "resposta direta baseada no contexto do Raio-X"
}}

Regras:
- prioridade 1 = crítico (faz hoje), 5 = algum dia
- empresa: BetVIP→betvip, NG→ng, PWP→pwp
- tags: lowercase com hífen, máximo 4
- tom: assistente próximo, direto"""


async def analyze(text: str, raio_x: str) -> dict:
    response = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system=SYSTEM_PROMPT.format(raio_x=raio_x),
        messages=[{"role": "user", "content": text}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

# ─────────────────────────────────────────────
# SUPABASE ACTIONS
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
        "telegram_msg_id":     msg_id,
        "conteudo_raw":        original,
        "conteudo_processado": json.dumps(c, ensure_ascii=False),
        "items_gerados":       1,
    }).execute()
    return res.data[0]['id'] if res.data else None


def find_and_update_status(busca: str, novo_status: str) -> list:
    """Busca itens por texto e atualiza status. Retorna itens encontrados."""
    words = [w for w in busca.lower().split() if len(w) > 3]
    if not words:
        return []

    # busca os 50 mais recentes pendentes e filtra por similaridade
    rows = (supabase.table('items')
            .select('id, texto, empresa')
            .eq('status', 'pendente')
            .order('criado_em', desc=True)
            .limit(100)
            .execute().data)

    matches = []
    for row in rows:
        texto_lower = (row.get('texto') or '').lower()
        score = sum(1 for w in words if w in texto_lower)
        if score >= max(1, len(words) // 2):
            matches.append((score, row))

    matches.sort(key=lambda x: -x[0])
    updated = []
    for _, row in matches[:3]:  # atualiza até 3 matches
        from datetime import datetime
        patch = {"status": novo_status}
        if novo_status == "concluido":
            patch["concluido_em"] = datetime.utcnow().isoformat()
        supabase.table('items').update(patch).eq('id', row['id']).execute()
        updated.append(row)

    return updated


def update_raio_x(chave: str, valor: str) -> bool:
    try:
        supabase.table('raio_x').upsert(
            {"chave": chave, "valor": valor},
            on_conflict="chave"
        ).execute()
        return True
    except Exception as e:
        logger.error(f"Erro ao atualizar Raio-X: {e}")
        return False

# ─────────────────────────────────────────────
# LABELS
# ─────────────────────────────────────────────

TIPO_EMOJI    = {"task":"✅","idea":"💡","insight":"🧠","question":"❓",
                 "priority":"🚨","meeting":"📅","financial":"💰","health":"💪","personal":"👤"}
EMPRESA_LABEL = {"betvip":"BetVIP","ng":"NG","pwp":"PWP","pessoal":"Pessoal","todos":"Geral"}
PRIO_STARS    = {1:"🔴🔴🔴",2:"🔴🔴",3:"🟡",4:"🟢",5:"⚪"}

# ─────────────────────────────────────────────
# MAIN MESSAGE HANDLER
# ─────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    text   = update.message.text
    msg_id = update.message.message_id

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        raio_x  = get_raio_x()
        result  = await analyze(text, raio_x)
        intencao = result.get("intencao", "novo_item")

        # ── NOVO ITEM ──────────────────────────────
        if intencao == "novo_item":
            save_item(result, text, msg_id)
            emoji   = TIPO_EMOJI.get(result.get("tipo","idea"), "📝")
            empresa = EMPRESA_LABEL.get(result.get("empresa","pessoal"), "Geral")
            prio    = PRIO_STARS.get(result.get("prioridade", 3), "🟡")
            confirm = result.get("resumo_confirmacao", "Salvo!")
            reply   = f"{emoji} <b>Salvo!</b>\n\n{confirm}\n\n<code>{empresa}</code> {prio}"
            if result.get("prazo"):
                reply += f"\n📅 Prazo: <code>{result['prazo']}</code>"
            if result.get("tags"):
                reply += "\n" + " ".join(f"#{t}" for t in result["tags"][:3])
            await update.message.reply_text(reply, parse_mode="HTML")

        # ── CONCLUIR ───────────────────────────────
        elif intencao == "concluir":
            busca   = result.get("busca", text)
            updated = find_and_update_status(busca, "concluido")
            confirm = result.get("resumo_confirmacao", "Marcado como feito!")
            if updated:
                items_txt = "\n".join(f"  ✅ {r['texto'][:60]}" for r in updated)
                reply = f"✅ <b>Concluído!</b>\n\n{confirm}\n\n{items_txt}"
            else:
                reply = f"🔍 Não encontrei nenhum item pendente com esse texto. Tenta descrever diferente ou usa /pendentes pra ver a lista."
            await update.message.reply_text(reply, parse_mode="HTML")

        # ── CANCELAR ───────────────────────────────
        elif intencao == "cancelar":
            busca   = result.get("busca", text)
            updated = find_and_update_status(busca, "cancelado")
            confirm = result.get("resumo_confirmacao", "Cancelado.")
            if updated:
                items_txt = "\n".join(f"  ❌ {r['texto'][:60]}" for r in updated)
                reply = f"❌ <b>Cancelado!</b>\n\n{confirm}\n\n{items_txt}"
            else:
                reply = "🔍 Não encontrei item com esse texto. Usa /pendentes pra ver o que está aberto."
            await update.message.reply_text(reply, parse_mode="HTML")

        # ── ATUALIZAR RAIO-X ───────────────────────
        elif intencao == "atualizar_raiox":
            chave  = result.get("chave", "estado_atual")
            valor  = result.get("novo_valor", text)
            ok     = update_raio_x(chave, valor)
            confirm = result.get("resumo_confirmacao", "Raio-X atualizado.")
            if ok:
                reply = f"🔄 <b>Raio-X atualizado!</b>\n\n{confirm}\n\n<code>{chave}</code> → salvo."
            else:
                reply = "⚠️ Erro ao atualizar o Raio-X. Tenta de novo."
            await update.message.reply_text(reply, parse_mode="HTML")

        # ── CONSULTA ───────────────────────────────
        elif intencao == "consulta":
            resposta = result.get("resposta", "Não sei responder isso ainda.")
            await update.message.reply_text(f"🧠 {resposta}", parse_mode="HTML")

        else:
            # fallback → salva como item
            save_item(result, text, msg_id)
            await update.message.reply_text("📝 <b>Salvo!</b>", parse_mode="HTML")

    except json.JSONDecodeError as e:
        logger.error(f"JSON inválido: {e}")
        await update.message.reply_text("⚠️ Erro ao classificar. Tenta reformular?")
    except Exception as e:
        logger.error(f"Erro geral: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Algo deu errado. Já anoto e sigo.")

# ─────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"🧠 <b>Victor Brain online.</b>\n\n"
        f"Joga qualquer coisa aqui — task, ideia, insight, prioridade.\n"
        f"Eu classifico, organizo e salvo automaticamente.\n\n"
        f"<b>Comandos:</b>\n"
        f"/pendentes — prioridades P1 e P2 🔥\n"
        f"/hoje — tasks com prazo hoje\n"
        f"/raio_x — teu contexto atual\n"
        f"/concluir [texto] — marca item como feito\n\n"
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
                .limit(15)
                .execute().data)

        if not rows:
            await update.message.reply_text("✅ Nada crítico ou urgente no momento!")
            return

        lines = ["🚨 <b>Prioridades altas:</b>\n"]
        for r in rows:
            emp   = EMPRESA_LABEL.get(r.get('empresa','pessoal'), '?')
            txt   = r.get('texto','')[:65]
            p     = r.get('prioridade', 3)
            emoji = TIPO_EMOJI.get(r.get('tipo','task'), '📝')
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
            await update.message.reply_text("🎯 Nada específico pra hoje. Foca nas prioridades!")
            return

        lines = [f"📅 <b>Para hoje ({hoje}):</b>\n"]
        for r in rows:
            emp   = EMPRESA_LABEL.get(r.get('empresa','pessoal'),'?')
            txt   = r.get('texto','')[:65]
            emoji = TIPO_EMOJI.get(r.get('tipo','task'),'📝')
            lines.append(f"{emoji} <code>[{emp}]</code> {txt}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Erro em /hoje: {e}")
        await update.message.reply_text("⚠️ Erro ao buscar tasks de hoje.")


async def handle_concluir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    busca = " ".join(context.args) if context.args else ""
    if not busca:
        await update.message.reply_text(
            "Usa assim: <code>/concluir [parte do texto do item]</code>\n"
            "Exemplo: <code>/concluir programação de abril</code>",
            parse_mode="HTML"
        )
        return
    updated = find_and_update_status(busca, "concluido")
    if updated:
        items_txt = "\n".join(f"  ✅ {r['texto'][:70]}" for r in updated)
        await update.message.reply_text(f"✅ <b>Marcado como feito!</b>\n\n{items_txt}", parse_mode="HTML")
    else:
        await update.message.reply_text(
            f"🔍 Não encontrei item pendente com <i>{busca}</i>.\nUsa /pendentes pra ver a lista.",
            parse_mode="HTML"
        )


async def handle_raio_x(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    try:
        rows = supabase.table('raio_x').select('chave, valor').execute().data
        if not rows:
            await update.message.reply_text("Raio-X vazio.")
            return
        chaves_resumo = ['identidade','prioridades_agora','estado_atual','projeto_brain']
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
    app.add_handler(CommandHandler("concluir",  handle_concluir))
    app.add_handler(CommandHandler("raio_x",    handle_raio_x))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🧠 Victor Brain iniciado.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
