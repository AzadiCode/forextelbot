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
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask
import matplotlib
matplotlib.use('Agg')
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
# 🕐  زمان ایران
# ══════════════════════════════════════════════════════════════════════
IRAN_TZ = timezone(timedelta(hours=3, minutes=30))

def tehran_now():
    return datetime.now(IRAN_TZ)

def tehran_str(fmt="%H:%M:%S"):
    return tehran_now().strftime(fmt)

# ══════════════════════════════════════════════════════════════════════
# 💱  دارایی‌ها
# ══════════════════════════════════════════════════════════════════════
ASSETS = {
    "XAUUSD": {
        "name": "انس جهانی طلا",
        "emoji": "🥇",
        "symbol": "XAU/USD",
        "yahoo": "XAUUSD=X",
        "decimals": 2,
    },
    "EURUSD": {
        "name": "یورو به دلار",
        "emoji": "💶",
        "symbol": "EUR/USD",
        "yahoo": "EURUSD=X",
        "decimals": 4,
    },
    "USDJPY": {
        "name": "ین ژاپن به دلار",
        "emoji": "💴",
        "symbol": "USD/JPY",
        "yahoo": "USDJPY=X",
        "decimals": 3,
    }
}

# ══════════════════════════════════════════════════════════════════════
# ☁️  ذخیره‌سازی ابری
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
            "price_history": {s: [] for s in ASSETS.keys()},
            "alerts": {},
            "last_updated": None
        }
        self._dirty = False
    
    def load(self):
        if not self.api_key or not self.bin_id:
            return
        try:
            r = requests.get(f"{self.url}/{self.bin_id}/latest",
                             headers=self.headers, timeout=15)
            r.raise_for_status()
            loaded = r.json().get("record", {})
            if loaded:
                for k, v in loaded.items():
                    self.data[k] = v
                for s in ASSETS.keys():
                    if s not in self.data["price_history"]:
                        self.data["price_history"][s] = []
                if "alerts" not in self.data:
                    self.data["alerts"] = {}
                log.info(f"☁️ داده‌ها بازیابی شد")
        except Exception as e:
            log.error(f"❌ خطا در بارگذاری: {e}")
    
    def save(self, force=False):
        if not self.api_key or (not force and not self._dirty):
            return
        self.data["last_updated"] = tehran_now().isoformat()
        try:
            if self.bin_id:
                r = requests.put(f"{self.url}/{self.bin_id}",
                                 headers=self.headers,
                                 json=self.data, timeout=15)
            else:
                r = requests.post(self.url,
                                  headers={**self.headers, "X-Bin-Private": "false"},
                                  json=self.data, timeout=15)
                new_id = r.json().get("metadata", {}).get("id", "")
                if new_id:
                    self.bin_id = new_id
                    log.info(f"☁️ Bin جدید: {self.bin_id}")
            r.raise_for_status()
            self._dirty = False
        except Exception as e:
            log.error(f"❌ خطا در ذخیره: {e}")
    
    def add_user(self, user_id, username=None):
        uid = str(user_id)
        if uid not in self.data["users"]:
            self.data["users"][uid] = {
                "username": username,
                "joined_at": tehran_now().isoformat(),
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
        if symbol not in self.data["price_history"]:
            self.data["price_history"][symbol] = []
        self.data["price_history"][symbol].append({
            "price": price,
            "time": tehran_now().isoformat()
        })
        if len(self.data["price_history"][symbol]) > 500:
            self.data["price_history"][symbol] = self.data["price_history"][symbol][-500:]
        self._dirty = True
    
    def set_alert(self, user_id, symbol, direction, target):
        uid = str(user_id)
        if uid not in self.data["alerts"]:
            self.data["alerts"][uid] = {}
        if symbol not in self.data["alerts"][uid]:
            self.data["alerts"][uid][symbol] = []
        self.data["alerts"][uid][symbol].append({
            "direction": direction,
            "target": target,
            "created_at": tehran_now().isoformat(),
            "triggered": False
        })
        self._dirty = True
    
    def get_alerts(self, user_id):
        uid = str(user_id)
        return self.data["alerts"].get(uid, {})
    
    def mark_alert_triggered(self, user_id, symbol, idx):
        try:
            uid = str(user_id)
            self.data["alerts"][uid][symbol][idx]["triggered"] = True
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
    cloud.save(force=True)
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
atexit.register(lambda: cloud.save(force=True))

# ══════════════════════════════════════════════════════════════════════
# 🌐  Flask
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    u, r = cloud.get_stats()
    return f"💱 Forex Bot alive | Users: {u} | Requests: {r} | Time: {tehran_str()}"

@flask_app.route('/health')
def health():
    return {"status": "ok"}

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════
# 💰  دریافت قیمت
# ══════════════════════════════════════════════════════════════════════
def get_price(symbol):
    if symbol not in ASSETS:
        return None
    asset = ASSETS[symbol]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{asset['yahoo']}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        price = data['chart']['result'][0]['meta']['regularMarketPrice']
        return round(float(price), asset['decimals'])
    except Exception as e:
        log.warning(f"⚠️ خطا در {symbol}: {str(e)[:60]}")
        return None

# ══════════════════════════════════════════════════════════════════════
# 📈  داده‌های تاریخی
# ══════════════════════════════════════════════════════════════════════
def get_historical(symbol, range_period, interval):
    if symbol not in ASSETS:
        return [], []
    asset = ASSETS[symbol]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{asset['yahoo']}"
    params = {"interval": interval, "range": range_period}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code != 200:
            return [], []
        data = r.json()
        result = data['chart']['result'][0]
        timestamps = result['timestamp']
        closes = result['indicators']['quote'][0]['close']
        times, prices = [], []
        for ts, p in zip(timestamps, closes):
            if p is not None:
                times.append(datetime.fromtimestamp(ts, tz=IRAN_TZ))
                prices.append(float(p))
        return times, prices
    except Exception as e:
        log.error(f"❌ خطا در historical {symbol}: {e}")
        return [], []

# ══════════════════════════════════════════════════════════════════════
# 📊  رسم نمودار
# ══════════════════════════════════════════════════════════════════════
TIMEFRAME_CONFIG = {
    "1h":  {"range": "1d",  "interval": "1m", "label": "1 Hour", "xfmt": "%H:%M"},
    "4h":  {"range": "1d",  "interval": "5m", "label": "4 Hours", "xfmt": "%H:%M"},
    "1d":  {"range": "1d",  "interval": "15m","label": "1 Day", "xfmt": "%H:%M"},
    "5d":  {"range": "5d",  "interval": "1h", "label": "5 Days", "xfmt": "%m/%d %H:%M"},
    "1mo": {"range": "1mo", "interval": "1d", "label": "1 Month", "xfmt": "%m/%d"},
}

def create_price_chart(symbol, tf_key):
    if symbol not in ASSETS or tf_key not in TIMEFRAME_CONFIG:
        return None
    asset = ASSETS[symbol]
    cfg = TIMEFRAME_CONFIG[tf_key]
    
    times, prices = get_historical(symbol, cfg["range"], cfg["interval"])
    
    if len(prices) < 5:
        history = cloud.data["price_history"].get(symbol, [])
        if len(history) >= 5:
            times, prices = [], []
            for item in history:
                try:
                    t = datetime.fromisoformat(item["time"])
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=IRAN_TZ)
                    times.append(t)
                    prices.append(item["price"])
                except:
                    continue
    
    if len(prices) < 5:
        return None
    
    current = prices[-1]
    start = prices[0]
    min_p = min(prices)
    max_p = max(prices)
    change = current - start
    change_pct = (change / start) * 100 if start else 0
    decimals = asset["decimals"]
    
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(12, 7))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')
    
    is_up = change >= 0
    main_color = '#00d97e' if is_up else '#ff4757'
    
    price_range = max_p - min_p
    if price_range < current * 0.0005:
        padding = current * 0.0015
    else:
        padding = price_range * 0.10
    ax.set_ylim(min_p - padding, max_p + padding)
    
    ax.plot(times, prices, color=main_color, linewidth=2.2, zorder=3)
    ax.fill_between(times, prices, min_p - padding, alpha=0.12, color=main_color, zorder=1)
    ax.axhline(y=current, color=main_color, linestyle='-', alpha=0.9, linewidth=2, zorder=2)
    ax.axhline(y=start, color='#6c757d', linestyle=':', alpha=0.6, linewidth=1.2, zorder=1)
    
    ax.text(times[-1], current,
            f' {current:.{decimals}f} ',
            color='white', fontsize=11, fontweight='bold',
            va='center', ha='right',
            bbox=dict(boxstyle='round,pad=0.4', facecolor=main_color, alpha=0.95, edgecolor='none'),
            zorder=4)
    
    min_idx = prices.index(min_p)
    max_idx = prices.index(max_p)
    ax.plot(times[min_idx], min_p, 'v', color='#00a8ff', markersize=12, zorder=5)
    ax.plot(times[max_idx], max_p, '^', color='#ffa502', markersize=12, zorder=5)
    
    ax.text(times[min_idx], min_p - padding * 0.4,
            f'LOW: {min_p:.{decimals}f}',
            color='#00a8ff', fontsize=9, fontweight='bold',
            ha='center', va='top',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='#0d1117', edgecolor='#00a8ff', alpha=0.9),
            zorder=5)
    ax.text(times[max_idx], max_p + padding * 0.4,
            f'HIGH: {max_p:.{decimals}f}',
            color='#ffa502', fontsize=9, fontweight='bold',
            ha='center', va='bottom',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='#0d1117', edgecolor='#ffa502', alpha=0.9),
            zorder=5)
    
    ax.grid(True, alpha=0.25, linestyle='-', color='#2a2f3a', linewidth=0.8, zorder=0)
    ax.tick_params(colors='#8b949e', labelsize=9)
    
    sign = "+" if is_up else ""
    ax.set_title(
        f"{asset['symbol']}  |  {cfg['label']}\n"
        f"Price: {current:.{decimals}f}  |  {sign}{change:.{decimals}f} ({sign}{change_pct:.2f}%)\n"
        f"{len(prices)} points  |  {tehran_str('%H:%M')}",
        fontsize=13, color='white', fontweight='bold', pad=15, linespacing=1.5
    )
    ax.set_xlabel("Time", fontsize=10, color='#8b949e')
    ax.set_ylabel("Price", fontsize=10, color='#8b949e')
    
    ax.xaxis.set_major_formatter(mdates.DateFormatter(cfg["xfmt"]))
    plt.xticks(rotation=45, fontsize=9)
    plt.yticks(fontsize=9)
    
    for spine in ax.spines.values():
        spine.set_color('#2a2f3a')
        spine.set_linewidth(0.8)
    
    plt.tight_layout()
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=140, bbox_inches='tight',
                facecolor='#0d1117', edgecolor='none')
    buf.seek(0)
    plt.close()
    return buf

# ══════════════════════════════════════════════════════════════════════
# ⌨️  کیبوردها
# ══════════════════════════════════════════════════════════════════════
def main_menu_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🥇 طلا", callback_data="asset:XAUUSD"),
            InlineKeyboardButton("💶 یورو", callback_data="asset:EURUSD"),
            InlineKeyboardButton("💴 ین", callback_data="asset:USDJPY")
        ],
        [InlineKeyboardButton("📊 همه قیمت‌ها", callback_data="all_prices")],
        [
            InlineKeyboardButton("🔔 هشدارهای من", callback_data="my_alerts"),
            InlineKeyboardButton("📈 نمودارها", callback_data="chart_menu")
        ],
        [
            InlineKeyboardButton("📊 آمار", callback_data="stats"),
            InlineKeyboardButton("❓ راهنما", callback_data="help")
        ]
    ])

def asset_kb(symbol):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 قیمت لحظه‌ای", callback_data=f"price:{symbol}")],
        [InlineKeyboardButton("📈 نمودار", callback_data=f"chart:{symbol}")],
        [
            InlineKeyboardButton("⬆️ هشدار صعودی", callback_data=f"alert_up:{symbol}"),
            InlineKeyboardButton("⬇️ هشدار نزولی", callback_data=f"alert_down:{symbol}")
        ],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")]
    ])

def timeframe_kb(symbol):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ 1 ساعت", callback_data=f"tf:{symbol}:1h"),
            InlineKeyboardButton("⏱ 4 ساعت", callback_data=f"tf:{symbol}:4h")
        ],
        [
            InlineKeyboardButton("📅 1 روز", callback_data=f"tf:{symbol}:1d"),
            InlineKeyboardButton("📆 5 روز", callback_data=f"tf:{symbol}:5d")
        ],
        [InlineKeyboardButton("🗓 1 ماه", callback_data=f"tf:{symbol}:1mo")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"asset:{symbol}")]
    ])

def chart_result_kb(symbol):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 تایم‌فریم دیگر", callback_data=f"chart:{symbol}"),
            InlineKeyboardButton("💰 قیمت لحظه‌ای", callback_data=f"price:{symbol}")
        ],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"asset:{symbol}")]
    ])

def chart_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🥇 نمودار طلا", callback_data="chart:XAUUSD")],
        [InlineKeyboardButton("💶 نمودار یورو", callback_data="chart:EURUSD")],
        [InlineKeyboardButton("💴 نمودار ین", callback_data="chart:USDJPY")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")]
    ])

# ══════════════════════════════════════════════════════════════════════
# 🤖  هندلرها
# ══════════════════════════════════════════════════════════════════════
async def send_message(ctx, chat_id, text, reply_markup=None):
    try:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    except Exception as e:
        log.error(f"خطا در ارسال: {e}")

async def send_photo(ctx, chat_id, photo, caption=None):
    try:
        await ctx.bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=caption,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        log.error(f"خطا در ارسال عکس: {e}")

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cloud.add_user(user_id, update.effective_user.username)
    
    text = (
        "👋 <b>سلام و خوش آمدید!</b>\n\n"
        "💱 این ربات قیمت لحظه‌ای ارزها و طلا را با دقت بالا نشان می‌دهد.\n\n"
        "📌 <b>دارایی‌های پشتیبانی شده:</b>\n"
        "• 🥇 انس جهانی طلا (XAU/USD)\n"
        "• 💶 یورو به دلار (EUR/USD)\n"
        "• 💴 ین ژاپن به دلار (USD/JPY)\n\n"
        "💡 <b>از منوی زیر شروع کنید:</b>"
    )
    
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb()
            )
        except:
            await send_message(context, update.effective_chat.id, text, main_menu_kb())
    else:
        await send_message(context, update.effective_chat.id, text, main_menu_kb())

async def show_all_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cloud.increment_request(user_id)
    
    text = "💱 <b>قیمت‌های لحظه‌ای</b>\n\n"
    for symbol, asset in ASSETS.items():
        price = get_price(symbol)
        if price:
            cloud.add_price(symbol, price)
            text += f"{asset['emoji']} <b>{asset['name']}</b>\n"
            text += f"   💰 <code>{price}</code>\n\n"
        else:
            text += f"{asset['emoji']} <b>{asset['name']}</b>\n"
            text += f"   ⚠️ <i>خطا در دریافت</i>\n\n"
    text += f"⏱ <i>{tehran_str()}</i>"
    
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb()
            )
        except:
            await send_message(context, update.effective_chat.id, text, main_menu_kb())
    else:
        await send_message(context, update.effective_chat.id, text, main_menu_kb())

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    chat_id = query.message.chat_id
    cloud.increment_request(user_id)
    
    if data == "main_menu":
        await start_handler(update, context)
        return
    
    if data == "all_prices":
        await show_all_prices(update, context)
        return
    
    if data == "stats":
        u, r = cloud.get_stats()
        text = (
            f"📊 <b>آمار ربات</b>\n\n"
            f"👥 <b>کاربران:</b> {u}\n"
            f"📈 <b>درخواست‌ها:</b> {r}\n\n"
            f"⏰ <i>{tehran_str()}</i>"
        )
        try:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
        except:
            await send_message(context, chat_id, text, main_menu_kb())
        return
    
    if data == "help":
        text = (
            "❓ <b>راهنما</b>\n\n"
            "📌 <b>دستورات:</b>\n"
            "• /start - منوی اصلی\n"
            "• /price - همه قیمت‌ها\n"
            "• /chart - نمودار طلا\n"
            "• /alerts - هشدارهای من\n"
            "• /cancel - لغو عملیات\n\n"
            "💡 <b>قابلیت‌ها:</b>\n"
            "• قیمت لحظه‌ای دقیق\n"
            "• نمودار با تایم‌فریم‌های مختلف\n"
            "• هشدار قیمت هدف\n"
            "• Reaction خودکار در گروه"
        )
        try:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
        except:
            await send_message(context, chat_id, text, main_menu_kb())
        return
    
    if data == "chart_menu":
        try:
            await query.edit_message_text(
                "📈 <b>انتخاب نمودار</b>\n\nیک دارایی را انتخاب کنید:",
                parse_mode=ParseMode.HTML, reply_markup=chart_menu_kb()
            )
        except:
            pass
        return
    
    if data == "my_alerts":
        alerts = cloud.get_alerts(user_id)
        if not alerts:
            text = "🔔 <b>هشدارهای شما</b>\n\n📭 هیچ هشداری تنظیم نشده."
        else:
            text = "🔔 <b>هشدارهای فعال:</b>\n\n"
            for sym, lst in alerts.items():
                asset = ASSETS.get(sym)
                if not asset: continue
                for a in lst:
                    status = "✅ فعال شده" if a.get("triggered") else "⏳ در انتظار"
                    arrow = "⬆️" if a["direction"] == "up" else "⬇️"
                    text += f"{asset['emoji']} {asset['name']} {arrow} <code>{a['target']}</code>\n   {status}\n\n"
        try:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
        except:
            pass
        return
    
    if data.startswith("asset:"):
        symbol = data.split(":")[1]
        asset = ASSETS[symbol]
        price = get_price(symbol)
        
        if price:
            cloud.add_price(symbol, price)
            text = (
                f"{asset['emoji']} <b>{asset['name']}</b>\n\n"
                f"💰 <b>قیمت:</b> <code>{price}</code>\n"
                f"⏱ <i>{tehran_str()}</i>\n\n"
                "یک گزینه را انتخاب کنید:"
            )
        else:
            text = f"{asset['emoji']} <b>{asset['name']}</b>\n\n⚠️ خطا در دریافت قیمت"
        
        try:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=asset_kb(symbol))
        except:
            await send_message(context, chat_id, text, asset_kb(symbol))
        return
    
    if data.startswith("price:"):
        symbol = data.split(":")[1]
        asset = ASSETS[symbol]
        price = get_price(symbol)
        
        if price:
            cloud.add_price(symbol, price)
            text = (
                f"{asset['emoji']} <b>{asset['name']}</b>\n\n"
                f"💰 <b>قیمت:</b> <code>{price}</code>\n"
                f"⏱ <i>{tehran_str()}</i>"
            )
        else:
            text = f"{asset['emoji']} <b>{asset['name']}</b>\n\n⚠️ خطا"
        
        try:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=asset_kb(symbol))
        except:
            pass
        return
    
    if data.startswith("chart:"):
        symbol = data.split(":")[1]
        asset = ASSETS[symbol]
        try:
            await query.edit_message_text(
                f"📈 <b>انتخاب تایم‌فریم برای {asset['name']}</b>\n\n"
                f"💡 <b>پیشنهاد اسکلپ:</b> ⚡ 1 ساعت",
                parse_mode=ParseMode.HTML,
                reply_markup=timeframe_kb(symbol)
            )
        except:
            await send_message(context, chat_id,
                               f"📈 <b>انتخاب تایم‌فریم برای {asset['name']}</b>",
                               timeframe_kb(symbol))
        return
    
    if data.startswith("tf:"):
        parts = data.split(":")
        symbol = parts[1]
        tf_key = parts[2]
        asset = ASSETS[symbol]
        
        try:
            await query.message.delete()
        except:
            pass
        
        loading_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"📈 در حال رسم نمودار {asset['name']}...",
            parse_mode=ParseMode.HTML
        )
        
        chart_buf = create_price_chart(symbol, tf_key)
        
        try:
            await loading_msg.delete()
        except:
            pass
        
        if chart_buf:
            await send_photo(
                context, chat_id, chart_buf,
                caption=f"{asset['emoji']} <b>نمودار {asset['name']}</b>\n\n⏱ <i>{tehran_str()}</i>"
            )
            await send_message(
                context, chat_id,
                "⬇️ <b>عملیات بیشتر:</b>",
                chart_result_kb(symbol)
            )
        else:
            await send_message(
                context, chat_id,
                f"⚠️ <b>داده کافی برای رسم نمودار نیست!</b>\n\n"
                f"چند دقیقه دیگر دوباره امتحان کنید یا تایم‌فریم بزرگتری انتخاب کنید.",
                timeframe_kb(symbol)
            )
        return
    
    if data.startswith("alert_up:") or data.startswith("alert_down:"):
        parts = data.split(":")
        direction = parts[0].split("_")[1]
        symbol = parts[1]
        
        context.user_data["pending_alert"] = {
            "symbol": symbol,
            "direction": direction
        }
        
        asset = ASSETS[symbol]
        dir_text = "صعودی ⬆️" if direction == "up" else "نزولی ⬇️"
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🔔 <b>تنظیم هشدار {dir_text}</b>\n\n"
                f"{asset['emoji']} {asset['name']}\n\n"
                f"💰 لطفاً <b>قیمت هدف</b> را به صورت عدد ارسال کنید:\n\n"
                f"مثال: <code>3350</code>\n\n"
                f"❌ برای لغو: <code>/cancel</code>"
            ),
            parse_mode=ParseMode.HTML
        )
        
        try:
            await query.message.delete()
        except:
            pass
        return

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    
    if text == "/cancel":
        context.user_data.pop("pending_alert", None)
        await send_message(context, chat_id, "❌ عملیات لغو شد.", main_menu_kb())
        return
    
    pending = context.user_data.get("pending_alert")
    if not pending:
        return
    
    try:
        target = float(text)
        symbol = pending["symbol"]
        direction = pending["direction"]
        asset = ASSETS[symbol]
        
        cloud.set_alert(user_id, symbol, direction, target)
        context.user_data.pop("pending_alert", None)
        
        dir_text = "بالا رفتن ⬆️" if direction == "up" else "پایین آمدن ⬇️"
        
        await send_message(
            context, chat_id,
            f"✅ <b>هشدار تنظیم شد!</b>\n\n"
            f"{asset['emoji']} <b>{asset['name']}</b>\n"
            f"🎯 هدف: <code>{target}</code>\n"
            f"📊 جهت: {dir_text}\n\n"
            f"💡 هر 2 دقیقه بررسی می‌شود.",
            main_menu_kb()
        )
    except ValueError:
        await send_message(
            context, chat_id,
            "⚠️ لطفاً یک <b>عدد معتبر</b> وارد کنید.\n\n"
            "مثال: <code>3350</code> یا <code>1.0850</code>\n\n"
            "❌ برای لغو: <code>/cancel</code>"
        )

async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cloud.increment_request(update.effective_user.id)
    await update.message.reply_chat_action('upload_photo')
    
    chart_buf = create_price_chart("XAUUSD", "1h")
    if chart_buf:
        await send_photo(
            context, update.effective_chat.id, chart_buf,
            caption=f"🥇 <b>نمودار طلا (1 ساعت)</b>\n\n⏱ <i>{tehran_str()}</i>"
        )
        await send_message(
            context, update.effective_chat.id,
            "⬇️ <b>عملیات بیشتر:</b>",
            chart_result_kb("XAUUSD")
        )
    else:
        await send_message(context, update.effective_chat.id,
                          "⚠️ داده کافی نیست. چند دقیقه بعد امتحان کنید.")

async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    alerts = cloud.get_alerts(user_id)
    
    if not alerts:
        text = "🔔 <b>هشدارهای شما</b>\n\n📭 هیچ هشداری تنظیم نشده."
    else:
        text = "🔔 <b>هشدارهای فعال:</b>\n\n"
        for sym, lst in alerts.items():
            asset = ASSETS.get(sym)
            if not asset: continue
            for a in lst:
                status = "✅ فعال" if a.get("triggered") else "⏳ در انتظار"
                arrow = "⬆️" if a["direction"] == "up" else "⬇️"
                text += f"{asset['emoji']} {asset['name']} {arrow} <code>{a['target']}</code>\n   {status}\n\n"
    
    await send_message(context, update.effective_chat.id, text)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pending_alert", None)
    await send_message(context, update.effective_chat.id, "❌ لغو شد.", main_menu_kb())

# ══════════════════════════════════════════════════════════════════════
# ❤️  Reaction در گروه
# ══════════════════════════════════════════════════════════════════════
REACTION_RULES = {
    "👍": ["طلا", "gold", "قیمت", "xau"],
    "🔥": ["عالی", "محشر", "فوق‌العاده", "💰"],
    "❤️": ["ممنون", "مرسی", "دمت گرم", "🙏"],
    "👏": ["بررسی کن", "تحلیل", "نظر"],
}

async def group_reaction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    if msg.chat.type not in ['group', 'supergroup']:
        return
    
    text = msg.text.lower()
    for emoji, kws in REACTION_RULES.items():
        if any(kw in text for kw in kws):
            try:
                await context.bot.set_message_reaction(
                    chat_id=msg.chat_id,
                    message_id=msg.message_id,
                    reaction=emoji,
                    is_big=False
                )
            except:
                pass
            break

# ══════════════════════════════════════════════════════════════════════
# 🛡  مدیریت خطا
# ══════════════════════════════════════════════════════════════════════
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error(f"خطا: {context.error}")

# ══════════════════════════════════════════════════════════════════════
# 🔔  وظایف دوره‌ای
# ══════════════════════════════════════════════════════════════════════
async def periodic_tasks(application):
    while True:
        try:
            await asyncio.sleep(120)
            
            for symbol in ASSETS.keys():
                price = get_price(symbol)
                if price:
                    cloud.add_price(symbol, price)
            
            cloud.save(force=True)
            
            try:
                for uid, alerts in list(cloud.data.get("alerts", {}).items()):
                    for symbol, lst in list(alerts.items()):
                        if not lst: continue
                        price = get_price(symbol)
                        if not price: continue
                        asset = ASSETS[symbol]
                        
                        for idx, a in enumerate(lst):
                            if a.get("triggered"): continue
                            target = a["target"]
                            direction = a["direction"]
                            
                            triggered = False
                            if direction == "up" and price >= target:
                                triggered = True
                                emoji, msg = "📈", "بالا رفت"
                            elif direction == "down" and price <= target:
                                triggered = True
                                emoji, msg = "📉", "پایین آمد"
                            
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
                                except Exception as e:
                                    log.error(f"خطا در ارسال هشدار: {e}")
            except Exception as e:
                log.error(f"خطا در بررسی هشدارها: {e}")
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"خطا در periodic_tasks: {e}")

# ══════════════════════════════════════════════════════════════════════
# 🚀  چرخه حیات
# ══════════════════════════════════════════════════════════════════════
async def post_init(application: Application):
    threading.Thread(target=run_flask, daemon=True).start()
    log.info(f"🌐 Flask پورت {PORT}")
    
    asyncio.create_task(periodic_tasks(application))
    log.info("🔄 وظایف دوره‌ای هر 2 دقیقه")
    
    for sym in ASSETS.keys():
        p = get_price(sym)
        if p:
            cloud.add_price(sym, p)
            log.info(f"✅ {sym}: {p}")

async def post_shutdown(application: Application):
    cloud.save(force=True)
    log.info("👋 خاموش شد")

# ══════════════════════════════════════════════════════════════════════
# 🎯  شروع
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
    
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", start_handler))
    application.add_handler(CommandHandler("price", show_all_prices))
    application.add_handler(CommandHandler("chart", chart_command))
    application.add_handler(CommandHandler("alerts", alerts_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    # هندلر متن (برای دریافت قیمت هدف هشدار) - group 0
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler),
        group=0
    )
    
    # هندلر Reaction در گروه - group 1 (فیلتر ساده‌تر)
    # در داخل handler چک میکنیم که گروه باشه
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, group_reaction_handler),
        group=1
    )
    
    application.add_error_handler(error_handler)
    
    log.info("🤖 آماده!")
    application.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
