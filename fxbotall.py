#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import random
import threading
import logging
from datetime import datetime
from flask import Flask
from telegram import Bot
from telegram.ext import Application, MessageHandler, filters

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger("RandomReactionBot")

# ══════════════════════════════════════════════════════════════════════
# 🔑  توکن‌ها
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
# 🎲  Reactionها و کلمات تریگر
# ══════════════════════════════════════════════════════════════════════
REACTIONS = [
    "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱",
    "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "🤓", "🥱", "💯",
    "🌟", "⭐", "💫", "✨", "💥", "💢", "🎈", "🎁", "🏆", "🥇"
]

TRIGGER_KEYWORDS = [
    "طلا", "gold", "قیمت", "xau", "انس",
    "عالی", "خوب", "مرسی", "ممنون", "سپاس",
    "تحلیل", "بررسی", "نظر", "چطور", "چرا",
    "تبریک", "مبارک", "فوق‌العاده", "سود", "رشد"
]

# ══════════════════════════════════════════════════════════════════════
# ⏱  تنظیمات زمانی
# ══════════════════════════════════════════════════════════════════════
MIN_DELAY = 2.0
MAX_DELAY = 4.0
COOLDOWN = 10
MIN_MESSAGE_LENGTH = 3

# ══════════════════════════════════════════════════════════════════════
# 🌐  Flask
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return f"🎯 Random Reaction Bot v5 (Simple & Working)\n\n🤖 ربات‌ها: {len(BOT_TOKENS)}"

@flask_app.route('/health')
def health():
    return {"status": "ok", "bots": len(BOT_TOKENS)}

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════
# 🔍  تشخیص تریگر
# ══════════════════════════════════════════════════════════════════════
def should_trigger(text):
    if not text or len(text) < MIN_MESSAGE_LENGTH:
        return False
    text_lower = text.lower()
    return any(kw in text_lower for kw in TRIGGER_KEYWORDS)

# ══════════════════════════════════════════════════════════════════════
# 🎲  ارسال Reactionها (ساده مثل نسخه /boost)
# ══════════════════════════════════════════════════════════════════════
async def send_all_reactions(chat_id, message_id):
    """دقیقاً مثل نسخه /boost - ساده و مطمئن"""
    
    success = 0
    fail = 0
    
    # Shuffle Reactionها
    available_reactions = REACTIONS.copy()
    random.shuffle(available_reactions)
    
    while len(available_reactions) < len(BOT_TOKENS):
        available_reactions.extend(REACTIONS)
        random.shuffle(available_reactions)
    
    log.info(f"🚀 شروع ارسال Reaction برای پیام {message_id}")
    
    for i, token in enumerate(BOT_TOKENS, 1):
        reaction = available_reactions[i - 1]
        
        try:
            # ساخت Bot (دقیقاً مثل نسخه /boost)
            bot = Bot(token)
            
            # ارسال Reaction
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=reaction,
                is_big=False
            )
            
            success += 1
            log.info(f"✅ [{i}/{len(BOT_TOKENS)}] ربات {i} → {reaction}")
            
        except Exception as e:
            fail += 1
            error_msg = str(e)[:80]
            
            # لاگ دقیق نوع خطا
            if "Chat not found" in error_msg:
                log.warning(f"❌ [{i}] ربات عضو گروه نیست")
            elif "Bad Request" in error_msg:
                log.warning(f"❌ [{i}] BadRequest: {error_msg}")
            elif "Forbidden" in error_msg:
                log.warning(f"❌ [{i}] دسترسی نداره")
            elif "Retry after" in error_msg.lower():
                log.warning(f"⏳ [{i}] Rate Limit: {error_msg}")
            else:
                log.warning(f"❌ [{i}] خطا: {error_msg}")
        
        # فاصله بین هر Reaction (مهم!)
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        await asyncio.sleep(delay)
    
    log.info(f"📊 پایان: ✅ {success} موفق | ❌ {fail} ناموفق")
    return success, fail

# ══════════════════════════════════════════════════════════════════════
# 🤖  هندلر اصلی
# ══════════════════════════════════════════════════════════════════════
last_reaction_time = {}

async def auto_reaction_handler(update, context):
    """هندلر خودکار"""
    
    # ── پست کانال ──
    if update.channel_post:
        message = update.channel_post
        
        if not message or not message.text:
            return
        
        if not should_trigger(message.text):
            return
        
        log.info(f"📢 [کانال] تریگر: {message.text[:40]}...")
        
        # ⚠️ صبر ۲ ثانیه برای ثبت کامل پیام در سرور تلگرام
        await asyncio.sleep(2)
        
        await send_all_reactions(message.chat_id, message.message_id)
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
    
    # ⚠️ صبر ۲ ثانیه برای ثبت کامل پیام در سرور تلگرام
    await asyncio.sleep(2)
    
    await send_all_reactions(message.chat_id, message.message_id)

# ══════════════════════════════════════════════════════════════════════
# 🎯  شروع
# ══════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKENS:
        log.error("❌ هیچ توکنی تنظیم نشده!")
        return
    
    log.info(f"🚀 Random Reaction Bot v5 با {len(BOT_TOKENS)} ربات")
    
    threading.Thread(target=run_flask, daemon=True).start()
    
    main_token = BOT_TOKENS[0]
    application = Application.builder().token(main_token).build()
    
    application.add_handler(
        MessageHandler(
            (filters.TEXT & ~filters.COMMAND) | filters.UpdateType.CHANNEL_POST,
            auto_reaction_handler
        )
    )
    
    log.info("🤖 آماده!")
    application.run_polling(
        allowed_updates=["message", "channel_post"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
