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
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut
from telegram.ext import Application, MessageHandler, CommandHandler, filters

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger("RandomReactionBot")

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

# ⚠️ تنظیمات بهینه برای کانال
MIN_DELAY = 5.0   # حداقل ۵ ثانیه (قبلاً ۲ بود)
MAX_DELAY = 8.0   # حداکثر ۸ ثانیه (قبلاً ۴ بود)
COOLDOWN = 30     # ۳۰ ثانیه بین پیام‌ها (قبلاً ۱۰ بود)
INITIAL_WAIT = 3  # ۳ ثانیه صبر قبل از شروع (قبلاً ۲ بود)
MIN_MESSAGE_LENGTH = 3

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return f"🎯 Random Reaction Bot v8 (Optimized for Channels)\n\n🤖 ربات‌ها: {len(BOT_TOKENS)}\n⏱ فاصله: {MIN_DELAY}-{MAX_DELAY}s"

@flask_app.route('/health')
def health():
    return {"status": "ok", "bots": len(BOT_TOKENS)}

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def should_trigger(text):
    if not text or len(text) < MIN_MESSAGE_LENGTH:
        return False
    text_lower = text.lower()
    return any(kw in text_lower for kw in TRIGGER_KEYWORDS)

# ══════════════════════════════════════════════════════════════════════
# 🎲  ارسال Reactionها (با فاصله بیشتر برای کانال)
# ══════════════════════════════════════════════════════════════════════
async def send_all_reactions(chat_id, message_id, is_channel=False):
    """ارسال Reaction با فاصله بیشتر و مدیریت دقیق Rate Limit"""
    
    success = 0
    fail = 0
    rate_limited = 0
    
    available_reactions = REACTIONS.copy()
    random.shuffle(available_reactions)
    
    while len(available_reactions) < len(BOT_TOKENS):
        available_reactions.extend(REACTIONS)
        random.shuffle(available_reactions)
    
    chat_type = "کانال" if is_channel else "گروه"
    log.info(f"🚀 [{chat_type}] شروع Reaction برای پیام {message_id}")
    log.info(f"⏱ فاصله بین Reactionها: {MIN_DELAY}-{MAX_DELAY} ثانیه")
    
    # صبر اولیه قبل از شروع
    log.info(f"⏳ صبر اولیه {INITIAL_WAIT} ثانیه...")
    await asyncio.sleep(INITIAL_WAIT)
    
    for i, token in enumerate(BOT_TOKENS, 1):
        reaction = available_reactions[i - 1]
        
        try:
            bot = Bot(token)
            
            # تلاش برای Reaction
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=reaction,
                is_big=False
            )
            
            success += 1
            log.info(f"✅ [{i}/{len(BOT_TOKENS)}] ربات {i} → {reaction}")
            
        except RetryAfter as e:
            # Rate Limit - تلگرام گفته صبر کن
            rate_limited += 1
            wait_time = e.retry_after
            log.warning(f"⏳ [{i}] 🚫 Rate Limit! صبر {wait_time}s")
            
            # اگه Rate Limit خورد، صبر کن و بعدی رو ادامه بده
            await asyncio.sleep(wait_time)
            
        except Forbidden as e:
            fail += 1
            error_msg = str(e).lower()
            
            if is_channel and ("not authorized" in error_msg or "admin" in error_msg):
                log.error(f"❌ [{i}] 🔴 ربات {i} ادمین کانال نیست!")
            else:
                log.warning(f"❌ [{i}] Forbidden: {str(e)[:60]}")
                
        except BadRequest as e:
            fail += 1
            error_msg = str(e).lower()
            
            if "chat not found" in error_msg:
                log.warning(f"❌ [{i}] ربات عضو {chat_type} نیست")
            else:
                log.warning(f"❌ [{i}] BadRequest: {str(e)[:60]}")
                
        except Exception as e:
            fail += 1
            log.warning(f"❌ [{i}] {type(e).__name__}: {str(e)[:60]}")
        
        # فاصله رندوم بین هر Reaction (مهم برای کانال!)
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        log.debug(f"⏱ صبر {delay:.1f}s قبل از ربات بعدی...")
        await asyncio.sleep(delay)
    
    log.info(f"📊 [{chat_type}] پایان:")
    log.info(f"   ✅ موفق: {success}")
    log.info(f"   ❌ ناموفق: {fail}")
    log.info(f"   ⏳ Rate Limited: {rate_limited}")
    log.info(f"   📈 مجموع: {len(BOT_TOKENS)}")
    
    return success, fail, rate_limited

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
        
        await send_all_reactions(
            message.chat_id,
            message.message_id,
            is_channel=True
        )
        return
    
    # ── پیام عادی ──
    message = update.message
    if not message or not message.text:
        return
    
    if message.chat.type not in ['group', 'supergroup']:
        return
    
    chat_id = message.chat_id
    current_time = datetime.now().timestamp()
    
    if chat_id in last_reaction_time:
        if current_time - last_reaction_time[chat_id] < COOLDOWN:
            log.debug(f"⏱ Cooldown فعال: {int(COOLDOWN - (current_time - last_reaction_time[chat_id]))}s")
            return
    
    if not should_trigger(message.text):
        return
    
    log.info(f"👥 [گروه] تریگر: {message.text[:40]}...")
    
    last_reaction_time[chat_id] = current_time
    
    await send_all_reactions(
        message.chat_id,
        message.message_id,
        is_channel=False
    )

# ══════════════════════════════════════════════════════════════════════
# 🎯  شروع
# ══════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKENS:
        log.error("❌ هیچ توکنی تنظیم نشده!")
        return
    
    log.info(f"🚀 Random Reaction Bot v8 با {len(BOT_TOKENS)} ربات")
    log.info(f"⏱ فاصله: {MIN_DELAY}-{MAX_DELAY}s | Cooldown: {COOLDOWN}s")
    log.info(f"📢 بهینه شده برای کانال (Rate Limit کمتر)")
    
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
