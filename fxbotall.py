#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════╗
║     🎯 Random Reaction Bot  ·  Unique Reactions  ·  Multi-Account   ║
║     ربات Reaction رندوم - هر ربات Reaction متفاوت میزنه             ║
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
# 🎲  لیست کامل Reactionهای رندوم (هر ربات یکی متفاوت)
# ══════════════════════════════════════════════════════════════════════
REACTIONS = [
    "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱",
    "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "🤓", "🥱", "💯",
    "🌟", "⭐", "💫", "✨", "💥", "💢", "💤", "💦", "💨", "🕳️",
    "🎈", "🎁", "🏆", "🥇", "🥈", "🥉", "⚽", "🏀", "🏈", "⚾",
    "🍕", "🍔", "🍟", "🌭", "🍿", "🧁", "🍩", "🍪", "🎂", "🍰"
]

# ══════════════════════════════════════════════════════════════════════
# 🎯  کلمات کلیدی برای تریگر (فقط برای شروع)
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
MIN_DELAY = 1  # حداقل فاصله بین Reactionها (ثانیه)
MAX_DELAY = 3  # حداکثر فاصله
COOLDOWN = 60  # فاصله بین تریگرها (ثانیه) - جلوگیری از اسپم
MIN_MESSAGE_LENGTH = 3  # حداقل طول پیام برای تریگر

# ══════════════════════════════════════════════════════════════════════
# 🌐  Flask - سیستم بیداری
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return f"""
🎯 Random Reaction Bot is alive!

🤖 ربات‌های فعال: {len(BOT_TOKENS)}
🎲 Reactionهای موجود: {len(REACTIONS)}
🎯 کلمات تریگر: {len(TRIGGER_KEYWORDS)}
⏱ Cooldown: {COOLDOWN}s
⏰ زمان: {datetime.now().strftime('%H:%M:%S')}

💡 هر ربات Reaction متفاوت میزنه!
"""

@flask_app.route('/health')
def health():
    return {"status": "ok", "bots": len(BOT_TOKENS)}

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════
# 🔍  تشخیص کلمات کلیدی (فقط برای تریگر)
# ══════════════════════════════════════════════════════════════════════
def should_trigger(text):
    """آیا پیام شامل کلمه تریگر هست؟"""
    text_lower = text.lower()
    
    # حداقل طول پیام
    if len(text) < MIN_MESSAGE_LENGTH:
        return False
    
    # بررسی کلمات کلیدی
    for keyword in TRIGGER_KEYWORDS:
        if keyword.lower() in text_lower:
            return True
    
    return False

# ══════════════════════════════════════════════════════════════════════
# 🎲  ارسال Reactionهای رندوم و متفاوت
# ══════════════════════════════════════════════════════════════════════
async def send_random_reactions(chat_id, message_id):
    """
    هر ربات یه Reaction متفاوت و رندوم میزنه
    هیچ دو رباتی Reaction یکسان نمی‌زنن (تا جای ممکن)
    """
    
    success = 0
    fail = 0
    
    # ایجاد لیست رندوم از Reactionها (shuffle)
    available_reactions = REACTIONS.copy()
    random.shuffle(available_reactions)
    
    # اگه تعداد ربات‌ها بیشتر از Reactionها باشه، لیست رو گسترش میدیم
    while len(available_reactions) < len(BOT_TOKENS):
        available_reactions.extend(REACTIONS)
        random.shuffle(available_reactions)
    
    for i, token in enumerate(BOT_TOKENS, 1):
        try:
            bot = Bot(token)
            
            # هر ربات Reaction متفاوت میزنه
            reaction = available_reactions[i - 1]
            
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
            error_msg = str(e)[:50]
            
            # تشخیص نوع خطا
            if "not found" in error_msg.lower():
                log.warning(f"❌ [{i}] ربات عضو گروه نیست")
            elif "not authorized" in error_msg.lower():
                log.warning(f"❌ [{i}] دسترسی Reaction نداره")
            else:
                log.warning(f"❌ [{i}] خطا: {error_msg}")
        
        # فاصله رندوم بین هر Reaction
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        await asyncio.sleep(delay)
    
    return success, fail

# ══════════════════════════════════════════════════════════════════════
# 🤖  هندلر اصلی - تشخیص خودکار
# ══════════════════════════════════════════════════════════════════════

# ذخیره زمان آخرین Reaction برای هر چت
last_reaction_time = {}

async def auto_reaction_handler(update, context):
    """هندلر خودکار - تشخیص کلمات و Reaction رندوم"""
    
    # ── پشتیبانی از کانال (channel_post) ──
    if update.channel_post:
        message = update.channel_post
        
        if not message.text:
            return
        
        # تشخیص کلمه تریگر
        if not should_trigger(message.text):
            return
        
        log.info(f"📢 [کانال] تریگر: {message.text[:40]}...")
        
        # ارسال Reactionهای رندوم
        success, fail = await send_random_reactions(
            message.chat_id,
            message.message_id
        )
        
        log.info(f"📊 نتیجه کانال: ✅ {success} | ❌ {fail}")
        return
    
    # ── پیام عادی ──
    message = update.message
    if not message or not message.text:
        return
    
    # فقط در گروه‌ها (نه PV)
    if message.chat.type not in ['group', 'supergroup']:
        return
    
    # بررسی Cooldown
    chat_id = message.chat_id
    current_time = datetime.now().timestamp()
    
    if chat_id in last_reaction_time:
        time_diff = current_time - last_reaction_time[chat_id]
        if time_diff < COOLDOWN:
            return
    
    # تشخیص کلمه تریگر
    if not should_trigger(message.text):
        return
    
    log.info(f"👥 [گروه] تریگر: {message.text[:40]}...")
    
    # آپدیت زمان آخرین Reaction
    last_reaction_time[chat_id] = current_time
    
    # ارسال Reactionهای رندوم و متفاوت
    success, fail = await send_random_reactions(
        message.chat_id,
        message.message_id
    )
    
    log.info(f"📊 نتیجه گروه: ✅ {success} | ❌ {fail}")

# ══════════════════════════════════════════════════════════════════════
# 🎯  شروع
# ══════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKENS:
        log.error("❌ هیچ توکنی تنظیم نشده!")
        return
    
    log.info(f"🚀 Random Reaction Bot با {len(BOT_TOKENS)} ربات")
    log.info(f"🎲 {len(REACTIONS)} Reaction رندوم موجود")
    log.info(f"🎯 {len(TRIGGER_KEYWORDS)} کلمه تریگر")
    log.info(f"⏱ Cooldown: {COOLDOWN}s | فاصله Reaction: {MIN_DELAY}-{MAX_DELAY}s")
    
    # شروع Flask
    threading.Thread(target=run_flask, daemon=True).start()
    log.info("🌐 Flask اجرا شد")
    
    # استفاده از توکن اول
    main_token = BOT_TOKENS[0]
    
    # ساخت Application
    application = Application.builder().token(main_token).build()
    
    # هندلر خودکار - پیام‌های متنی + پست کانال
    application.add_handler(
        MessageHandler(
            (filters.TEXT & ~filters.COMMAND) | filters.UpdateType.CHANNEL_POST,
            auto_reaction_handler
        )
    )
    
    # شروع polling
    log.info("🤖 آماده - هر ربات Reaction متفاوت میزنه!")
    application.run_polling(
        allowed_updates=["message", "channel_post"],  # ← پشتیبانی از کانال
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
