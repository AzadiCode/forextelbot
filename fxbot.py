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
import io
from datetime import datetime, timedelta

import requests
from flask import Flask
import matplotlib
matplotlib.use('Agg')  # بدون نیاز به GUI
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
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
log = logging.getLogger("ForexBot")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 5000))
JSONBIN_API_KEY = os.environ.get("JSONBIN_API_KEY", "")
JSONBIN_BIN_ID = os.environ.get("JSONBIN_BIN_ID", "")

# ══════════════════════════════════════════════════════════════════════
# 💱  تنظیمات دارایی‌های قابل معامله
# ══════════════════════════════════════════════════════════════════════
ASSETS = {
    "XAUUSD": {
        "name": "انس جهانی طلا",
        "emoji": "🥇",
        "symbol": "XAU/USD",
        "sources": [
            {
                "name": "GoldPrice.org",
                "url": "https://data-asg.goldprice.org/dbXRates/USD",
                "parser": lambda d: d['items'][0]['xauPrice'],
                "min": 1000, "max": 10000
            },
            {
                "name": "Swissquote",
                "url": "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD",
                "parser": lambda d: (float(d[0]['spreadProfilePrices'][0]['ask']) + float(d[0]['spreadProfilePrices'][0]['bid'])) / 2,
                "min": 1000, "max": 10000
            },
            {
                "name": "Yahoo Finance",
                "url": "https://query1.finance.yahoo.com/v8/finance/chart/XAUUSD=X",
                "parser": lambda d: d['chart']['result'][0]['meta']['regularMarketPrice'],
                "min": 1000, "max": 10000
            }
        ]
    },
    "EURUSD": {
        "name": "یورو به دلار",
        "emoji": "💶",
        "symbol": "EUR/USD",
        "sources": [
            {
                "name": "Swissquote",
                "url": "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/EUR/USD",
                "parser": lambda d: (float(d[0]['spreadProfilePrices'][0]['ask']) + float(d[0]['spreadProfilePrices'][0]['bid'])) / 2,
                "min": 0.8, "max": 1.5
            },
            {
                "name": "Yahoo Finance",
                "url": "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X",
                "parser": lambda d: d['chart']['result'][0]['meta']['regularMarketPrice'],
                "min": 0.8, "max": 1.5
            }
        ]
    },
    "USDJPY": {
        "name": "ین ژاپن به دلار",
        "emoji": "💴",
        "symbol": "USD/JPY",
        "sources": [
            {
                "name": "Swissquote",
                "url": "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/USD/JPY",
                "parser": lambda d: (float(d[0]['spreadProfilePrices'][0]['ask']) + float(d[0]['spreadProfilePrices'][0]['bid'])) / 2,
                "min": 100, "max": 200
            },
            {
                "name": "Yahoo Finance",
                "url": "https://query1.finance.yahoo.com/v8/finance/chart/USDJPY=X",
                "parser": lambda d: d['chart']['result'][0]['meta']['regularMarketPrice'],
                "min": 100, "max": 200
            }
        ]
    }
}

# ══════════════════════════════════════════════════════════════════════
# ☁️  سیستم ذخیره‌سازی ابری (JSONBin.io)
# ══════════════════════════════════════════════════════════════════════
class CloudStorage:
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
            "price_history": {"XAUUSD": [], "EURUSD": [], "USDJPY": []},
            "alerts": {},
            "last_updated": None
        }
        self._dirty = False
    
    def load(self):
        if not self.api_key:
            return
        if not self.bin_id:
            return
        try:
            response = requests.get(
                f"{self.url}/{self.bin_id}/latest",
                headers=self.headers, timeout=15
            )
            response.raise_for_status()
            loaded = response.json().get("record", {})
            if loaded:
                # merge با داده‌های پیش‌فرض
                for k, v in loaded.items():
                    self.data[k] = v
                # اطمینان از وجود همه فیلدها
                if "price_history" not in self.data:
                    self.data["price_history"] = {"XAUUSD": [], "EURUSD": [], "USDJPY": []}
                if "alerts" not in self.data:
                    self.data["alerts"] = {}
                for sym in ["XAUUSD", "EURUSD", "USDJPY"]:
                    if sym not in self.data["price_history"]:
                        self.data["price_history"][sym] = []
                log.info(f"☁️ داده‌ها بازیابی شد")
        except Exception as e:
            log.error(f"❌ خطا در بارگذاری: {e}")
    
    def save(self, force=False):
        if not self.api_key:
            return
        if not force and not self._dirty:
            return
        self.data["last_updated"] = datetime.now().isoformat()
        try:
            if self.bin_id:
                response = requests.put(
                    f"{self.url}/{self.bin_id}",
                    headers=self.headers,
                    json=self.data, timeout=15
                )
            else:
                response = requests.post(
                    self.url,
                    headers={**self.headers, "X-Bin-Private": "false"},
                    json=self.data, timeout=15
                )
                new_bin_id = response.json().get("metadata", {}).get("id", "")
                if new_bin_id:
                    self.bin_id = new_bin_id
                    log.info(f"☁️ Bin جدید: {self.bin_id}")
            response.raise_for_status()
            self._dirty = False
        except Exception as e:
            log.error(f"❌ خطا در ذخیره: {e}")
    
    def add_user(self, user_id, username=None):
        uid = str(user_id)
        if uid not in self.data["users"]:
            self.data["users"][uid] = {
                "username": username,
                "joined_at": datetime.now().isoformat(),
                "requests": 0
            }
            self._dirty = True
    
    def increment_request(self, user_id):
        uid = str(user_id)
        if uid in self.data["users"]:
            self.data["users"][uid]["requests"] += 1
        self.data["total_requests"] = self.data.get("total_requests", 0) + 1
        self._dirty = True
    
    def add_price(self, symbol, price):
        """ذخیره قیمت در تاریخچه (حداکثر ۱۰۰ مورد)"""
        if symbol not in self.data["price_history"]:
            self.data["price_history"][symbol] = []
        self.data["price_history"][symbol].append({
            "price": price,
            "time": datetime.now().isoformat()
        })
        # محدود کردن به ۱۰۰ رکورد
        if len(self.data["price_history"][symbol]) > 100:
            self.data["price_history"][symbol] = self.data["price_history"][symbol][-100:]
        self._dirty = True
    
    def set_alert(self, user_id, symbol, direction, target_price):
        """تنظیم هشدار"""
        uid = str(user_id)
        if uid not in self.data["alerts"]:
            self.data["alerts"][uid] = {}
        if symbol not in self.data["alerts"][uid]:
            self.data["alerts"][uid][symbol] = []
        
        alert = {
            "direction": direction,  # "up" یا "down"
            "target": target_price,
            "created_at": datetime.now().isoformat(),
            "triggered": False
        }
        self.data["alerts"][uid][symbol].append(alert)
        self._dirty = True
    
    def get_alerts(self, user_id, symbol=None):
        uid = str(user_id)
        if uid not in self.data["alerts"]:
            return []
        if symbol:
            return self.data["alerts"][uid].get(symbol, [])
        return self.data["alerts"][uid]
    
    def mark_alert_triggered(self, user_id, symbol, alert_index):
        uid = str(user_id)
        try:
            self.data["alerts"][uid][symbol][alert_index]["triggered"] = True
            self._dirty = True
        except:
            pass
    
    def remove_alert(self, user_id, symbol, alert_index):
        uid = str(user_id)
        try:
            del self.data["alerts"][uid][symbol][alert_index]
            self._dirty = True
        except:
            pass
    
    def get_stats(self):
        return len(self.data.get("users", {})), self.data.get("total_requests", 0)

cloud = CloudStorage()

# ══════════════════════════════════════════════════════════════════════
# 🛡  مدیریت سیگنال
# ══════════════════════════════════════════════════════════════════════
def signal_handler(signum, frame):
    log.info(f"🛡 سیگنال {signum}، ذخیره...")
    cloud.save(force=True)
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
atexit.register(lambda: cloud.save(force=True))

# ══════════════════════════════════════════════════════════════════════
# 🌐  Flask - سیستم بیداری
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    total_users, total_requests = cloud.get_stats()
    return f"""
💱 Forex Bot is alive!
👥 کاربران: {total_users}
📊 درخواست‌ها: {total_requests}
⏰ زمان: {datetime.now().strftime('%H:%M:%S')}
"""

@flask_app.route('/health')
def health():
    return {"status": "ok"}

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════
# 💰  دریافت قیمت (چند منبعی - همه دارایی‌ها)
# ══════════════════════════════════════════════════════════════════════
def get_price(symbol):
    """دریافت قیمت دارایی با fallback چند منبعی"""
    if symbol not in ASSETS:
        return None
    
    asset = ASSETS[symbol]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
    }
    
    for source in asset["sources"]:
        try:
            response = requests.get(source["url"], headers=headers, timeout=15)
            if response.status_code == 200:
                data = response.json()
                price = source["parser"](data)
                
                if source["min"] < price < source["max"]:
                    log.info(f"✅ {symbol} از {source['name']}: {price}")
                    return round(price, 4 if symbol != "XAUUSD" else 2)
        except Exception as e:
            log.warning(f"⚠️ {symbol}/{source['name']}: {str(e)[:60]}")
            continue
    
    log.error(f"❌ {symbol}: همه منابع ناموفق")
    return None

# ══════════════════════════════════════════════════════════════════════
# 📊  رسم نمودار قیمت
# ══════════════════════════════════════════════════════════════════════
def create_price_chart(symbol):
    """رسم نمودار قیمت و برگردوندن به صورت bytes"""
    history = cloud.data.get("price_history", {}).get(symbol, [])
    
    if len(history) < 2:
        return None
    
    asset = ASSETS[symbol]
    
    # استخراج داده‌ها
    times = []
    prices = []
    for item in history[-50:]:  # ۵۰ مورد آخر
        try:
            t = datetime.fromisoformat(item["time"])
            times.append(t)
            prices.append(item["price"])
        except:
            continue
    
    if len(times) < 2:
        return None
    
    # تنظیم استایل نمودار
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 6))
    
    # رنگ بر اساس روند
    color = '#00ff88' if prices[-1] >= prices[0] else '#ff4444'
    
    # رسم خط
    ax.plot(times, prices, color=color, linewidth=2, marker='o', markersize=4)
    ax.fill_between(times, prices, alpha=0.2, color=color)
    
    # عنوان و برچسب‌ها
    ax.set_title(f"{asset['emoji']} {asset['name']} ({asset['symbol']})", 
                 fontsize=16, color='white', pad=15)
    ax.set_xlabel("زمان", fontsize=11, color='white')
    ax.set_ylabel("قیمت", fontsize=11, color='white')
    
    # فرمت محور X
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    plt.xticks(rotation=45)
    
    # خطوط grid
    ax.grid(True, alpha=0.3, linestyle='--', color='gray')
    
    # اضافه کردن قیمت فعلی
    current_price = prices[-1]
    ax.axhline(y=current_price, color=color, linestyle='--', alpha=0.5)
    ax.text(times[-1], current_price, f'  {current_price}', 
            color=color, fontsize=11, va='center')
    
    # min/max
    min_price = min(prices)
    max_price = max(prices)
    ax.text(times[0], min_price, f' Min: {min_price}', 
            color='#ffaa00', fontsize=9, va='bottom')
    ax.text(times[0], max_price, f' Max: {max_price}', 
            color='#ffaa00', fontsize=9, va='top')
    
    plt.tight_layout()
    
    # تبدیل به bytes
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    buf.seek(0)
    plt.close()
    
    return buf

# ══════════════════════════════════════════════════════════════════════
# 🔔  سیستم هشدار
# ══════════════════════════════════════════════════════════════════════
async def check_alerts(application):
    """بررسی همه هشدارها و ارسال نوتیفیکیشن"""
    try:
        for uid, alerts in list(cloud.data.get("alerts", {}).items()):
            for symbol, alert_list in list(alerts.items()):
                if not alert_list:
                    continue
                
                price = get_price(symbol)
                if not price:
                    continue
                
                asset = ASSETS[symbol]
                
                for idx, alert in enumerate(alert_list):
                    if alert.get("triggered"):
                        continue
                    
                    target = alert["target"]
                    direction = alert["direction"]
                    
                    triggered = False
                    if direction == "up" and price >= target:
                        triggered = True
                        emoji = "📈"
                        msg = "بالا رفت"
                    elif direction == "down" and price <= target:
                        triggered = True
                        emoji = "📉"
                        msg = "پایین آمد"
                    
                    if triggered:
                        try:
                            await application.bot.send_message(
                                chat_id=int(uid),
                                text=(
                                    f"{emoji} <b>هشدار قیمت!</b>\n\n"
                                    f"{asset['emoji']} <b>{asset['name']}</b>\n\n"
                                    f"💰 قیمت فعلی: <b>{price}</b>\n"
                                    f"🎯 هدف: <b>{target}</b>\n\n"
                                    f"✅ قیمت به هدف شما {msg}!"
                                ),
                                parse_mode=ParseMode.HTML
                            )
                            cloud.mark_alert_triggered(uid, symbol, idx)
                            log.info(f"🔔 هشدار فعال شد برای کاربر {uid}: {symbol} → {price}")
                        except Exception as e:
                            log.error(f"خطا در ارسال هشدار: {e}")
    except Exception as e:
        log.error(f"خطا در بررسی هشدارها: {e")

# ══════════════════════════════════════════════════════════════════════
# ⌨️  کیبوردهای Inline
# ══════════════════════════════════════════════════════════════════════
def main_menu_keyboard():
    """منوی اصلی"""
    keyboard = [
        [
            InlineKeyboardButton("🥇 طلا", callback_data="asset:XAUUSD"),
            InlineKeyboardButton("💶 یورو", callback_data="asset:EURUSD"),
            InlineKeyboardButton("💴 ین", callback_data="asset:USDJPY")
        ],
        [
            InlineKeyboardButton("📊 همه قیمت‌ها", callback_data="all_prices")
        ],
        [
            InlineKeyboardButton("🔔 هشدارهای من", callback_data="my_alerts"),
            InlineKeyboardButton("📈 نمودار", callback_data="chart_menu")
        ],
        [
            InlineKeyboardButton("📊 آمار", callback_data="stats"),
            InlineKeyboardButton("❓ راهنما", callback_data="help")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def asset_keyboard(symbol):
    """منوی یک دارایی"""
    keyboard = [
        [InlineKeyboardButton("💰 قیمت لحظه‌ای", callback_data=f"price:{symbol}")],
        [InlineKeyboardButton("📈 نمودار", callback_data=f"chart:{symbol}")],
        [
            InlineKeyboardButton("⬆️ هشدار صعودی", callback_data=f"alert_up:{symbol}"),
            InlineKeyboardButton("⬇️ هشدار نزولی", callback_data=f"alert_down:{symbol}")
        ],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def chart_menu_keyboard():
    """منوی انتخاب نمودار"""
    keyboard = [
        [InlineKeyboardButton("🥇 نمودار طلا", callback_data="chart:XAUUSD")],
        [InlineKeyboardButton("💶 نمودار یورو", callback_data="chart:EURUSD")],
        [InlineKeyboardButton("💴 نمودار ین", callback_data="chart:USDJPY")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ══════════════════════════════════════════════════════════════════════
# 🤖  هندلرها
# ══════════════════════════════════════════════════════════════════════

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    cloud.add_user(user_id, username)
    
    text = (
        "👋 <b>سلام و خوش آمدید!</b>\n\n"
        "💱 این ربات قیمت لحظه‌ای ارزها و طلا رو با دقت بالا نشون میده.\n\n"
        "📌 <b>دارایی‌های پشتیبانی شده:</b>\n"
        "• 🥇 انس جهانی طلا (XAU/USD)\n"
        "• 💶 یورو به دلار (EUR/USD)\n"
        "• 💴 ین ژاپن به دلار (USD/JPY)\n\n"
        "💡 <b>از منوی زیر شروع کنید:</b>"
    )
    await update.message.reply_text(
        text, 
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard()
    )

async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور /price برای نمایش همه قیمت‌ها"""
    await show_all_prices(update, context)

async def show_all_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش همه قیمت‌ها"""
    user_id = update.effective_user.id
    cloud.increment_request(user_id)
    
    text = "💱 <b>قیمت‌های لحظه‌ای</b>\n\n"
    
    for symbol, asset in ASSETS.items():
        price = get_price(symbol)
        if price:
            # ذخیره در تاریخچه
            cloud.add_price(symbol, price)
            text += f"{asset['emoji']} <b>{asset['name']}</b>\n"
            text += f"   💰 <code>{price}</code>\n\n"
        else:
            text += f"{asset['emoji']} <b>{asset['name']}</b>\n"
            text += f"   ⚠️ <i>خطا در دریافت</i>\n\n"
    
    text += f"⏱ <i>{datetime.now().strftime('%H:%M:%S')}</i>"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, 
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت همه Callback ها"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = update.effective_user.id
    cloud.increment_request(user_id)
    
    # ── منوی اصلی ──
    if data == "main_menu":
        await query.edit_message_text(
            "🏠 <b>منوی اصلی</b>\n\nیک گزینه را انتخاب کنید:",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )
        return
    
    # ── نمایش همه قیمت‌ها ──
    if data == "all_prices":
        await show_all_prices(update, context)
        return
    
    # ── آمار ──
    if data == "stats":
        total_users, total_requests = cloud.get_stats()
        text = (
            f"📊 <b>آمار ربات</b>\n\n"
            f"👥 <b>کاربران:</b> {total_users}\n"
            f"📈 <b>درخواست‌ها:</b> {total_requests}\n\n"
            f"⏰ <i>{datetime.now().strftime('%H:%M:%S')}</i>"
        )
        await query.edit_message_text(
            text, 
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )
        return
    
    # ── راهنما ──
    if data == "help":
        text = (
            "❓ <b>راهنما</b>\n\n"
            "📌 <b>دستورات:</b>\n"
            "• /start - منوی اصلی\n"
            "• /price - همه قیمت‌ها\n"
            "• /chart - نمودار طلا\n"
            "• /alerts - هشدارهای من\n\n"
            "💡 <b>قابلیت‌ها:</b>\n"
            "• قیمت لحظه‌ای از ۳ منبع معتبر\n"
            "• نمودار تغییرات قیمت\n"
            "• هشدار قیمت هدف\n"
            "• Reaction خودکار در گروه‌ها"
        )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )
        return
    
    # ── منوی نمودار ──
    if data == "chart_menu":
        await query.edit_message_text(
            "📈 <b>انتخاب نمودار</b>\n\nیک دارایی را انتخاب کنید:",
            parse_mode=ParseMode.HTML,
            reply_markup=chart_menu_keyboard()
        )
        return
    
    # ── منوی یک دارایی ──
    if data.startswith("asset:"):
        symbol = data.split(":")[1]
        asset = ASSETS[symbol]
        price = get_price(symbol)
        
        if price:
            cloud.add_price(symbol, price)
            text = (
                f"{asset['emoji']} <b>{asset['name']}</b>\n\n"
                f"💰 <b>قیمت:</b> <code>{price}</code>\n"
                f"⏱ <i>{datetime.now().strftime('%H:%M:%S')}</i>\n\n"
                "یک گزینه را انتخاب کنید:"
            )
        else:
            text = (
                f"{asset['emoji']} <b>{asset['name']}</b>\n\n"
                f"⚠️ <i>خطا در دریافت قیمت</i>\n\n"
                "یک گزینه را انتخاب کنید:"
            )
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=asset_keyboard(symbol)
        )
        return
    
    # ── قیمت لحظه‌ای یک دارایی ──
    if data.startswith("price:"):
        symbol = data.split(":")[1]
        asset = ASSETS[symbol]
        price = get_price(symbol)
        
        if price:
            cloud.add_price(symbol, price)
            text = (
                f"{asset['emoji']} <b>{asset['name']}</b>\n\n"
                f"💰 <b>قیمت:</b> <code>{price}</code>\n"
                f"⏱ <i>{datetime.now().strftime('%H:%M:%S')}</i>"
            )
        else:
            text = f"{asset['emoji']} <b>{asset['name']}</b>\n\n⚠️ خطا در دریافت قیمت"
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=asset_keyboard(symbol)
        )
        return
    
    # ── نمودار یک دارایی ──
    if data.startswith("chart:"):
        symbol = data.split(":")[1]
        asset = ASSETS[symbol]
        
        # اگه داده کافی نیست، اول قیمت رو بگیر
        history = cloud.data.get("price_history", {}).get(symbol, [])
        if len(history) < 2:
            price = get_price(symbol)
            if price:
                cloud.add_price(symbol, price)
        
        chart_buf = create_price_chart(symbol)
        
        if chart_buf:
            try:
                await query.edit_message_text("📈 در حال ارسال نمودار...")
                await query.message.reply_photo(
                    photo=chart_buf,
                    caption=f"{asset['emoji']} <b>نمودار {asset['name']}</b>\n\n⏱ <i>{datetime.now().strftime('%H:%M:%S')}</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=asset_keyboard(symbol)
                )
            except Exception as e:
                log.error(f"خطا در ارسال نمودار: {e}")
                await query.edit_message_text(
                    f"⚠️ داده کافی برای رسم نمودار وجود ندارد.\n\nچند بار قیمت را چک کنید تا تاریخچه ذخیره شود.",
                    reply_markup=asset_keyboard(symbol)
                )
        else:
            await query.edit_message_text(
                f"⚠️ <b>داده کافی برای نمودار نیست!</b>\n\n"
                f"حداقل ۲ بار قیمت را چک کنید.",
                parse_mode=ParseMode.HTML,
                reply_markup=asset_keyboard(symbol)
            )
        return
    
    # ── تنظیم هشدار صعودی ──
    if data.startswith("alert_up:") or data.startswith("alert_down:"):
        parts = data.split(":")
        direction = parts[0].split("_")[1]  # up یا down
        symbol = parts[1]
        
        # ذخیره در context برای دریافت عدد
        context.user_data["pending_alert"] = {
            "symbol": symbol,
            "direction": direction
        }
        
        asset = ASSETS[symbol]
        direction_text = "صعودی ⬆️" if direction == "up" else "نزولی ⬇️"
        
        await query.edit_message_text(
            f"🔔 <b>تنظیم هشدار {direction_text}</b>\n\n"
            f"{asset['emoji']} {asset['name']}\n\n"
            f"💰 لطفاً <b>قیمت هدف</b> را به صورت عدد ارسال کنید:\n\n"
            f"مثال: <code>3350</code>\n\n"
            f"❌ برای لغو: <code>/cancel</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # ── هشدارهای من ──
    if data == "my_alerts":
        alerts = cloud.get_alerts(user_id)
        
        if not alerts:
            text = "🔔 <b>هشدارهای شما</b>\n\n📭 هیچ هشداری تنظیم نشده."
        else:
            text = "🔔 <b>هشدارهای فعال شما:</b>\n\n"
            for symbol, alert_list in alerts.items():
                asset = ASSETS.get(symbol)
                if not asset:
                    continue
                for alert in alert_list:
                    if alert.get("triggered"):
                        status = "✅ فعال شده"
                    else:
                        status = "⏳ در انتظار"
                    direction = "⬆️" if alert["direction"] == "up" else "⬇️"
                    text += f"{asset['emoji']} {asset['name']} {direction} <code>{alert['target']}</code>\n   {status}\n\n"
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )
        return

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت قیمت هدف برای هشدار"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    # بررسی دستور لغو
    if text == "/cancel":
        context.user_data.pop("pending_alert", None)
        await update.message.reply_text(
            "❌ عملیات لغو شد.",
            reply_markup=main_menu_keyboard()
        )
        return
    
    # بررسی هشدار در انتظار
    pending = context.user_data.get("pending_alert")
    if not pending:
        return
    
    try:
        target_price = float(text)
        symbol = pending["symbol"]
        direction = pending["direction"]
        asset = ASSETS[symbol]
        
        cloud.set_alert(user_id, symbol, direction, target_price)
        context.user_data.pop("pending_alert", None)
        
        direction_text = "بالا رفتن ⬆️" if direction == "up" else "پایین آمدن ⬇️"
        
        await update.message.reply_text(
            f"✅ <b>هشدار تنظیم شد!</b>\n\n"
            f"{asset['emoji']} <b>{asset['name']}</b>\n"
            f"🎯 هدف: <code>{target_price}</code>\n"
            f"📊 جهت: {direction_text}\n\n"
            f"💡 هر ۵ دقیقه بررسی می‌شود و در صورت رسیدن به هدف، اطلاع‌رسانی می‌شود.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )
    except ValueError:
        await update.message.reply_text(
            "⚠️ لطفاً یک <b>عدد معتبر</b> وارد کنید.\n\n"
            "مثال: <code>3350</code> یا <code>1.0850</code>\n\n"
            "❌ برای لغو: <code>/cancel</code>",
            parse_mode=ParseMode.HTML
        )

async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور /chart - نمودار سریع طلا"""
    user_id = update.effective_user.id
    cloud.increment_request(user_id)
    
    await update.message.reply_chat_action('upload_photo')
    
    # اگه داده کمه، اول قیمت رو بگیر
    history = cloud.data.get("price_history", {}).get("XAUUSD", [])
    if len(history) < 2:
        price = get_price("XAUUSD")
        if price:
            cloud.add_price("XAUUSD", price)
    
    chart_buf = create_price_chart("XAUUSD")
    
    if chart_buf:
        await update.message.reply_photo(
            photo=chart_buf,
            caption=f"🥇 <b>نمودار انس جهانی طلا</b>\n\n⏱ <i>{datetime.now().strftime('%H:%M:%S')}</i>",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            "⚠️ داده کافی برای رسم نمودار نیست.\nچند بار /price را اجرا کنید."
        )

async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور /alerts"""
    user_id = update.effective_user.id
    alerts = cloud.get_alerts(user_id)
    
    if not alerts:
        text = "🔔 <b>هشدارهای شما</b>\n\n📭 هیچ هشداری تنظیم نشده."
    else:
        text = "🔔 <b>هشدارهای فعال:</b>\n\n"
        for symbol, alert_list in alerts.items():
            asset = ASSETS.get(symbol)
            if not asset:
                continue
            for alert in alert_list:
                status = "✅ فعال شده" if alert.get("triggered") else "⏳ در انتظار"
                direction = "⬆️" if alert["direction"] == "up" else "⬇️"
                text += f"{asset['emoji']} {asset['name']} {direction} <code>{alert['target']}</code>\n   {status}\n\n"
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pending_alert", None)
    await update.message.reply_text(
        "❌ عملیات لغو شد.",
        reply_markup=main_menu_keyboard()
    )

# ══════════════════════════════════════════════════════════════════════
# 🛡  مدیریت خطاها
# ══════════════════════════════════════════════════════════════════════
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error(f"خطا: {context.error}")

# ══════════════════════════════════════════════════════════════════════
# 🔄  وظایف دوره‌ای
# ══════════════════════════════════════════════════════════════════════
async def periodic_tasks(application):
    """ذخیره + بررسی هشدار هر ۵ دقیقه"""
    while True:
        try:
            await asyncio.sleep(300)
            log.info("🔄 ذخیره دوره‌ای + بررسی هشدارها")
            cloud.save(force=True)
            await check_alerts(application)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"خطا در وظایف دوره‌ای: {e}")

# ══════════════════════════════════════════════════════════════════════
# 🚀  چرخه حیات
# ══════════════════════════════════════════════════════════════════════
async def post_init(application: Application):
    threading.Thread(target=run_flask, daemon=True).start()
    log.info(f"🌐 Flask روی پورت {PORT}")
    
    asyncio.create_task(periodic_tasks(application))
    log.info("🔄 وظایف دوره‌ای هر ۵ دقیقه")
    
    # Warm-up برای همه دارایی‌ها
    for symbol in ASSETS.keys():
        price = get_price(symbol)
        if price:
            cloud.add_price(symbol, price)
            log.info(f"✅ Warm-up {symbol}: {price}")

async def post_shutdown(application: Application):
    log.info("💾 ذخیره نهایی...")
    cloud.save(force=True)
    log.info("👋 خاموش شد")

# ══════════════════════════════════════════════════════════════════════
# 🎯  نقطه شروع
# ══════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKEN:
        log.error("❌ BOT_TOKEN تنظیم نشده!")
        return
    
    log.info("🚀 Forex Bot در حال راه‌اندازی...")
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
    application.add_handler(CommandHandler("price", price_command))
    application.add_handler(CommandHandler("chart", chart_command))
    application.add_handler(CommandHandler("alerts", alerts_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    
    # Callback ها
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    # پیام‌های متنی (برای دریافت قیمت هدف هشدار)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)
    )
    
    application.add_error_handler(error_handler)
    
    log.info("🤖 ربات آماده است...")
    application.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
