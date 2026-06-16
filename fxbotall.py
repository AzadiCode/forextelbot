#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════╗
║     🎯 Random Reaction Bot v3  ·  No Crash  ·  Multi-Account       ║
║     ربات Reaction رندوم - بدون کرش با خطا                           ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os
import asyncio
import random
import threading
import logging
from datetime import datetime
from flask import Flask
from telegram import Bot
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut, NetworkError
from telegram.ext import Application, MessageHandler, ContextTypes, filters

# ══════════════════════════════════════════════════════════════════════
# ⚙️  تنظیمات
# ══════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger("RandomReactionBot")

# ══════════════════════════════════════════════════════════════════════
# 🔑  لیست توکن‌ها
# ══════════════════════════════════════════════════════════════════════
BOT_TOKENS = [
    os.environ.get("TOKEN_1", ""),
    os.environ.get("TOKEN_2", ""),
    os.environ.get("TOKEN_3", ""),
    os.environ.get("TOKEN_4", ""),
    os.environ.get("TOKEN_5", ""),
    os.environ.get("TOKEN_6", ""),
    os.environ.get("TOKEN_7", ""),
    os.environ.get("TOKEN_8", ""),
    os.environ.get("TOKEN_9", ""),
    os.environ.get("TOKEN_10", ""),
    os.environ.get("TOKEN_11", ""),
    os.environ.get("TOKEN_12", ""),
    os.environ.get("TOKEN_13", ""),
    os.environ.get("TOKEN_14", ""),
    os.environ.get("TOKEN_15", ""),
    os.environ.get("TOKEN_16", ""),
    os.environ.get("TOKEN_17", ""),
    os.environ.get("TOKEN_18", ""),
    os.environ.get("TOKEN_19", ""),
    os.environ.get("TOKEN_20", ""),
]

BOT_TOKENS = [token for token in BOT_TOKENS if token.strip()]

# ══════════════════════════════════════════════════════════════════════
# 🎲  Reactionهای رندوم (۵۰ تا)
# ══════════════════════════════════════════════════════════════════════
REACTIONS = [
    "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱",
    "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "🤓", "🥱", "💯",
    "🌟", "⭐", "💫", "✨", "💥", "💢", "🎈", "🎁", "🏆", "🥇",
    "🥈", "🥉", "⚽", "🏀", "🎮", "🎨", "🎭", "🎪", "🎯", "🎲",
    "🍕", "🍔", "🍟", "🌭", "🍿", "🧁", "🍩", "🍪", "🎂", "🍰"
]

# ══════════════════════════════════════════════════════════════════════
# 🎯  کلمات تریگر
# ══════════════════════════════════════════════════════════════════════
TRIGGER_KEYWORDS = [
    "طلا", "gold", "قیمت", "xau", "انس",
    "عالی", "خوب", "مرسی", "ممنون", "سپاس",
    "تحلیل", "بررسی", "نظر", "چطور", "چرا",
    "تبریک", "مبارک", "فوق‌العاده", "سود", "رشد"
]

# ══════════════════════════════════════════════════════════════════════
# ⏱  تنظیمات زمانی
# ══════════════════════════════════════════════════════════════════════
MIN_DELAY = 1.5  # حداقل فاصله (ثانیه)
MAX_DELAY = 3.5  # حداکثر فاصله
COOLDOWN = 60    # فاصله بین تریگرها (ثانیه)
MIN_MESSAGE_LENGTH = 3

# ══════════════════════════════════════════════════════════════════════
# 🌐  Flask
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return f"""
🎯 Random Reaction Bot v3 is alive!

🤖 ربات‌های فعال: {len(BOT_TOKENS)}
🎲 Reactionهای موجود: {len(REACTIONS)}
⏱ Cooldown: {COOLDOWN}s
⏰ زمان: {datetime.now().strftime('%H:%M:%S')}

✅ فیکس شده: بدون کرش با خطا
"""

@flask_app.route('/health')
def health():
    return {"status": "ok", "bots": len(BOT_TOKENS)}

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════
# 🔍  تشخیص کلمات کلیدی
# ══════════════════════════════════════════════════════════════════════
def should_trigger(text):
    text_lower = text.lower()
    if len(text) < MIN_MESSAGE_LENGTH:
        return False
    for keyword in TRIGGER_KEYWORDS:
        if keyword.lower() in text_lower:
            return True
    return False

# ══════════════════════════════════════════════════════════════════════
# 🛡  پردازش خطای هر ربات
# ══════════════════════════════════════════════════════════════════════
def handle_bot_error(error, bot_index):
    """تشخیص و لاگ دقیق خطاهای هر ربات"""
    error_name = type(error).__name__
    error_msg = str(error).lower()
    
    if isinstance(error, BadRequest):
        if "message not found" in error_msg or "message to react" in error_msg:
            log.warning(f"❌ ربات {bot_index}: پیام پیدا نشد (حذف شده؟)")
        elif "chat not found" in error_msg:
            log.warning(f"❌ ربات {bot_index}: عضو چت نیست")
        elif "reactions too much" in error_msg:
            log.warning(f"⚠️ ربات {bot_index}: Reaction محدود شده")
        else:
            log.warning(f"❌ ربات {bot_index}: BadRequest - {str(error)[:60]}")
    
    elif isinstance(error, Forbidden):
        if "bot was blocked" in error_msg:
            log.warning(f"❌ ربات {bot_index}: بلاک شده")
        elif "bot can't" in error_msg or "not authorized" in error_msg:
            log.warning(f"❌ ربات {bot_index}: دسترسی نداره (Admin کن در کانال)")
        else:
            log.warning(f"❌ ربات {bot_index}: Forbidden")
    
    elif isinstance(error, RetryAfter):
        wait_time = getattr(error, 'retry_after', 5)
        log.warning(f"⏳ ربات {bot_index}: Rate Limit، صبر {wait_time}s")
        return wait_time  # برگردوندن زمان صبر
    
    elif isinstance(error, (TimedOut, NetworkError)):
        log.warning(f"⏱ ربات {bot_index}: Timeout یا خطای شبکه")
    
    else:
        log.warning(f"❌ ربات {bot_index}: {error_name} - {str(error)[:60]}")
    
    return 0  # بدون صبر

# ══════════════════════════════════════════════════════════════════════
# 🎲  ارسال Reactionهای رندوم (بدون کرش!)
# ══════════════════════════════════════════════════════════════════════
async def send_random_reactions(chat_id, message_id):
    """
    هر ربات یه Reaction متفاوت میزنه
    اگه یکی خطا داد، بقیه ادامه میدن!
    """
    
    success = 0
    fail = 0
    skipped = 0
    
    # Shuffle Reactionها
    available_reactions = REACTIONS.copy()
    random.shuffle(available_reactions)
    
    # اگه تعداد ربات‌ها بیشتر بود، گسترش بده
    while len(available_reactions) < len(BOT_TOKENS):
        available_reactions.extend(REACTIONS)
        random.shuffle(available_reactions)
    
    for i, token in enumerate(BOT_TOKENS, 1):
        reaction = available_reactions[i - 1]
        
        try:
            # ساخت bot برای این توکن
            bot = Bot(token)
            
            # تلاش برای Reaction
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=reaction,
                is_big=False
            )
            
            success += 1
            log.info(f"✅ [{i}/{len(BOT_TOKENS)}] ربات {i} زد: {reaction}")
            
        except Exception as e:
            fail += 1
            
            # پردازش خطا و ادامه حلقه (کرش نکن!)
            wait_time = handle_bot_error(e, i)
            
            # اگه Rate Limit بود، صبر کن
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            
            # ادامه حلقه (مهم‌ترین خط!)
            continue
        
        # فاصله رندوم بین هر Reaction
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        await asyncio.sleep(delay)
    
    return success, fail

# ══════════════════════════════════════════════════════════════════════
# 🤖  هندلر اصلی
# ══════════════════════════════════════════════════════════════════════
last_reaction_time = {}

async def auto_reaction_handler(update, context):
    """هندلر خودکار با پشتیبانی از کانال و گروه"""
    
    # ── پست کانال ──
    if update.channel_post:
        message = update.channel_post
        
        if not message or not message.text:
            return
        
        if not should_trigger(message.text):
            return
        
        log.info(f"📢 [کانال] تریگر: {message.text[:40]}...")
        
        try:
            success, fail = await send_random_reactions(
                message.chat_id,
                message.message_id
            )
            log.info(f"📊 کانال: ✅ {success} موفق | ❌ {fail} ناموفق")
        except Exception as e:
            log.error(f"💥 خطای کلی کانال: {e}")
        
        return
    
    # ── پیام عادی ──
    message = update.message
    if not message or not message.text:
        return
    
    if message.chat.type not in ['group', 'supergroup']:
        return
    
    # Cooldown
    chat_id = message.chat_id
    current_time = datetime.now().timestamp()
    
    if chat_id in last_reaction_time:
        if current_time - last_reaction_time[chat_id] < COOLDOWN:
            return
    
    if not should_trigger(message.text):
        return
    
    log.info(f"👥 [گروه] تریگر: {message.text[:40]}...")
    
    last_reaction_time[chat_id] = current_time
    
    try:
        success, fail = await send_random_reactions(
            message.chat_id,
            message.message_id
        )
        log.info(f"📊 گروه: ✅ {success} موفق | ❌ {fail} ناموفق")
    except Exception as e:
        log.error(f"💥 خطای کلی گروه: {e}")

# ══════════════════════════════════════════════════════════════════════
# 🎯  شروع
# ══════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKENS:
        log.error("❌ هیچ توکنی تنظیم نشده!")
        return
    
    log.info(f"🚀 Random Reaction Bot v3 با {len(BOT_TOKENS)} ربات")
    log.info(f"🎲 {len(REACTIONS)} Reaction رندوم")
    log.info(f"⏱ Cooldown: {COOLDOWN}s | فاصله: {MIN_DELAY}-{MAX_DELAY}s")
    log.info(f"🛡 حالت ضد کرش: فعال")
    
    threading.Thread(target=run_flask, daemon=True).start()
    log.info("🌐 Flask اجرا شد")
    
    main_token = BOT_TOKENS[0]
    application = Application.builder().token(main_token).build()
    
    application.add_handler(
        MessageHandler(
            (filters.TEXT & ~filters.COMMAND) | filters.UpdateType.CHANNEL_POST,
            auto_reaction_handler
        )
    )
    
    log.info("🤖 آماده - بدون کرش با خطا!")
    application.run_polling(
        allowed_updates=["message", "channel_post"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
