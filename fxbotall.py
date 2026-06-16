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
from telegram.error import BadRequest, Forbidden, RetryAfter
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

MIN_DELAY = 2.0
MAX_DELAY = 4.0
COOLDOWN = 15
MIN_MESSAGE_LENGTH = 3

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return f"🎯 Random Reaction Bot v11 (Debug Mode)\n\n🤖 ربات‌ها: {len(BOT_TOKENS)}"

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
# 🔧  دستور /debug - تست تک تک ربات‌ها در کانال
# ══════════════════════════════════════════════════════════════════════
async def debug_handler(update, context):
    """تست تک تک ربات‌ها با فاصله ۱۰ ثانیه"""
    
    chat = update.effective_chat
    
    if chat.type != 'channel':
        await update.message.reply_text("⚠️ این دستور فقط در کانال کار می‌کنه!")
        return
    
    # پیام reply شده یا پیام فعلی؟
    target_msg = update.message.reply_to_message or update.message
    
    status = await update.message.reply_text(
        f"🔧 <b>حالت Debug</b>\n\n"
        f"📍 کانال: {chat.title}\n"
        f"🤖 تست {len(BOT_TOKENS)} ربات با فاصله ۱۰ ثانیه...\n\n"
        f"⏳ لطفاً صبر کنید (حدود {len(BOT_TOKENS) * 10 // 60} دقیقه)",
        parse_mode='HTML'
    )
    
    results = []
    
    for i, token in enumerate(BOT_TOKENS, 1):
        reaction = random.choice(REACTIONS)
        
        try:
            bot = Bot(token)
            
            # ⚠️ مرحله ۱: اول چت رو fetch کن (اتصال به کانال)
            await bot.get_chat(chat.id)
            log.info(f"🔗 ربات {i}: اتصال به کانال موفق")
            
            # ⚠️ مرحله ۲: حالا Reaction بزن
            await bot.set_message_reaction(
                chat_id=chat.id,
                message_id=target_msg.message_id,
                reaction=reaction,
                is_big=False
            )
            
            results.append(f"✅ ربات {i}: {reaction}")
            log.info(f"✅ ربات {i}: Reaction {reaction} موفق")
            
        except Forbidden as e:
            results.append(f"❌ ربات {i}: ادمین نیست")
            log.error(f"❌ ربات {i}: Forbidden - {e}")
            
        except BadRequest as e:
            results.append(f"❌ ربات {i}: BadRequest")
            log.error(f"❌ ربات {i}: BadRequest - {e}")
            
        except RetryAfter as e:
            results.append(f"⏳ ربات {i}: Rate Limit ({e.retry_after}s)")
            log.warning(f"⏳ ربات {i}: Rate Limit - {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            
        except Exception as e:
            results.append(f"❌ ربات {i}: {type(e).__name__}")
            log.error(f"❌ ربات {i}: {type(e).__name__} - {e}")
        
        # فاصله ۱۰ ثانیه بین هر ربات
        if i < len(BOT_TOKENS):
            await asyncio.sleep(10)
        
        # آپدیت وضعیت
        if i % 5 == 0:
            await status.edit_text(
                f"🔧 <b>در حال تست...</b>\n\n"
                f"📊 پیشرفت: {i}/{len(BOT_TOKENS)}",
                parse_mode='HTML'
            )
    
    # گزارش نهایی
    report = f"🔧 <b>نتیجه Debug</b>\n\n"
    for result in results:
        report += f"{result}\n"
    
    await status.edit_text(report, parse_mode='HTML')

# ══════════════════════════════════════════════════════════════════════
# 🎲  ارسال Reactionها (با get_chat قبل از Reaction)
# ══════════════════════════════════════════════════════════════════════
async def send_all_reactions(chat_id, message_id, is_channel=False):
    """
    قبل از هر Reaction، ربات چت رو fetch می‌کنه (اتصال)
    """
    
    success = 0
    fail = 0
    
    chat_type = "کانال" if is_channel else "گروه"
    log.info(f"🚀 [{chat_type}] شروع Reaction برای پیام {message_id}")
    
    await asyncio.sleep(2)
    
    for i, token in enumerate(BOT_TOKENS, 1):
        reaction = random.choice(REACTIONS)
        
        try:
            bot = Bot(token)
            
            # ⚠️ مهم: اول چت رو fetch کن
            if is_channel:
                await bot.get_chat(chat_id)
            
            # حالا Reaction بزن
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=reaction,
                is_big=False
            )
            
            success += 1
            log.info(f"✅ [{i}/{len(BOT_TOKENS)}] ربات {i} → {reaction}")
            
        except RetryAfter as e:
            wait_time = e.retry_after
            log.warning(f"⏳ [{i}] Rate Limit! صبر {wait_time}s")
            await asyncio.sleep(wait_time)
            fail += 1
            
        except Forbidden as e:
            fail += 1
            error_msg = str(e).lower()
            
            if is_channel and ("not authorized" in error_msg or "admin" in error_msg):
                log.error(f"❌ [{i}] ربات {i} ادمین کانال نیست!")
            else:
                log.warning(f"❌ [{i}] Forbidden")
                
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
        
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        await asyncio.sleep(delay)
    
    log.info(f"📊 [{chat_type}] پایان: ✅ {success} موفق | ❌ {fail} ناموفق")
    return success, fail

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
        await send_all_reactions(message.chat_id, message.message_id, is_channel=True)
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
            return
    
    if not should_trigger(message.text):
        return
    
    log.info(f"👥 [گروه] تریگر: {message.text[:40]}...")
    last_reaction_time[chat_id] = current_time
    await send_all_reactions(message.chat_id, message.message_id, is_channel=False)

# ══════════════════════════════════════════════════════════════════════
# 🎯  شروع
# ══════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKENS:
        log.error("❌ هیچ توکنی تنظیم نشده!")
        return
    
    log.info(f"🚀 Random Reaction Bot v11 با {len(BOT_TOKENS)} ربات")
    
    threading.Thread(target=run_flask, daemon=True).start()
    
    main_token = BOT_TOKENS[0]
    application = Application.builder().token(main_token).build()
    
    # دستور debug
    application.add_handler(CommandHandler("debug", debug_handler))
    
    # هندلر خودکار
    application.add_handler(
        MessageHandler(
            (filters.TEXT & ~filters.COMMAND) | filters.UpdateType.CHANNEL_POST,
            auto_reaction_handler
        )
    )
    
    log.info("🤖 آماده! دستور /debug رو در کانال تست کن")
    application.run_polling(
        allowed_updates=["message", "channel_post"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
