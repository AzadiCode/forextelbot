#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import random
import threading
import logging
from datetime import datetime
from flask import Flask
from telegram import Bot, ReactionTypeEmoji
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
    "❤️", "🔥", "🥰"
]

TRIGGER_KEYWORDS = [
    "طلا", "gold", "قیمت", "xau", "انس",
    "عالی", "خوب", "مرسی", "ممنون", "سپاس",
    "تحلیل", "بررسی", "نظر", "چطور", "چرا",
    "تبریک", "مبارک", "فوق‌العاده", "سود", "رشد","ریکشن","ریکشنو","بکوب",
]

# ══════════════════════════════════════════════════════════════════════
# ⏱  تنظیمات
# ══════════════════════════════════════════════════════════════════════
MIN_DELAY = 10
MAX_DELAY = 60
COOLDOWN = 25
MAX_RETRIES = 2  # تعداد تلاش مجدد برای Reactionهای ناموفق

# ══════════════════════════════════════════════════════════════════════
# 🌐  Flask (Keep-Alive)
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return f"🎯 Bot OK | {len(BOT_TOKENS)} bots"

@flask_app.route('/health')
def health():
    return {"status": "ok"}

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════
# 🔍  تشخیص تریگر
# ══════════════════════════════════════════════════════════════════════
def should_trigger(text):
    if not text or len(text) < 3:
        return False
    text_lower = text.lower()
    return any(kw in text_lower for kw in TRIGGER_KEYWORDS)

# ══════════════════════════════════════════════════════════════════════
# 🚀  ارسال Reaction با Retry (تلاش مجدد برای ناموفق‌ها)
# ══════════════════════════════════════════════════════════════════════
async def try_reaction(bot, chat_id, message_id, reaction, bot_index):
    """تلاش برای Reaction با Retry در صورت شکست"""
    
    for attempt in range(MAX_RETRIES):
        try:
            # ⭐ نکته کلیدی: استفاده از ReactionTypeEmoji برای PTB v22
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=reaction)],  # ← لیست + ReactionTypeEmoji
                is_big=False
            )
            return True
        except Exception as e:
            error_msg = str(e)
            
            # اگه Rate Limit بود، صبر کن
            if "retry_after" in error_msg.lower() or "too many" in error_msg.lower():
                wait = random.uniform(3, 6)
                await asyncio.sleep(wait)
                continue
            
            # اگه خطای غیرقابل جبران بود، برگردون
            if "message_id_invalid" in error_msg.lower():
                log.warning(f"❌ ربات {bot_index}: message_id اشتباه")
                return False
            
            # تلاش مجدد بعد از صبر کوتاه
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(random.uniform(1, 2))
                continue
            
            log.warning(f"❌ ربات {bot_index}: {error_msg[:60]}")
            return False
    
    return False

async def send_all_reactions(chat_id, message_id):
    """ارسال Reaction با retry برای ناموفق‌ها"""
    
    log.info(f"🚀 شروع Reaction روی پیام {message_id}")
    
    # ── مرحله ۱: دور اول ──
    success = []
    failed = []  # لیست (index, token, reaction) برای retry
    
    for i, token in enumerate(BOT_TOKENS, 1):
        reaction = random.choice(REACTIONS)
        bot = Bot(token)
        
        if await try_reaction(bot, chat_id, message_id, reaction, i):
            success.append(i)
            log.info(f"✅ ربات {i} → {reaction}")
        else:
            failed.append((i, token, reaction))
        
        # فاصله بین Reactionها
        await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
    
    # ── مرحله ۲: Retry برای ناموفق‌ها ──
    if failed:
        log.info(f"🔄 {len(failed)} ربات ناموفق، شروع Retry...")
        await asyncio.sleep(5)  # صبر قبل از retry
        
        retry_failed = []
        for i, token, reaction in failed:
            bot = Bot(token)
            
            if await try_reaction(bot, chat_id, message_id, reaction, i):
                success.append(i)
                log.info(f"✅ ربات {i} → {reaction} (retry)")
            else:
                retry_failed.append(i)
            
            await asyncio.sleep(random.uniform(2, 4))
        
        if retry_failed:
            log.warning(f"⚠️ {len(retry_failed)} ربات در retry هم ناموفق: {retry_failed}")
    
    log.info(f"📊 پایان: ✅ {len(success)} موفق | ❌ {len(BOT_TOKENS) - len(success)} ناموفق")
    return len(success), len(BOT_TOKENS) - len(success)

# ══════════════════════════════════════════════════════════════════════
# 🤖  هندلر اصلی
# ══════════════════════════════════════════════════════════════════════
last_reaction_time = {}

async def auto_reaction_handler(update, context):
    # ── پست کانال ──
    if update.channel_post:
        message = update.channel_post
        
        if not message or not message.text:
            return
        
        if not should_trigger(message.text):
            return
        
        log.info(f"📢 [کانال] تریگر: {message.text[:40]}...")
        await asyncio.sleep(2)
        await send_all_reactions(message.chat_id, message.message_id)
        return
    
    # ── پیام گروه ──
    message = update.message
    if not message or not message.text:
        return
    
    if message.chat.type not in ['group', 'supergroup']:
        return
    
    chat_id = message.chat_id
    current_time = datetime.now().timestamp()
    
    if chat_id in last_reaction_time:
        if current_time - last_reaction_time[chat_id] < COOLDOWN:
            return
    
    if not should_trigger(message.text):
        return
    
    log.info(f"👥 [گروه] تریگر: {message.text[:40]}...")
    last_reaction_time[chat_id] = current_time
    await asyncio.sleep(2)
    await send_all_reactions(message.chat_id, message.message_id)

# ══════════════════════════════════════════════════════════════════════
# 🎯  شروع
# ══════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKENS:
        log.error("❌ هیچ توکنی تنظیم نشده!")
        return
    
    log.info(f"🚀 ربات با {len(BOT_TOKENS)} توکن | Retry: {MAX_RETRIES}x")
    
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
