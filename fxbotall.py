#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════╗
║     🎯 Reaction Booster Bot  ·  Multiple Accounts  ·  Sequential    ║
║     ربات Reaction چند اکانتی با فاصله زمانی                         ║
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
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# ══════════════════════════════════════════════════════════════════════
# ⚙️  تنظیمات
# ══════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger("ReactionBot")

# ══════════════════════════════════════════════════════════════════════
# 🔑  لیست توکن‌های ربات‌ها (۲۰ تا)
# ══════════════════════════════════════════════════════════════════════
# می‌تونی اینجا هاردکد کنی یا از Environment Variable بخونی
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

# فیلتر کردن توکن‌های خالی
BOT_TOKENS = [token for token in BOT_TOKENS if token.strip()]

# ══════════════════════════════════════════════════════════════════════
# ❤️  Reactionهای مجاز (استاندارد تلگرام)
# ══════════════════════════════════════════════════════════════════════
REACTIONS = ["👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱", "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏"]

# ══════════════════════════════════════════════════════════════════════
# ⏱  تنظیمات زمانی
# ══════════════════════════════════════════════════════════════════════
DELAY_BETWEEN_REACTIONS = 2  # ثانیه فاصله بین هر Reaction
RANDOM_DELAY = True  # فاصله رندوم بین 1 تا 3 ثانیه

# ══════════════════════════════════════════════════════════════════════
# 🌐  Flask - سیستم بیداری
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return f"""
🎯 Reaction Booster Bot is alive!

🤖 تعداد ربات‌های فعال: {len(BOT_TOKENS)}
⏰ زمان: {datetime.now().strftime('%H:%M:%S')}
"""

@flask_app.route('/health')
def health():
    return {"status": "ok", "bots": len(BOT_TOKENS)}

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════
# 🚀  تابع اصلی Reaction زدن
# ══════════════════════════════════════════════════════════════════════
async def send_reactions_sequential(chat_id, message_id, status_message=None):
    """ارسال Reaction از همه ربات‌ها به ترتیب با فاصله زمانی"""
    
    success_count = 0
    fail_count = 0
    
    for i, token in enumerate(BOT_TOKENS, 1):
        try:
            bot = Bot(token)
            reaction = random.choice(REACTIONS)
            
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=reaction,
                is_big=False
            )
            
            success_count += 1
            log.info(f"✅ [{i}/{len(BOT_TOKENS)}] Reaction {reaction} از ربات {i}")
            
            # آپدیت وضعیت (هر 5 تا)
            if status_message and i % 5 == 0:
                try:
                    await status_message.edit_text(
                        f"⏳ در حال Reaction زدن...\n\n"
                        f"✅ موفق: {success_count}\n"
                        f"❌ ناموفق: {fail_count}\n"
                        f"📊 پیشرفت: {i}/{len(BOT_TOKENS)}"
                    )
                except:
                    pass
            
            # فاصله زمانی
            if RANDOM_DELAY:
                delay = random.uniform(1, 3)
            else:
                delay = DELAY_BETWEEN_REACTIONS
            await asyncio.sleep(delay)
            
        except Exception as e:
            fail_count += 1
            log.error(f"❌ [{i}/{len(BOT_TOKENS)}] خطا در ربات {i}: {e}")
    
    return success_count, fail_count

# ══════════════════════════════════════════════════════════════════════
# 🤖  هندلرهای ربات
# ══════════════════════════════════════════════════════════════════════

async def start_handler(update, context):
    """دستور /start"""
    text = (
        f"🎯 <b>Reaction Booster Bot</b>\n\n"
        f"🤖 <b>تعداد ربات‌های فعال:</b> {len(BOT_TOKENS)}\n\n"
        f"📌 <b>نحوه استفاده:</b>\n"
        f"1️⃣ روی پیام مورد نظر Reply کن\n"
        f"2️⃣ دستور /boost رو بفرست\n\n"
        f"💡 همه ربات‌ها به ترتیب Reaction می‌زنن!"
    )
    await update.message.reply_text(text, parse_mode='HTML')

async def boost_handler(update, context):
    """دستور /boost - شروع Reaction زدن"""
    
    # بررسی Reply
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "⚠️ <b>خطا!</b>\n\n"
            "لطفاً روی پیام مورد نظر Reply کن و بعد /boost رو بزن.",
            parse_mode='HTML'
        )
        return
    
    target_message = update.message.reply_to_message
    
    # ارسال پیام وضعیت
    status_msg = await update.message.reply_text(
        f"🚀 <b>شروع Reaction زدن!</b>\n\n"
        f"🎯 هدف: پیام از {target_message.from_user.first_name}\n"
        f"🤖 تعداد ربات‌ها: {len(BOT_TOKENS)}\n"
        f"⏳ لطفاً صبر کنید...",
        parse_mode='HTML'
    )
    
    # شروع Reaction زدن
    success, fail = await send_reactions_sequential(
        target_message.chat_id,
        target_message.message_id,
        status_msg
    )
    
    # پیام نهایی
    await status_msg.edit_text(
        f"✅ <b>پایان عملیات!</b>\n\n"
        f"🎯 هدف: پیام از {target_message.from_user.first_name}\n\n"
        f"✅ <b>موفق:</b> {success}\n"
        f"❌ <b>ناموفق:</b> {fail}\n"
        f"📊 <b>مجموع:</b> {len(BOT_TOKENS)}",
        parse_mode='HTML'
    )

async def status_handler(update, context):
    """دستور /status - نمایش وضعیت"""
    text = (
        f"📊 <b>وضعیت ربات</b>\n\n"
        f"🤖 <b>تعداد ربات‌های فعال:</b> {len(BOT_TOKENS)}\n"
        f"❤️ <b>Reactionهای مجاز:</b> {len(REACTIONS)}\n"
        f"⏱ <b>فاصله زمانی:</b> {DELAY_BETWEEN_REACTIONS} ثانیه\n"
        f"🎲 <b>فاصله رندوم:</b> {'بله' if RANDOM_DELAY else 'خیر'}\n\n"
        f"⏰ <i>{datetime.now().strftime('%H:%M:%S')}</i>"
    )
    await update.message.reply_text(text, parse_mode='HTML')

# ══════════════════════════════════════════════════════════════════════
# 🎯  نقطه شروع
# ══════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKENS:
        log.error("❌ هیچ توکنی تنظیم نشده!")
        return
    
    log.info(f"🚀 Reaction Bot با {len(BOT_TOKENS)} ربات در حال راه‌اندازی...")
    
    # شروع Flask در پس‌زمینه
    threading.Thread(target=run_flask, daemon=True).start()
    log.info("🌐 Flask اجرا شد")
    
    # استفاده از توکن اول به عنوان ربات اصلی
    main_token = BOT_TOKENS[0]
    
    # ساخت Application
    application = Application.builder().token(main_token).build()
    
    # افزودن هندلرها
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", start_handler))
    application.add_handler(CommandHandler("boost", boost_handler))
    application.add_handler(CommandHandler("status", status_handler))
    
    # شروع polling
    log.info("🤖 ربات آماده است...")
    application.run_polling(
        allowed_updates=["message"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
