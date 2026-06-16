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
import re
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
        self.data = {
            "users": {},
            "total_requests": 0,
            "last_updated": None
        }
    
    def load(self):
        if not self.api_key:
            log.warning("⚠️ JSONBIN_API_KEY تنظیم نشده")
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
                log.info(f"☁️ داده‌ها بازیابی شد ({len(self.data.get('users', {}))} کاربر)")
        except Exception as e:
            log.error(f"❌ خطا در بارگذاری: {e}")
    
    def save(self):
        if not self.api_key:
            return
        self.data["last_updated"] = datetime.now().isoformat()
        try:
            if self.bin_id:
                response = requests.put(
                    f"{self.url}/{self.bin_id}",
                    headers=self.headers,
                    json=self.data,
                    timeout=15
                )
            else:
                response = requests.post(
                    self.url,
                    headers={**self.headers, "X-Bin-Private": "false"},
                    json=self.data,
                    timeout=15
                )
                new_bin_id = response.json().get("metadata", {}).get("id", "")
                if new_bin_id:
                    self.bin_id = new_bin_id
                    log.info(f"☁️ Bin جدید: {self.bin_id}")
                    log.info(f"💡 این مقدار را در Render به عنوان JSONBIN_BIN_ID تنظیم کنید")
            response.raise_for_status()
            log.info("☁️ ذخیره شد")
        except Exception as e:
            log.error(f"❌ خطا در ذخیره: {e}")
    
    def add_user(self, user_id, username=None):
        user_id_str = str(user_id)
        if user_id_str not in self.data["users"]:
            self.data["users"][user_id_str] = {
                "username": username,
                "joined_at": datetime.now().isoformat(),
                "requests": 0
            }
            log.info(f"✅ کاربر جدید: {user_id}")
    
    def increment_request(self, user_id):
        user_id_str = str(user_id)
        if user_id_str in self.data["users"]:
            self.data["users"][user_id_str]["requests"] += 1
        self.data["total_requests"] = self.data.get("total_requests", 0) + 1
    
    def get_stats(self):
        return len(self.data.get("users", {})), self.data.get("total_requests", 0)

cloud = CloudStorage()

# ══════════════════════════════════════════════════════════════════════
# 🛡  مدیریت سیگنال
# ══════════════════════════════════════════════════════════════════════
def signal_handler(signum, frame):
    log.info(f"🛡 سیگنال {signum}، ذخیره...")
    cloud.save()
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
atexit.register(cloud.save)

# ══════════════════════════════════════════════════════════════════════
# 🌐  Flask - سیستم بیداری
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    total_users, total_requests = cloud.get_stats()
    return f"""
🥇 Gold Bot is alive!
👥 کاربران: {total_users}
📊 درخواست‌ها: {total_requests}
☁️ Bin ID: {cloud.bin_id or 'Not set'}
⏰ زمان: {datetime.now().strftime('%H:%M:%S')}
"""

@flask_app.route('/health')
def health():
    total_users, total_requests = cloud.get_stats()
    return {"status": "ok", "users": total_users, "requests": total_requests}

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════
# 💰  دریافت قیمت طلای Spot (XAU/USD) - دقیقاً مطابق TradingView
# ══════════════════════════════════════════════════════════════════════

def get_gold_price():
    """
    دریافت قیمت لحظه‌ای طلای Spot (XAU/USD)
    دقیقاً همون قیمتی که در TradingView با نماد XAUUSD می‌بینی
    """
    
    # لیست منابع به ترتیب اولویت (هر کدوم کار نکرد، بعدی امتحان میشه)
    sources = [
        {
            "name": "XAU/USD Spot",
            "url": "https://query1.finance.yahoo.com/v8/finance/chart/XAUUSD=X",
            "parser": lambda data: data['chart']['result'][0]['meta']['regularMarketPrice']
        },
        {
            "name": "XAU/USD Alternative",
            "url": "https://query1.finance.yahoo.com/v8/finance/chart/XAU=X",
            "parser": lambda data: data['chart']['result'][0]['meta']['regularMarketPrice']
        }
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    for source in sources:
        try:
            response = requests.get(source["url"], headers=headers, timeout=20)
            if response.status_code == 200:
                data = response.json()
                price = source["parser"](data)
                log.info(f"✅ قیمت از {source['name']}: {price}")
                return round(price, 2)
            else:
                log.warning(f"⚠️ {source['name']} وضعیت {response.status_code}")
        except Exception as e:
            log.warning(f"⚠️ خطا از {source['name']}: {e}")
            continue
    
    log.error("❌ همه منابع ناموفق بودن")
    return None

# ══════════════════════════════════════════════════════════════════════
# ❤️  سیستم Reaction هوشمند (قوانین)
# ══════════════════════════════════════════════════════════════════════
REACTION_RULES = {
    "👍": ["طلا", "gold", "قیمت", "xau"],
    "🔥": ["عالی", "محشر", "فوق‌العاده", "💰"],
    "❤️": ["ممنون", "مرسی", "دمت گرم", "❤️", "🙏"],
    "👏": ["بررسی کن", "تحلیل", "نظر"],
    "😍": ["زیبا", "قشنگ", "💎"],
}

async def reaction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هندلر Reaction برای گروه‌ها + پاسخ کلمات کلیدی در PV"""
    message = update.message
    if not message or not message.text:
        return
    
    text = message.text.lower()
    
    # ── ۱. Reaction در گروه‌ها ──
    if message.chat.type in ['group', 'supergroup']:
        for emoji, keywords in REACTION_RULES.items():
            if any(kw.lower() in text for kw in keywords):
                try:
                    await context.bot.set_message_reaction(
                        chat_id=message.chat_id,
                        message_id=message.message_id,
                        reaction=emoji,
                        is_big=False
                    )
                    log.info(f"{emoji} Reaction روی: {message.text[:30]}")
                    break
                except Exception as e:
                    log.error(f"خطا در Reaction: {e}")
                break
        
        # اگر کاربر کلمات کلیدی طلا رو فرستاد، قیمت رو هم بفرست
        if any(kw in text for kw in ['طلا', 'gold', 'قیمت طلا']):
            await gold_handler(update, context)
        return
    
    # ── ۲. پاسخ کلمات کلیدی در PV ──
    keywords = ['طلا', 'قیمت', 'gold', 'xau', 'انس']
    if any(kw in text for kw in keywords):
        await gold_handler(update, context)
    else:
        await message.reply_text(
            "🤖 من فقط قیمت طلا رو می‌شناسم!\n\n"
            "📌 دستور /gold رو امتحان کنید."
        )

# ══════════════════════════════════════════════════════════════════════
# 🤖  هندلرهای ربات
# ══════════════════════════════════════════════════════════════════════

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    cloud.add_user(user_id, username)
    
    text = (
        "👋 <b>سلام و خوش آمدید!</b>\n\n"
        "🥇 این ربات قیمت لحظه‌ای <b>انس جهانی طلا (XAU/USD)</b> رو میاره.\n\n"
        "📌 <b>دستورات:</b>\n"
        "• /gold - دریافت قیمت لحظه‌ای\n"
        "• /stats - آمار ربات\n"
        "• /help - راهنما\n\n"
        "💡 <b>نکته:</b> ربات رو به گروه اضافه کنید تا پیام‌های مرتبط رو Reaction بذاره!"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def gold_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_chat_action('typing')
    cloud.increment_request(user_id)
    
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
            "⚠️ <b>خطا در دریافت قیمت!</b>\nلطفاً چند لحظه دیگر امتحان کنید.",
            parse_mode=ParseMode.HTML
        )

async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_users, total_requests = cloud.get_stats()
    text = (
        f"📊 <b>آمار ربات</b>\n\n"
        f"👥 <b>کاربران:</b> {total_users}\n"
        f"📈 <b>درخواست‌ها:</b> {total_requests}\n\n"
        f"☁️ <i>داده‌ها در فضای ابری</i>\n"
        f"⏰ <i>{datetime.now().strftime('%H:%M:%S')}</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ══════════════════════════════════════════════════════════════════════
# 🛡  مدیریت خطاها
# ══════════════════════════════════════════════════════════════════════
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error(f"خطا: {context.error}")

# ══════════════════════════════════════════════════════════════════════
# 🔄  ذخیره‌سازی دوره‌ای (هر ۵ دقیقه)
# ══════════════════════════════════════════════════════════════════════
async def periodic_save():
    while True:
        try:
            await asyncio.sleep(300)
            cloud.save()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"خطا در ذخیره دوره‌ای: {e}")

# ══════════════════════════════════════════════════════════════════════
# 🚀  چرخه حیات
# ══════════════════════════════════════════════════════════════════════
async def post_init(application: Application):
    threading.Thread(target=run_flask, daemon=True).start()
    log.info(f"🌐 Flask روی پورت {PORT}")
    asyncio.create_task(periodic_save())
    log.info("🔄 ذخیره دوره‌ای هر ۵ دقیقه")
    price = get_gold_price()
    if price:
        log.info(f"✅ Warm-up: {price} USD")

async def post_shutdown(application: Application):
    log.info("💾 ذخیره نهایی...")
    cloud.save()
    log.info("👋 خاموش شد")

# ══════════════════════════════════════════════════════════════════════
# 🎯  نقطه شروع
# ══════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKEN:
        log.error("❌ BOT_TOKEN تنظیم نشده!")
        return
    
    log.info("🚀 Gold Bot در حال راه‌اندازی...")
    cloud.load()
    
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    
    # دستورات
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", start_handler))
    application.add_handler(CommandHandler("gold", gold_handler))
    application.add_handler(CommandHandler("stats", stats_handler))
    
    # Reaction + کلمات کلیدی (یک هندلر یکپارچه)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, reaction_handler)
    )
    
    application.add_error_handler(error_handler)
    
    log.info("🤖 ربات آماده است...")
    application.run_polling(
        allowed_updates=["message"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
