#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Random Reaction Bot v3 - Webhook + Forward Trigger
════════════════════════════════════════════════════════════════════
ربات ریکشن طبیعی - فقط برای پست‌های فوروارد شده
"""

import os
import re
import asyncio
import random
import threading
import logging
from flask import Flask, request
from telegram import Bot, ReactionTypeEmoji, Update
from telegram.ext import Application, MessageHandler, filters

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

]

# حذف توکن‌های خالی
BOT_TOKENS = [token for token in BOT_TOKENS if token and token.strip()]

log.info(f"📋 تعداد توکن‌های بارگذاری شده: {len(BOT_TOKENS)}")

# ══════════════════════════════════════════════════════════════════════
# 🎲 Reactions (لیست متنوع)
# ══════════════════════════════════════════════════════════════════════
REACTIONS = [
    "❤️", "🔥", "🥰", "👍", "👏", "🎉", "💯", "🙏",
    "😍", "🤩", "💪", "✨", "🌟", "💎", "🏆"
]

# ══════════════════════════════════════════════════════════════════════
# ⏱ تنظیمات زمان (رندوم بین min و max)
# ══════════════════════════════════════════════════════════════════════
MIN_DELAY = 8    # حداقل فاصله بین reaction هر ربات (ثانیه)
MAX_DELAY = 45   # حداکثر فاصله بین reaction هر ربات (ثانیه)
INITIAL_DELAY_MIN = 3   # حداقل تاخیر قبل از شروع reaction ها
INITIAL_DELAY_MAX = 15  # حداکثر تاخیر قبل از شروع reaction ها
MAX_RETRIES = 3         # تعداد تلاش مجدد

# ══════════════════════════════════════════════════════════════════════
# 🤖 Cache برای Bot Objects
# ══════════════════════════════════════════════════════════════════════
_bot_cache = {}


def get_bot(token):
    """گرفتن Bot object از cache"""
    if token not in _bot_cache:
        _bot_cache[token] = Bot(token)
    return _bot_cache[token]


# ══════════════════════════════════════════════════════════════════════
# 🚀 Reaction زدن با Retry هوشمند
# ══════════════════════════════════════════════════════════════════════
async def try_reaction(bot, chat_id, message_id, reaction, bot_index):
    """تلاش برای Reaction با Retry و Rate Limit handling"""
    
    for attempt in range(MAX_RETRIES):
        try:
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=reaction)],
                is_big=False
            )
            return True
        except Exception as e:
            error_msg = str(e).lower()
            
            # Rate Limit
            if "retry_after" in error_msg or "too many" in error_msg:
                match = re.search(r'retry after (\d+)', error_msg)
                if match:
                    wait = int(match.group(1)) + random.uniform(1, 3)
                else:
                    wait = random.uniform(8, 20)
                log.warning(f"⏳ Bot {bot_index}: Rate limit، صبر {wait:.1f}s")
                await asyncio.sleep(wait)
                continue
            
            # خطاهای غیرقابل جبران
            if any(err in error_msg for err in [
                "message_id_invalid", "chat not found", 
                "message not found", "bot was blocked",
                "not authorized", "bot can't"
            ]):
                log.warning(f"❌ Bot {bot_index}: {str(e)[:80]}")
                return False
            
            # تلاش مجدد
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(random.uniform(1.5, 4))
                continue
            
            log.warning(f"❌ Bot {bot_index} ({attempt+1} تلاش): {str(e)[:80]}")
            return False
    
    return False


# ══════════════════════════════════════════════════════════════════════
# 🎯 ارسال Reaction به همه ربات‌ها (100% همه reaction می‌زنند)
# ══════════════════════════════════════════════════════════════════════
async def send_all_reactions(chat_id, message_id):
    """ارسال Reaction به تمام ربات‌ها - همه 100% reaction می‌زنند"""
    
    log.info(f"🚀 شروع Reaction روی پیام {message_id} با {len(BOT_TOKENS)} ربات")
    
    # رندوم کردن ترتیب ربات‌ها
    bot_indices = list(range(len(BOT_TOKENS)))
    random.shuffle(bot_indices)
    
    success = 0
    failed = 0
    
    # پردازش ترتیبی (یکی یکی با فاصله رندوم)
    for i in bot_indices:
        # انتخاب Reaction رندوم
        reaction = random.choice(REACTIONS)
        bot = get_bot(BOT_TOKENS[i])
        
        # تلاش برای Reaction
        if await try_reaction(bot, chat_id, message_id, reaction, i+1):
            success += 1
            log.info(f"✅ Bot {i+1} → {reaction}")
        else:
            failed += 1
        
        # فاصله رندوم بین reaction ها
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        log.debug(f"⏱ فاصله: {delay:.1f}s")
        await asyncio.sleep(delay)
    
    log.info(f"📊 پایان: ✅ {success} موفق | ❌ {failed} ناموفق")
    return success, failed


# ══════════════════════════════════════════════════════════════════════
# 🤖 Handler اصلی - فقط پیام‌های فوروارد شده
# ══════════════════════════════════════════════════════════════════════
async def forward_reaction_handler(update: Update, context):
    """پردازش فقط پیام‌های فوروارد شده به ربات اصلی"""
    
    message = update.message
    if not message:
        return
    
    # چک کن پیام فوروارد شده است
    if not message.forward_from_chat:
        log.debug("⏭️ پیام فوروارد نشده، رد شد")
        return
    
    # گرفتن اطلاعات پست اصلی
    original_chat_id = message.forward_from_chat.id
    original_message_id = message.forward_from_message_id
    
    text_preview = (message.text or message.caption or "📎 فایل")[:50]
    log.info(f"📢 [فوروارد] پست از کانال {original_chat_id}: {text_preview}...")
    
    # پاسخ سریع به کاربر
    await message.reply_text(
        f"✅ پست دریافت شد!\n\n"
        f"🤖 شروع Reaction با {len(BOT_TOKENS)} ربات...\n"
        f"⏱ زمان تقریبی: {len(BOT_TOKENS) * ((MIN_DELAY + MAX_DELAY) / 2):.0f} ثانیه"
    )
    
    # تاخیر اولیه رندوم
    initial_delay = random.uniform(INITIAL_DELAY_MIN, INITIAL_DELAY_MAX)
    log.info(f"⏱ تاخیر اولیه: {initial_delay:.1f}s")
    await asyncio.sleep(initial_delay)
    
    # شروع Reaction روی پست اصلی در کانال
    success, failed = await send_all_reactions(original_chat_id, original_message_id)
    
    # گزارش نهایی
    await message.reply_text(
        f"🎯 <b>پایان عملیات!</b>\n\n"
        f"✅ موفق: <b>{success}</b>\n"
        f"❌ ناموفق: <b>{failed}</b>\n"
        f"📊 مجموع: <b>{len(BOT_TOKENS)}</b>",
        parse_mode="HTML"
    )


# ══════════════════════════════════════════════════════════════════════
# 🌐 Flask (Webhook Server)
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)


@flask_app.route('/')
def home():
    """Endpoint برای UptimeRobot"""
    return f"""
🤖 Random Reaction Bot is Alive!
━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 Bots: {len(BOT_TOKENS)}
⚙️ Mode: Webhook
🎯 Trigger: Forwarded Messages
✅ Status: OK
""", 200


@flask_app.route('/health')
def health():
    """Health check endpoint"""
    return {
        "status": "ok",
        "bots": len(BOT_TOKENS),
        "mode": "webhook",
        "trigger": "forward"
    }, 200


# ══════════════════════════════════════════════════════════════════════
# 🎯 Main Function
# ══════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKENS:
        log.error("❌ هیچ توکنی تنظیم نشده!")
        return
    
    log.info(f"🚀 شروع ربات با {len(BOT_TOKENS)} توکن")
    
    # ساخت Application با توکن اصلی
    main_token = BOT_TOKENS[0]
    application = Application.builder().token(main_token).build()
    
    # Handler فقط برای پیام‌های فوروارد شده
    application.add_handler(
        MessageHandler(
            filters.FORWARDED & ~filters.COMMAND,
            forward_reaction_handler
        )
    )
    
    # ── Webhook vs Polling ──
    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
    
    if webhook_url:
        log.info("🌐 Running with Flask Webhook")
        
        # حلقه asyncio جداگانه
        main_loop = asyncio.new_event_loop()
        
        def run_event_loop(loop):
            asyncio.set_event_loop(loop)
            loop.run_forever()
        
        loop_thread = threading.Thread(target=run_event_loop, args=(main_loop,), daemon=True)
        loop_thread.start()
        
        # Endpoint تلگرام
        @flask_app.route(f"/{main_token}", methods=["POST"])
        def telegram_webhook():
            try:
                json_data = request.get_json(force=True)
                update = Update.de_json(json_data, application.bot)
                asyncio.run_coroutine_threadsafe(
                    application.process_update(update),
                    main_loop
                )
            except Exception as e:
                log.error(f"Webhook error: {e}")
            return "OK", 200
        
        # Setup webhook
        async def setup_webhook():
            full_url = webhook_url.rstrip("/") + "/" + main_token
            await application.bot.set_webhook(
                url=full_url,
                allowed_updates=["message"],
                drop_pending_updates=True,
            )
            log.info(f"✅ Webhook set: {full_url[:70]}...")
        
        async def initialize():
            await application.initialize()
            await application.start()
            await setup_webhook()
            log.info("🤖 Bot ready ✅")
        
        asyncio.run_coroutine_threadsafe(initialize(), main_loop).result(timeout=30)
        
        # اجرای Flask
        port = int(os.environ.get('PORT', 10000))
        log.info(f"🚀 Starting Flask on port {port}")
        flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    
    else:
        # Polling برای تست محلی
        log.warning("⚠️ RENDER_EXTERNAL_URL not set, falling back to polling")
        application.run_polling(
            allowed_updates=["message"],
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()
