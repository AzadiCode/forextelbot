#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Random Reaction Bot v4 - Fixed & Debugged
════════════════════════════════════════════════════════════════════
"""

import os
import re
import asyncio
import random
import threading
import logging
import json
from flask import Flask, request
from telegram import Bot, ReactionTypeEmoji, Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger("RandomReactionBot")

# ══════════════════════════════════════════════════════════════════════
# 🔑 توکن‌ها
# ══════════════════════════════════════════════════════════════════════
BOT_TOKENS = [
    # 🎯 ربات اصلی (باید ادمین کانال باشد)
    "8504586965:AAH0QZ8QlnofhNE33gNDEQsjym6Ey-CDpSY",   # TOKEN_1 - ربات اصلی
    
    # 🤖 ربات‌های واکنش‌دهنده (فقط عضو کانال باشند)
    "8977670673:AAHmUCp4I9K-LZbui0DS17zvTyaYt4CSwfw",   # TOKEN_2
    "7276868854:AAEYeHXcvwjN35sq_CcBWewGVmQuXqQcgm8",   # TOKEN_3
    "8501873739:AAEVgQQ2vKiZhCtViE-NfzRe7RtCA813MCM",   # TOKEN_4
    "8617939621:AAG0P6rl6WCX4nWnD0X3mnmZ-ii9cx_u3qw",   # TOKEN_5
    "8938641269:AAFHeeKMr1zWKIXacn8FiNcx6fhz2DepYF0",   # TOKEN_6
    "8812461262:AAGb_9XP38sQUpcKe5JJmSz0vp_6lArrguI",   # TOKEN_7
    "8614754345:AAFnMGqqzty-vR7SEmjN4NY0U-EhI0rhYlo",   # TOKEN_8
    "8899642552:AAGChO_p_UPtHbOftUoBZzBLm-lFs5dxIqo",   # TOKEN_9



]
for i in range(1, 201):
    token = os.environ.get(f"TOKEN_{i}", "")
    if token and token.strip():
        BOT_TOKENS.append(token.strip())

if not BOT_TOKENS:
    BOT_TOKENS = []

log.info(f"📋 تعداد توکن‌های بارگذاری شده: {len(BOT_TOKENS)}")

# ══════════════════════════════════════════════════════════════════════
# 🎲 Reactions و تنظیمات
# ══════════════════════════════════════════════════════════════════════
REACTIONS = ["❤️", "🔥", "👍","❤️", "👏", "💯", "❤️", "🙏","❤️", "🏆"]

MIN_DELAY = 8
MAX_DELAY = 45
INITIAL_DELAY_MIN = 3
INITIAL_DELAY_MAX = 15
MAX_RETRIES = 3

_bot_cache = {}
_stats = {
    "updates_received": 0,
    "forwards_processed": 0,
    "reactions_sent": 0,
    "errors": 0,
}


def get_bot(token):
    if token not in _bot_cache:
        _bot_cache[token] = Bot(token)
    return _bot_cache[token]


# ══════════════════════════════════════════════════════════════════════
# 🚀 Reaction با Retry هوشمند
# ══════════════════════════════════════════════════════════════════════
async def try_reaction(bot, chat_id, message_id, reaction, bot_index):
    for attempt in range(MAX_RETRIES):
        try:
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=reaction)],
                is_big=False
            )
            _stats["reactions_sent"] += 1
            return True
        except Exception as e:
            error_msg = str(e).lower()
            
            if "retry_after" in error_msg or "too many" in error_msg:
                match = re.search(r'retry after (\d+)', error_msg)
                wait = int(match.group(1)) + random.uniform(1, 3) if match else random.uniform(8, 20)
                log.warning(f"⏳ Bot {bot_index}: Rate limit، صبر {wait:.1f}s")
                await asyncio.sleep(wait)
                continue
            
            if any(err in error_msg for err in [
                "message_id_invalid", "chat not found",
                "message not found", "bot was blocked",
                "not authorized", "bot can't"
            ]):
                log.warning(f"❌ Bot {bot_index}: {str(e)[:80]}")
                _stats["errors"] += 1
                return False
            
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(random.uniform(1.5, 4))
                continue
            
            log.warning(f"❌ Bot {bot_index}: {str(e)[:80]}")
            _stats["errors"] += 1
            return False
    
    return False


async def send_all_reactions(chat_id, message_id):
    log.info(f"🚀 شروع Reaction روی پیام {message_id} با {len(BOT_TOKENS)} ربات")
    
    bot_indices = list(range(len(BOT_TOKENS)))
    random.shuffle(bot_indices)
    
    success = 0
    failed = 0
    
    for i in bot_indices:
        reaction = random.choice(REACTIONS)
        bot = get_bot(BOT_TOKENS[i])
        
        if await try_reaction(bot, chat_id, message_id, reaction, i+1):
            success += 1
            log.info(f"✅ Bot {i+1} → {reaction}")
        else:
            failed += 1
        
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        await asyncio.sleep(delay)
    
    log.info(f"📊 پایان: ✅ {success} موفق | ❌ {failed} ناموفق")
    _stats["forwards_processed"] += 1
    return success, failed


# ══════════════════════════════════════════════════════════════════════
# 🔍 تشخیص فوروارد (سازگار با PTB v22)
# ══════════════════════════════════════════════════════════════════════
def is_forwarded_message(message):
    """چک می‌کند که آیا پیام فوروارد شده است"""
    if not message:
        return False
    # PTB v22+ (جدید)
    if hasattr(message, 'forward_origin') and message.forward_origin:
        return True
    # نسخه‌های قدیمی (fallback)
    if hasattr(message, 'forward_from_chat') and message.forward_from_chat:
        return True
    if hasattr(message, 'forward_from') and message.forward_from:
        return True
    return False


def get_forward_info(message):
    """استخراج chat_id و message_id از پیام فوروارد شده"""
    original_chat_id = None
    original_message_id = None
    
    # روش جدید PTB v22+
    if hasattr(message, 'forward_origin') and message.forward_origin:
        origin = message.forward_origin
        # origin می‌تواند MessageOriginChannel, MessageOriginUser, یا MessageOriginHiddenUser باشد
        if hasattr(origin, 'chat') and origin.chat:
            original_chat_id = origin.chat.id
        if hasattr(origin, 'message_id'):
            original_message_id = origin.message_id
    
    # روش قدیمی (fallback)
    if not original_chat_id and hasattr(message, 'forward_from_chat') and message.forward_from_chat:
        original_chat_id = message.forward_from_chat.id
        original_message_id = getattr(message, 'forward_from_message_id', None)
    
    return original_chat_id, original_message_id


# ══════════════════════════════════════════════════════════════════════
# 🤖 Command Handlers
# ══════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, context):
    """پاسخ به دستور /start"""
    await update.message.reply_text(
        f"👋 <b>سلام! ربات Reaction فعال است</b>\n\n"
        f"🤖 تعداد ربات‌های متصل: <b>{len(BOT_TOKENS)}</b>\n\n"
        f"📌 <b>نحوه استفاده:</b>\n"
        f"1️⃣ یک پست از کانال را انتخاب کنید\n"
        f"2️⃣ روی آن کلیک راست و Forward را بزنید\n"
        f"3️⃣ به این ربات ارسال کنید\n\n"
        f"⏱ <b>تنظیمات:</b>\n"
        f"• تاخیر اولیه: {INITIAL_DELAY_MIN}-{INITIAL_DELAY_MAX} ثانیه\n"
        f"• فاصله بین Reaction: {MIN_DELAY}-{MAX_DELAY} ثانیه\n\n"
        f"📈 <b>آمار:</b>\n"
        f"• آپدیت‌های دریافتی: {_stats['updates_received']}\n"
        f"• فوروارد پردازش شده: {_stats['forwards_processed']}\n"
        f"• Reaction ارسال شده: {_stats['reactions_sent']}\n\n"
        f"💡 دستور /status برای آمار لحظه‌ای",
        parse_mode="HTML"
    )


async def cmd_status(update: Update, context):
    """نمایش وضعیت ربات"""
    await update.message.reply_text(
        f"📊 <b>وضعیت ربات</b>\n\n"
        f"🤖 ربات‌های متصل: <b>{len(BOT_TOKENS)}</b>\n"
        f"📥 آپدیت‌های دریافتی: <b>{_stats['updates_received']}</b>\n"
        f"📨 فوروارد پردازش شده: <b>{_stats['forwards_processed']}</b>\n"
        f"👍 Reaction ارسال شده: <b>{_stats['reactions_sent']}</b>\n"
        f"❌ خطاها: <b>{_stats['errors']}</b>",
        parse_mode="HTML"
    )


# ══════════════════════════════════════════════════════════════════════
# 🎯 Handler اصلی
# ══════════════════════════════════════════════════════════════════════
async def forward_reaction_handler(update: Update, context):
    """پردازش پیام‌های فوروارد شده"""
    _stats["updates_received"] += 1
    
    message = update.message
    if not message:
        log.warning("⚠️ update.message is None")
        return
    
    log.info(f"📥 پیام دریافت شد از: {message.chat.id}, نوع: {message.chat.type}")
    log.info(f"📄 متن: {(message.text or message.caption or '📎 فایل')[:80]}")
    
    # چک کردن فوروارد
    if not is_forwarded_message(message):
        log.info("⏭️ پیام فوروارد نشده است")
        try:
            await message.reply_text(
                "⚠️ <b>فقط پیام‌های فوروارد شده پردازش می‌شوند!</b>\n\n"
                "📌 لطفاً یک پست از <b>کانال</b> را انتخاب و Forward کنید.\n\n"
                "💡 برای راهنما /start را بزنید.",
                parse_mode="HTML"
            )
        except Exception as e:
            log.error(f"❌ خطا در ارسال پاسخ: {e}")
        return
    
    # استخراج اطلاعات پست اصلی
    original_chat_id, original_message_id = get_forward_info(message)
    
    if not original_chat_id or not original_message_id:
        log.warning("⚠️ نمی‌توان اطلاعات پست اصلی را استخراج کرد")
        try:
            await message.reply_text(
                "❌ <b>خطا!</b>\n\n"
                "نمی‌توان اطلاعات پست اصلی را استخراج کرد.\n"
                "مطمئن شوید پست از یک <b>کانال</b> (نه کاربر) فوروارد شده است.\n\n"
                "💡 ربات‌های Reaction باید عضو آن کانال باشند.",
                parse_mode="HTML"
            )
        except Exception as e:
            log.error(f"❌ خطا در ارسال پاسخ: {e}")
        return
    
    text_preview = (message.text or message.caption or "📎 فایل")[:50]
    log.info(f"📢 [فوروارد] از کانال {original_chat_id}, پیام {original_message_id}: {text_preview}...")
    
    try:
        await message.reply_text(
            f"✅ <b>پست دریافت شد!</b>\n\n"
            f"🤖 شروع Reaction با <b>{len(BOT_TOKENS)}</b> ربات...\n"
            f"⏱ زمان تقریبی: <b>{len(BOT_TOKENS) * ((MIN_DELAY + MAX_DELAY) / 2):.0f}</b> ثانیه",
            parse_mode="HTML"
        )
    except Exception as e:
        log.error(f"❌ خطا در ارسال پاسخ: {e}")
    
    # تاخیر اولیه
    initial_delay = random.uniform(INITIAL_DELAY_MIN, INITIAL_DELAY_MAX)
    log.info(f"⏱ تاخیر اولیه: {initial_delay:.1f}s")
    await asyncio.sleep(initial_delay)
    
    # شروع Reaction
    success, failed = await send_all_reactions(original_chat_id, original_message_id)
    
    try:
        await message.reply_text(
            f"🎯 <b>پایان عملیات!</b>\n\n"
            f"✅ موفق: <b>{success}</b>\n"
            f"❌ ناموفق: <b>{failed}</b>\n"
            f"📊 مجموع: <b>{len(BOT_TOKENS)}</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        log.error(f"❌ خطا در ارسال گزارش: {e}")


# ══════════════════════════════════════════════════════════════════════
# 🌐 Flask Server
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)
_application_ref = None


@flask_app.route('/')
def home():
    return f"""🤖 Random Reaction Bot is Alive!
━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 Bots: {len(BOT_TOKENS)}
⚙️ Mode: Webhook
📥 Updates: {_stats['updates_received']}
📨 Forwards: {_stats['forwards_processed']}
👍 Reactions: {_stats['reactions_sent']}
✅ Status: OK
""", 200


@flask_app.route('/health')
def health():
    return {
        "status": "ok",
        "bots": len(BOT_TOKENS),
        "mode": "webhook",
        "stats": _stats,
    }, 200


@flask_app.route('/webhook-info')
def webhook_info():
    """چک کردن وضعیت webhook از تلگرام"""
    import requests as req
    if not BOT_TOKENS:
        return {"error": "No tokens"}, 500
    try:
        resp = req.get(f"https://api.telegram.org/bot{BOT_TOKENS[0]}/getWebhookInfo", timeout=10)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}, 500


@flask_app.route('/delete-webhook')
def delete_webhook():
    """حذف webhook برای ریست"""
    import requests as req
    if not BOT_TOKENS:
        return {"error": "No tokens"}, 500
    try:
        resp = req.get(f"https://api.telegram.org/bot{BOT_TOKENS[0]}/deleteWebhook?drop_pending_updates=true", timeout=10)
        return {"result": "Webhook deleted", "telegram_response": resp.json()}
    except Exception as e:
        return {"error": str(e)}, 500


# ══════════════════════════════════════════════════════════════════════
# 🎯 Main
# ══════════════════════════════════════════════════════════════════════
def main():
    global _application_ref
    
    if not BOT_TOKENS:
        log.error("❌ هیچ توکنی تنظیم نشده!")
        return
    
    log.info(f"🚀 شروع ربات با {len(BOT_TOKENS)} توکن")
    
    main_token = BOT_TOKENS[0]
    application = Application.builder().token(main_token).build()
    _application_ref = application
    
    # Command Handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("status", cmd_status))
    
    # Message Handler - هر پیام متنی یا کپشن
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            forward_reaction_handler
        )
    )
    
    # Error handler
    async def error_handler(update, context):
        log.error(f"❌ Exception: {context.error}", exc_info=context.error)
    application.add_error_handler(error_handler)
    
    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
    
    if webhook_url:
        log.info("🌐 Running with Flask Webhook")
        
        main_loop = asyncio.new_event_loop()
        
        def run_event_loop(loop):
            asyncio.set_event_loop(loop)
            loop.run_forever()
        
        loop_thread = threading.Thread(target=run_event_loop, args=(main_loop,), daemon=True)
        loop_thread.start()
        
        # Webhook endpoint
        @flask_app.route(f"/{main_token}", methods=["POST"])
        def telegram_webhook():
            try:
                json_data = request.get_json(force=True)
                log.info(f"📥 Webhook: {json.dumps(json_data, ensure_ascii=False)[:300]}")
                update = Update.de_json(json_data, application.bot)
                asyncio.run_coroutine_threadsafe(
                    application.process_update(update),
                    main_loop
                )
            except Exception as e:
                log.error(f"❌ Webhook error: {e}", exc_info=True)
            return "OK", 200
        
        async def setup_webhook():
            # حذف webhook قدیمی
            log.info("🗑 Deleting old webhook...")
            await application.bot.delete_webhook(drop_pending_updates=True)
            await asyncio.sleep(2)
            
            full_url = webhook_url.rstrip("/") + "/" + main_token
            success = await application.bot.set_webhook(
                url=full_url,
                allowed_updates=["message", "edited_message", "channel_post", "callback_query"],
                drop_pending_updates=True,
                secret_token=None,
            )
            log.info(f"✅ Webhook set: {full_url}")
            
            # چک کردن وضعیت
            info = await application.bot.get_webhook_info()
            log.info(f"📊 Webhook info:")
            log.info(f"   URL: {info.url}")
            log.info(f"   Pending: {info.pending_update_count}")
            log.info(f"   Last Error: {info.last_error_message}")
        
        async def initialize():
            await application.initialize()
            await application.start()
            await setup_webhook()
            log.info("🤖 Bot ready ✅")
        
        asyncio.run_coroutine_threadsafe(initialize(), main_loop).result(timeout=30)
        
        port = int(os.environ.get('PORT', 10000))
        log.info(f"🚀 Starting Flask on port {port}")
        flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    
    else:
        log.warning("⚠️ RENDER_EXTERNAL_URL not set, falling back to polling")
        application.run_polling(
            allowed_updates=["message", "edited_message", "channel_post", "callback_query"],
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()
