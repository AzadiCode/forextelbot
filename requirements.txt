#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import threading
import logging
import signal
import sys
import asyncio
import atexit
from datetime import datetime

import requests
from flask import Flask

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ══════════════════════════════════════════════════════════════════════
# ⚙️  تنظیمات
# ══════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger("GoldBot")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 5000))
JSONBIN_API_KEY = os.environ.get("JSONBIN_API_KEY", "")
JSONBIN_BIN_ID = os.environ.get("JSONBIN_BIN_ID", "")

# ══════════════════════════════════════════════════════════════════════
# ☁️  سیستم ذخیره‌سازی ابری (JSONBin.io)
# ══════════════════════════════════════════════════════════════════════
class CloudStorage:
    """ذخیره داده‌ها در فضای ابری با JSONBin.io"""
    
    def __init__(self):
        self.api_key = JSONBIN_API_KEY
        self.bin_id = JSONBIN_BIN_ID
        self.url = "https://api.jsonbin.io/v3/b"
        self.headers = {
            "X-Master-Key": self.api_key,
            "Content-Type": "application/json"
        }
        
        # داده‌های پیش‌فرض
        self.data = {
            "users": {},
            "total_requests": 0,
            "last_updated": None
        }
    
    def load(self):
        """بارگذاری داده‌ها از JSONBin"""
        if not self.api_key:
            log.warning("⚠️ JSONBIN_API_KEY تنظیم نشده، داده‌ها در حافظه موقت ذخیره می‌شوند")
            return
        
        if not self.bin_id:
            log.info("ℹ️ JSONBIN_BIN_ID تنظیم نشده، bin جدید ساخته می‌شود")
            return
        
        try:
            response = requests.get(
                f"{self.url}/{self.bin_id}/latest",
                headers=self.headers,
                timeout=15
            )
            response.raise_for_status()
            
            loaded_data = response.json().get("record", {})
            if loaded_data:
                self.data = loaded_data
                log.info(f"☁️ داده‌ها از ابر بازیابی شد ({len(self.data.get('users', {}))} کاربر)")
                
        except Exception as e:
            log.error(f"❌ خطا در بارگذاری از ابر: {e}")
    
    def save(self):
        """ذخیره داده‌ها به JSONBin"""
        if not self.api_key:
            return
        
        self.data["last_updated"] = datetime.now().isoformat()
        
        try:
            if self.bin_id:
                # آپدیت bin موجود
                response = requests.put(
                    f"{self.url}/{self.bin_id}",
                    headers=self.headers,
                    json=self.data,
                    timeout=15
                )
            else:
                # ساخت bin جدید
                response = requests.post(
                    self.url,
                    headers={**self.headers, "X-Bin-Private": "false"},
                    json=self.data,
                    timeout=15
                )
                # ذخیره bin_id جدید
                new_bin_id = response.json().get("metadata", {}).get("id", "")
                if new_bin_id:
                    self.bin_id = new_bin_id
                    log.info(f"☁️ Bin جدید ساخته شد: {self.bin_id}")
                    log.info(f"💡 این مقدار را در Render به عنوان JSONBIN_BIN_ID تنظیم کنید")
            
            response.raise_for_status()
            log.info("☁️ داده‌ها در ابر ذخیره شد")
            
        except Exception as e:
            log.error(f"❌ خطا در ذخیره به ابر: {e}")
    
    def add_user(self, user_id, username=None):
        """افزودن کاربر جدید"""
        user_id_str = str(user_id)
        if user_id_str not in self.data["users"]:
            self.data["users"][user_id_str] = {
                "username": username,
                "joined_at": datetime.now().isoformat(),
                "requests": 0
            }
            log.info(f"✅ کاربر جدید: {user_id}")
    
    def increment_request(self, user_id):
        """افزایش شمارنده درخواست"""
        user_id_str = str(user_id)
        if user_id_str in self.data["users"]:
            self.data["users"][user_id_str]["requests"] += 1
        self.data["total_requests"] = self.data.get("total_requests", 0) + 1
    
    def get_stats(self):
        """دریافت آمار"""
        total_users = len(self.data.get("users", {}))
        total_requests = self.data.get("total_requests", 0)
        return total_users, total_requests

# نمونه سراسری
cloud = CloudStorage()

# ══════════════════════════════════════════════════════════════════════
# 🛡  مدیریت سیگنال و خروج (برای ذخیره قبل از خاموش شدن)
# ══════════════════════════════════════════════════════════════════════
def signal_handler(signum, frame):
    """ذخیره داده‌ها قبل از خاموش شدن اجباری"""
    log.info(f"🛡 دریافت سیگنال {signum}، در حال ذخیره داده‌ها...")
    cloud.save()
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
atexit.register(cloud.save)

# ══════════════════════════════════════════════════════════════════════
# 🌐  Flask - سیستم بیداری دائمی
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    total_users, total_requests = cloud.get_stats()
    last_update = cloud.data.get("last_updated", "N/A")
    return f"""
    🥇 Gold Bot is alive!
    
    👥 کاربران: {total_users}
    📊 درخواست‌ها: {total_requests}
    ☁️ Bin ID: {cloud.bin_id or 'Not set'}
    ⏰ آخرین ذخیره: {last_update}
    ⏱ زمان: {datetime.now().strftime('%H:%M:%S')}
    """

@flask_app.route('/health')
def health():
    total_users, total_requests = cloud.get_stats()
    return {
        "status": "ok",
        "bot": "running",
        "users": total_users,
        "requests": total_requests,
        "bin_id": cloud.bin_id
    }

def run_flask():
    """اجرای Flask در پس‌زمینه"""
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════
# 💰  دریافت قیمت طلا (زنده و بدون کش)
# ══════════════════════════════════════════════════════════════════════
def get_gold_price():
    """دریافت قیمت لحظه‌ای انس طلا"""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        response = requests.get(url, headers=headers, timeout=20)
        
        if response.status_code == 200:
            data = response.json()
            price = data['chart']['result'][0]['meta']['regularMarketPrice']
            return round(price, 2)
        else:
            log.error(f"خطای HTTP: {response.status_code}")
            
    except Exception as e:
        log.error(f"خطا در دریافت قیمت: {e}")
    
    return None

# ══════════════════════════════════════════════════════════════════════
# 🤖  هندلرهای ربات
# ══════════════════════════════════════════════════════════════════════

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور /start و /help"""
    user_id = update.effective_user.id
    username = update.effective_user.username
    
    # افزودن کاربر به ابر
    cloud.add_user(user_id, username)
    
    text = (
        "👋 <b>سلام و خوش آمدید!</b>\n\n"
        "🥇 این ربات قیمت لحظه‌ای <b>انس جهانی طلا (XAU/USD)</b> رو براتون میاره.\n\n"
        "📌 <b>دستورات:</b>\n"
        "• /gold - دریافت قیمت لحظه‌ای\n"
        "• /stats - آمار ربات\n"
        "• /help - راهنما\n\n"
        "💡 <b>نکته:</b> می‌تونید کلمات «طلا»، «قیمت» یا «gold» رو هم بفرستید!"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def gold_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور /gold - دریافت قیمت لحظه‌ای"""
    user_id = update.effective_user.id
    
    await update.message.reply_chat_action('typing')
    
    # افزایش شمارنده
    cloud.increment_request(user_id)
    
    # دریافت قیمت زنده
    price = get_gold_price()
    
    if price:
        text = (
            f"🥇 <b>قیمت لحظه‌ای انس جهانی طلا (XAU/USD)</b>\n\n"
            f"💰 <b>{price}</b> دلار\n\n"
            f"⏱ <i>بروزرسانی: {datetime.now().strftime('%H:%M:%S')}</i>"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(
            "⚠️ <b>خطا در دریافت قیمت!</b>\n\n"
            "لطفاً چند لحظه دیگر دوباره امتحان کنید.",
            parse_mode=ParseMode.HTML
        )

async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور /stats - نمایش آمار"""
    total_users, total_requests = cloud.get_stats()
    
    text = (
        f"📊 <b>آمار ربات</b>\n\n"
        f"👥 <b>تعداد کاربران:</b> {total_users}\n"
        f"📈 <b>تعداد درخواست‌ها:</b> {total_requests}\n\n"
        f"☁️ <i>داده‌ها در فضای ابری ذخیره می‌شوند</i>\n"
        f"⏰ <i>زمان: {datetime.now().strftime('%H:%M:%S')}</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def echo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پاسخ به کلمات کلیدی"""
    user_id = update.effective_user.id
    username = update.effective_user.username
    
    cloud.add_user(user_id, username)
    
    text = update.message.text.lower()
    keywords = ['طلا', 'قیمت', 'gold', 'xau', 'انس']
    
    if any(keyword in text for keyword in keywords):
        await gold_handler(update, context)
    else:
        await update.message.reply_text(
            "🤖 من فقط قیمت طلا رو می‌شناسم!\n\n"
            "📌 دستور /gold رو امتحان کنید."
        )

# ══════════════════════════════════════════════════════════════════════
# 🛡  مدیریت خطاها
# ══════════════════════════════════════════════════════════════════════

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت خطاها"""
    log.error(f"خطا: {context.error}")

# ══════════════════════════════════════════════════════════════════════
# 🔄  ذخیره‌سازی دوره‌ای (هر ۵ دقیقه)
# ══════════════════════════════════════════════════════════════════════

async def periodic_save(application: Application):
    """ذخیره خودکار هر ۵ دقیقه"""
    while True:
        try:
            await asyncio.sleep(300)  # ۵ دقیقه
            log.info("💾 ذخیره‌سازی دوره‌ای...")
            cloud.save()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"خطا در ذخیره دوره‌ای: {e}")

# ══════════════════════════════════════════════════════════════════════
# 🚀  چرخه حیات
# ══════════════════════════════════════════════════════════════════════

async def post_init(application: Application):
    """اجرا بعد از آماده شدن ربات"""
    # شروع Flask
    threading.Thread(target=run_flask, daemon=True).start()
    log.info(f"🌐 Flask روی پورت {PORT} اجرا شد")
    
    # شروع ذخیره‌سازی دوره‌ای
    asyncio.create_task(periodic_save(application))
    log.info("🔄 ذخیره‌سازی دوره‌ای هر ۵ دقیقه فعال شد")
    
    # تست دریافت قیمت
    log.info("🔥 تست دریافت قیمت...")
    price = get_gold_price()
    if price:
        log.info(f"✅ موفق: {price} USD")

async def post_shutdown(application: Application):
    """اجرا قبل از خاموش شدن"""
    log.info("💾 ذخیره نهایی داده‌ها...")
    cloud.save()
    log.info("👋 ربات خاموش شد")

# ══════════════════════════════════════════════════════════════════════
# ❤️  سیستم لایک هوشمند با قوانین سفارشی
# ══════════════════════════════════════════════════════════════════════

# قوانین لایک
REACTION_RULES = {
    "👍": ["طلا", "gold", "قیمت", "xau"],
    "🔥": ["عالی", "محشر", "فوق‌العاده", "🔥", "💰"],
    "❤️": ["ممنون", "مرسی", "دمت گرم", "❤️", "🙏"],
    "👏": ["بررسی کن", "تحلیل", "نظر"],
    "😍": ["زیبا", "قشنگ", "💎"],
}

async def smart_reaction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لایک هوشمند بر اساس قوانین"""
    message = update.message
    
    # فقط در گروه‌ها
    if message.chat.type not in ['group', 'supergroup']:
        return
    
    text = (message.text or "").lower()
    
    # بررسی هر قانون
    for emoji, keywords in REACTION_RULES.items():
        if any(keyword.lower() in text for keyword in keywords):
            try:
                await context.bot.set_message_reaction(
                    chat_id=message.chat_id,
                    message_id=message.message_id,
                    reaction=emoji,
                    is_big=False  # False = لایک کوچک، True = لایک بزرگ
                )
                log.info(f"{emoji} لایک شد: {message.text[:40]}...")
                break  # فقط یک لایک برای هر پیام
                
            except Exception as e:
                log.error(f"خطا در لایک با {emoji}: {e}")

# ══════════════════════════════════════════════════════════════════════
# 🎯  نقطه شروع
# ══════════════════════════════════════════════════════════════════════

def main():
    """تابع اصلی"""
    if not BOT_TOKEN:
        log.error("❌ BOT_TOKEN تنظیم نشده!")
        return
    
    log.info("🚀 Gold Bot در حال راه‌اندازی...")
    
    # بارگذاری داده‌ها از ابر
    log.info("☁️ بارگذاری داده‌ها از ابر...")
    cloud.load()
    
    # ساخت Application
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    
    # افزودن هندلرها
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", start_handler))
    application.add_handler(CommandHandler("gold", gold_handler))
    application.add_handler(CommandHandler("stats", stats_handler))
    # 🆕 هندلر لایک خودکار (قبل از echo_handler اضافه کن)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, auto_reaction_handler)
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_handler))
    
    # مدیریت خطاها
    application.add_error_handler(error_handler)
    
    # شروع polling
    log.info("🤖 ربات آماده است...")
    application.run_polling(
        allowed_updates=["message"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
