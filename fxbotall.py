#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════╗
║     🎯 Reaction Booster Bot v2  ·  Multi-Account  ·  Smart Retry   ║
║     ربات Reaction چند اکانتی با مدیریت خطا و Retry                 ║
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
from telegram.error import BadRequest, Forbidden, RetryAfter
from telegram.ext import Application, CommandHandler, ContextTypes

# ══════════════════════════════════════════════════════════════════════
# ⚙️  تنظیمات
# ══════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger("ReactionBot")

# ══════════════════════════════════════════════════════════════════════
# 🔑  لیست توکن‌های ربات‌ها
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
# ❤️  Reactionهای مجاز
# ══════════════════════════════════════════════════════════════════════
REACTIONS = ["👍", "❤️", "🔥", "🥰", "👏", "😁", "🤔", "🎉", "🤩", "🙏"]

# ══════════════════════════════════════════════════════════════════════
# ⏱  تنظیمات زمانی (بهبود یافته)
# ══════════════════════════════════════════════════════════════════════
MIN_DELAY = 3  # حداقل فاصله (ثانیه)
MAX_DELAY = 6  # حداکثر فاصله (ثانیه)
MAX_RETRIES = 2  # تعداد تلاش مجدد

# ══════════════════════════════════════════════════════════════════════
# 🌐  Flask
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return f"""
🎯 Reaction Booster Bot v2 is alive!

🤖 تعداد ربات‌های فعال: {len(BOT_TOKENS)}
⏱ فاصله زمانی: {MIN_DELAY}-{MAX_DELAY} ثانیه
🔄 Retry: {MAX_RETRIES} بار
⏰ زمان: {datetime.now().strftime('%H:%M:%S')}
"""

@flask_app.route('/health')
def health():
    return {"status": "ok", "bots": len(BOT_TOKENS)}

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════
# 🚀  تابع اصلی Reaction با Retry و لاگ دقیق
# ══════════════════════════════════════════════════════════════════════
async def send_reactions_sequential(chat_id, message_id, status_message=None):
    """ارسال Reaction با مدیریت خطا و Retry"""
    
    success_count = 0
    fail_count = 0
    results = []  # ذخیره نتیجه هر ربات
    
    for i, token in enumerate(BOT_TOKENS, 1):
        reaction = random.choice(REACTIONS)
        success = False
        error_msg = ""
        
        # تلاش مجدد
        for attempt in range(MAX_RETRIES + 1):
            try:
                bot = Bot(token)
                
                await bot.set_message_reaction(
                    chat_id=chat_id,
                    message_id=message_id,
                    reaction=reaction,
                    is_big=False
                )
                
                success = True
                break
                
            except BadRequest as e:
                error_msg = f"BadRequest: {str(e)[:50]}"
                if "message to react not found" in str(e).lower():
                    error_msg = "پیام یافت نشد (حذف شده؟)"
                    break
                elif "chat not found" in str(e).lower():
                    error_msg = "ربات عضو گروه نیست!"
                    break
                    
            except Forbidden as e:
                error_msg = f"دسترسی نداره: {str(e)[:50]}"
                break
                
            except RetryAfter as e:
                wait_time = e.retry_after
                error_msg = f"Rate Limit: صبر {wait_time}s"
                log.warning(f"⏳ [{i}] Rate Limit، صبر {wait_time} ثانیه...")
                await asyncio.sleep(wait_time)
                
            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)[:50]}"
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(2)
        
        # ذخیره نتیجه
        if success:
            success_count += 1
            log.info(f"✅ [{i}/{len(BOT_TOKENS)}] {reaction} موفق")
            results.append(f"✅ ربات {i}: {reaction}")
        else:
            fail_count += 1
            log.error(f"❌ [{i}/{len(BOT_TOKENS)}] {error_msg}")
            results.append(f"❌ ربات {i}: {error_msg}")
        
        # آپدیت وضعیت
        if status_message and i % 3 == 0:
            try:
                await status_message.edit_text(
                    f"⏳ <b>در حال Reaction زدن...</b>\n\n"
                    f"✅ موفق: {success_count}\n"
                    f"❌ ناموفق: {fail_count}\n"
                    f"📊 پیشرفت: {i}/{len(BOT_TOKENS)}\n\n"
                    f"⏱ فاصله: {MIN_DELAY}-{MAX_DELAY}s",
                    parse_mode='HTML'
                )
            except:
                pass
        
        # فاصله رندوم
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        await asyncio.sleep(delay)
    
    return success_count, fail_count, results

# ══════════════════════════════════════════════════════════════════════
# 🤖  هندلرها
# ══════════════════════════════════════════════════════════════════════

async def start_handler(update, context):
    text = (
        f"🎯 <b>Reaction Booster Bot v2</b>\n\n"
        f"🤖 <b>ربات‌های فعال:</b> {len(BOT_TOKENS)}\n"
        f"⏱ <b>فاصله:</b> {MIN_DELAY}-{MAX_DELAY}s\n"
        f"🔄 <b>Retry:</b> {MAX_RETRIES}x\n\n"
        f"📌 <b>استفاده:</b>\n"
        f"1️⃣ Reply روی پیام\n"
        f"2️⃣ /boost\n\n"
        f"📋 /diagnose - بررسی ربات‌ها"
    )
    await update.message.reply_text(text, parse_mode='HTML')

async def boost_handler(update, context):
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "⚠️ روی پیام Reply کن و بعد /boost بزن!",
            parse_mode='HTML'
        )
        return
    
    target = update.message.reply_to_message
    
    status_msg = await update.message.reply_text(
        f"🚀 <b>شروع عملیات!</b>\n\n"
        f"🎯 هدف: {target.from_user.first_name}\n"
        f"🤖 ربات‌ها: {len(BOT_TOKENS)}\n"
        f"⏳ صبر کنید...",
        parse_mode='HTML'
    )
    
    success, fail, results = await send_reactions_sequential(
        target.chat_id,
        target.message_id,
        status_msg
    )
    
    # گزارش نهایی
    report = (
        f"✅ <b>پایان عملیات!</b>\n\n"
        f"🎯 هدف: {target.from_user.first_name}\n\n"
        f"✅ <b>موفق:</b> {success}\n"
        f"❌ <b>ناموفق:</b> {fail}\n"
        f"📊 <b>مجموع:</b> {len(BOT_TOKENS)}\n\n"
        f"📋 <b>جزئیات:</b>\n"
    )
    
    # اضافه کردن ۵ نتیجه آخر
    for result in results[-5:]:
        report += f"{result}\n"
    
    if fail > 0:
        report += f"\n💡 <b>نکته:</b> برای دیدن همه نتایج /diagnose بزن"
    
    await status_msg.edit_text(report, parse_mode='HTML')

async def diagnose_handler(update, context):
    """بررسی وضعیت همه ربات‌ها"""
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "⚠️ روی یه پیام Reply کن و /diagnose بزن تا ببینم کدوم ربات‌ها کار می‌کنن!",
            parse_mode='HTML'
        )
        return
    
    target = update.message.reply_to_message
    
    status_msg = await update.message.reply_text(
        f"🔍 <b>در حال بررسی ربات‌ها...</b>\n\n"
        f"🤖 {len(BOT_TOKENS)} ربات",
        parse_mode='HTML'
    )
    
    results = []
    working = 0
    
    for i, token in enumerate(BOT_TOKENS, 1):
        try:
            bot = Bot(token)
            
            # تست با یه Reaction ساده
            await bot.set_message_reaction(
                chat_id=target.chat_id,
                message_id=target.message_id,
                reaction="👍",
                is_big=False
            )
            
            # اگه موفق بود، Reaction رو حذف کن (با یه Reaction دیگه)
            await asyncio.sleep(0.5)
            
            results.append(f"✅ ربات {i}: کار می‌کنه")
            working += 1
            log.info(f"✅ ربات {i}: OK")
            
        except Exception as e:
            error_type = type(e).__name__
            error_detail = str(e)[:80]
            
            if "chat not found" in error_detail.lower():
                results.append(f"❌ ربات {i}: عضو گروه نیست")
            elif "not authorized" in error_detail.lower():
                results.append(f"❌ ربات {i}: دسترسی Reaction نداره")
            elif "bad request" in error_type.lower():
                results.append(f"❌ ربات {i}: BadRequest")
            else:
                results.append(f"❌ ربات {i}: {error_type}")
            
            log.error(f"❌ ربات {i}: {error_detail}")
        
        await asyncio.sleep(1)
        
        # آپدیت وضعیت
        if i % 5 == 0:
            try:
                await status_msg.edit_text(
                    f"🔍 <b>بررسی ربات‌ها...</b>\n\n"
                    f"✅ کار می‌کنه: {working}\n"
                    f"📊 پیشرفت: {i}/{len(BOT_TOKENS)}",
                    parse_mode='HTML'
                )
            except:
                pass
    
    # گزارش نهایی
    report = f"🔍 <b>نتیجه بررسی:</b>\n\n"
    report += f"✅ <b>سالم:</b> {working}/{len(BOT_TOKENS)}\n"
    report += f"❌ <b>خراب:</b> {len(BOT_TOKENS) - working}/{len(BOT_TOKENS)}\n\n"
    report += "<b>جزئیات:</b>\n"
    
    for result in results:
        report += f"{result}\n"
    
    if working < len(BOT_TOKENS):
        report += (
            f"\n💡 <b>راه‌حل:</b>\n"
            f"1️⃣ همه ربات‌ها رو به گروه اضافه کن\n"
            f"2️⃣ Privacy Mode همه رو خاموش کن (BotFather)\n"
            f"3️⃣ ربات‌ها رو حذف و دوباره اضافه کن"
        )
    
    await status_msg.edit_text(report, parse_mode='HTML')

async def status_handler(update, context):
    text = (
        f"📊 <b>وضعیت</b>\n\n"
        f"🤖 ربات‌ها: {len(BOT_TOKENS)}\n"
        f"❤️ Reactionها: {len(REACTIONS)}\n"
        f"⏱ فاصله: {MIN_DELAY}-{MAX_DELAY}s\n"
        f"🔄 Retry: {MAX_RETRIES}x\n\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S')}"
    )
    await update.message.reply_text(text, parse_mode='HTML')

# ══════════════════════════════════════════════════════════════════════
# 🎯  شروع
# ══════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKENS:
        log.error("❌ هیچ توکنی تنظیم نشده!")
        return
    
    log.info(f"🚀 Reaction Bot v2 با {len(BOT_TOKENS)} ربات")
    
    threading.Thread(target=run_flask, daemon=True).start()
    log.info("🌐 Flask اجرا شد")
    
    main_token = BOT_TOKENS[0]
    application = Application.builder().token(main_token).build()
    
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", start_handler))
    application.add_handler(CommandHandler("boost", boost_handler))
    application.add_handler(CommandHandler("diagnose", diagnose_handler))
    application.add_handler(CommandHandler("status", status_handler))
    
    log.info("🤖 آماده...")
    application.run_polling(
        allowed_updates=["message"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
