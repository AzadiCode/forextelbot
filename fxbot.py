#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScalpBot Pro v2 — Fixed & Production Ready
"""

import os, sys, asyncio, logging, signal, atexit, io, threading, time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional

import requests, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from flask import Flask, jsonify

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

# ══════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO, stream=sys.stdout,
)
log = logging.getLogger("ScalpBot")

# ══════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
PORT        = int(os.environ.get("PORT", 5000))
JSONBIN_KEY = os.environ.get("JSONBIN_API_KEY", "")
JSONBIN_BIN = os.environ.get("JSONBIN_BIN_ID", "")

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
def now_ir() -> datetime: return datetime.now(IRAN_TZ)
def str_ir(fmt="%H:%M:%S") -> str: return now_ir().strftime(fmt)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

# ══════════════════════════════════════════════════════════════════════
# ASSETS
# ══════════════════════════════════════════════════════════════════════
@dataclass
class Source:
    name: str
    url: str
    parser: object
    min_val: float
    max_val: float

@dataclass
class Asset:
    key: str
    name: str
    emoji: str
    symbol: str
    decimals: int
    pip_size: float
    sources: list
    yahoo_sym: str      # نماد Yahoo برای چارت (spot)
    yahoo_fb: str       # fallback (futures) فقط برای طلا

ASSETS: dict[str, Asset] = {
    "XAUUSD": Asset(
        key="XAUUSD", name="Gold", emoji="🥇", symbol="XAU/USD",
        decimals=2, pip_size=0.1,
        yahoo_sym="GC=F", yahoo_fb="XAUUSD=X",
        sources=[
            Source("GoldPrice",
                   "https://data-asg.goldprice.org/dbXRates/USD",
                   lambda d: d["items"][0]["xauPrice"], 1000, 12000),
            Source("Swissquote",
                   "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD",
                   lambda d: (float(d[0]["spreadProfilePrices"][0]["ask"]) +
                              float(d[0]["spreadProfilePrices"][0]["bid"])) / 2, 1000, 12000),
            Source("Yahoo",
                   "https://query1.finance.yahoo.com/v8/finance/chart/GC=F",
                   lambda d: d["chart"]["result"][0]["meta"]["regularMarketPrice"], 1000, 12000),
        ],
    ),
    "EURUSD": Asset(
        key="EURUSD", name="EUR/USD", emoji="💶", symbol="EUR/USD",
        decimals=5, pip_size=0.0001,
        yahoo_sym="EURUSD=X", yahoo_fb="EURUSD=X",
        sources=[
            Source("Swissquote",
                   "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/EUR/USD",
                   lambda d: (float(d[0]["spreadProfilePrices"][0]["ask"]) +
                              float(d[0]["spreadProfilePrices"][0]["bid"])) / 2, 0.8, 1.5),
            Source("Yahoo",
                   "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X",
                   lambda d: d["chart"]["result"][0]["meta"]["regularMarketPrice"], 0.8, 1.5),
        ],
    ),
    "USDJPY": Asset(
        key="USDJPY", name="USD/JPY", emoji="💴", symbol="USD/JPY",
        decimals=3, pip_size=0.01,
        yahoo_sym="USDJPY=X", yahoo_fb="USDJPY=X",
        sources=[
            Source("Swissquote",
                   "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/USD/JPY",
                   lambda d: (float(d[0]["spreadProfilePrices"][0]["ask"]) +
                              float(d[0]["spreadProfilePrices"][0]["bid"])) / 2, 100, 200),
            Source("Yahoo",
                   "https://query1.finance.yahoo.com/v8/finance/chart/USDJPY=X",
                   lambda d: d["chart"]["result"][0]["meta"]["regularMarketPrice"], 100, 200),
        ],
    ),
    "GBPUSD": Asset(
        key="GBPUSD", name="GBP/USD", emoji="💷", symbol="GBP/USD",
        decimals=5, pip_size=0.0001,
        yahoo_sym="GBPUSD=X", yahoo_fb="GBPUSD=X",
        sources=[
            Source("Swissquote",
                   "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/GBP/USD",
                   lambda d: (float(d[0]["spreadProfilePrices"][0]["ask"]) +
                              float(d[0]["spreadProfilePrices"][0]["bid"])) / 2, 1.1, 1.6),
            Source("Yahoo",
                   "https://query1.finance.yahoo.com/v8/finance/chart/GBPUSD=X",
                   lambda d: d["chart"]["result"][0]["meta"]["regularMarketPrice"], 1.1, 1.6),
        ],
    ),
}

# ══════════════════════════════════════════════════════════════════════
# TIMEFRAMES  — FIX: 1h range با 2m interval (Yahoo محدودیت 1m دارد)
# Yahoo Finance محدودیت‌ها:
#   interval=1m  → max range: 7d  (ولی اغلب کمتر برمیگرداند)
#   interval=2m  → max range: 60d
#   interval=5m  → max range: 60d
#   interval=15m → max range: 60d
#   interval=1h  → max range: 730d
#   interval=1d  → max range: unlimited
# ══════════════════════════════════════════════════════════════════════
TIMEFRAMES = {
    # key: (yahoo_range, yahoo_interval, label_en, label_fa)
    "1h":  ("1d",  "2m",  "1H  |  2m bars",  "۱ ساعت"),
    "4h":  ("5d",  "5m",  "4H  |  5m bars",  "۴ ساعت"),
    "1d":  ("1mo", "15m", "1D  |  15m bars", "۱ روز"),
    "5d":  ("3mo", "1h",  "5D  |  1h bars",  "۵ روز"),
    "1mo": ("1y",  "1d",  "1M  |  1d bars",  "۱ ماه"),
}

# ══════════════════════════════════════════════════════════════════════
# PRICE FETCHER
# ══════════════════════════════════════════════════════════════════════
_price_cache: dict[str, tuple[float, float]] = {}
CACHE_TTL = 12  # seconds

def get_price(symbol: str, force: bool = False) -> Optional[float]:
    if symbol not in ASSETS:
        return None
    if not force and symbol in _price_cache:
        p, ts = _price_cache[symbol]
        if time.time() - ts < CACHE_TTL:
            return p
    asset = ASSETS[symbol]
    for src in asset.sources:
        try:
            r = requests.get(src.url, headers=_HEADERS, timeout=10)
            r.raise_for_status()
            p = float(src.parser(r.json()))
            if src.min_val < p < src.max_val:
                p = round(p, asset.decimals)
                _price_cache[symbol] = (p, time.time())
                log.info(f"price {symbol}={p} [{src.name}]")
                return p
        except Exception as e:
            log.warning(f"src {src.name}/{symbol}: {str(e)[:60]}")
    return None

# ══════════════════════════════════════════════════════════════════════
# CHART ENGINE — v2 (fully fixed)
# ══════════════════════════════════════════════════════════════════════

def _fetch_ohlc(yahoo_sym: str, interval: str, range_: str) -> Optional[tuple]:
    """
    دریافت OHLC از Yahoo Finance.
    برمی‌گرداند (times_ir, opens, highs, lows, closes) یا None.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}"
    try:
        r = requests.get(url, headers=_HEADERS,
                         params={"interval": interval, "range": range_}, timeout=15)
        r.raise_for_status()
        data = r.json()
        result = data["chart"]["result"][0]
        ts_list = result["timestamp"]
        q = result["indicators"]["quote"][0]

        times, opens, highs, lows, closes = [], [], [], [], []
        for i, ts in enumerate(ts_list):
            c, o, h, l = q["close"][i], q["open"][i], q["high"][i], q["low"][i]
            if None in (c, o, h, l):
                continue
            times.append(datetime.fromtimestamp(ts, tz=IRAN_TZ))
            opens.append(float(o)); highs.append(float(h))
            lows.append(float(l));  closes.append(float(c))

        if len(closes) < 5:
            log.warning(f"Yahoo {yahoo_sym}: only {len(closes)} valid bars")
            return None
        log.info(f"Yahoo {yahoo_sym} {range_}/{interval}: {len(closes)} bars")
        return times, opens, highs, lows, closes
    except Exception as e:
        log.error(f"fetch_ohlc {yahoo_sym}: {str(e)[:80]}")
        return None

def _sma(data: list, period: int) -> list:
    out = [None] * len(data)
    for i in range(period - 1, len(data)):
        out[i] = sum(data[i - period + 1: i + 1]) / period
    return out

def _rsi(closes: list, period: int = 14) -> list:
    out = [None] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
        if i >= period:
            ag = sum(gains[-period:]) / period
            al = sum(losses[-period:]) / period
            out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out

def create_chart(symbol: str, tf_key: str = "1h",
                 live_price: Optional[float] = None) -> Optional[io.BytesIO]:
    """
    رسم چارت حرفه‌ای.
    live_price: اگه داده بشه، آخرین قیمت روی چارت override میشه با قیمت live.
    """
    if symbol not in ASSETS or tf_key not in TIMEFRAMES:
        return None

    asset = ASSETS[symbol]
    yahoo_range, yahoo_interval, label_en, label_fa = TIMEFRAMES[tf_key]

    # دریافت داده — اول از yahoo_sym، fallback به yahoo_fb
    ohlc = _fetch_ohlc(asset.yahoo_sym, yahoo_interval, yahoo_range)
    if ohlc is None and asset.yahoo_fb != asset.yahoo_sym:
        ohlc = _fetch_ohlc(asset.yahoo_fb, yahoo_interval, yahoo_range)
    if ohlc is None:
        return None

    times, opens, highs, lows, closes = ohlc
    dec = asset.decimals

    # FIX: برش به محدوده تایم‌فریم واقعی
    # Yahoo با range=1d ممکنه داده بیشتری برگردونه → فقط N کندل آخر رو نگه دار
    max_bars = {"1h": 60, "4h": 48, "1d": 96, "5d": 120, "1mo": 365}
    n = max_bars.get(tf_key, len(times))
    if len(times) > n:
        times, opens, highs, lows, closes = (
            times[-n:], opens[-n:], highs[-n:], lows[-n:], closes[-n:]
        )

    # FIX: override قیمت آخر با live price
    if live_price is not None:
        closes[-1] = live_price
        highs[-1] = max(highs[-1], live_price)
        lows[-1]  = min(lows[-1], live_price)

    # آمار
    cur   = closes[-1]
    start = closes[0]
    hi    = max(highs)
    lo    = min(lows)
    chg   = cur - start
    chgp  = (chg / start * 100) if start else 0
    is_up = chg >= 0
    C     = "#26a69a" if is_up else "#ef5350"   # رنگ اصلی (trading‌view style)
    C_dim = "#1a7a70" if is_up else "#b03030"

    # اندیکاتور
    sma20    = _sma(closes, min(20, len(closes)))
    sma50    = _sma(closes, min(50, len(closes)))
    rsi_vals = _rsi(closes)

    # ── Figure ──
    BG = "#131722"
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 8,
        "axes.unicode_minus": False,
    })
    fig = plt.figure(figsize=(13, 8), facecolor=BG)
    gs = fig.add_gridspec(
        3, 1, height_ratios=[4, 0.9, 1.1],
        hspace=0.0, left=0.01, right=0.88, top=0.88, bottom=0.08
    )
    ax  = fig.add_subplot(gs[0])   # price
    axv = fig.add_subplot(gs[1], sharex=ax)  # range bars
    axr = fig.add_subplot(gs[2], sharex=ax)  # RSI

    for a in (ax, axv, axr):
        a.set_facecolor(BG)
        a.tick_params(colors="#787b86", labelsize=7.5, length=3)
        a.yaxis.set_label_position("right")
        a.yaxis.tick_right()
        for sp in a.spines.values():
            sp.set_color("#2a2e39")
        a.grid(True, color="#2a2e39", linewidth=0.5, linestyle="-")

    # ── price line ──
    ax.plot(times, closes, color=C, linewidth=1.6, zorder=4, solid_capstyle="round")
    ax.fill_between(times, closes, lo * 0.9998, color=C, alpha=0.06, zorder=1)

    # SMA
    def plot_sma(period, sma, color, label):
        pts = [(t, v) for t, v in zip(times, sma) if v is not None]
        if pts:
            tt, vv = zip(*pts)
            ax.plot(tt, vv, color=color, linewidth=0.9,
                    linestyle="--", alpha=0.75, label=label, zorder=3)
    plot_sma(20, sma20, "#f9a825", "SMA20")
    plot_sma(50, sma50, "#7b1fa2", "SMA50")

    # current price line + label
    ax.axhline(cur, color=C, linestyle="-", alpha=0.35, linewidth=0.8, zorder=2)
    ax.text(1.002, cur, f"{cur:.{dec}f}",
            transform=ax.get_yaxis_transform(),
            fontsize=8.5, fontweight="bold",
            color="white",
            va="center", ha="left",
            bbox=dict(boxstyle="round,pad=0.3", fc=C, ec="none"))

    # HIGH / LOW
    hi_idx = highs.index(hi)
    lo_idx = lows.index(lo)
    ax.annotate(f"H {hi:.{dec}f}",
                xy=(times[hi_idx], hi), xytext=(0, 9),
                textcoords="offset points",
                fontsize=7.5, color="#f9a825", fontweight="bold", ha="center",
                arrowprops=dict(arrowstyle="-", color="#f9a825", lw=0.6))
    ax.annotate(f"L {lo:.{dec}f}",
                xy=(times[lo_idx], lo), xytext=(0, -11),
                textcoords="offset points",
                fontsize=7.5, color="#29b6f6", fontweight="bold", ha="center",
                arrowprops=dict(arrowstyle="-", color="#29b6f6", lw=0.6))

    # price Y padding
    pr = hi - lo
    pad = max(pr * 0.08, cur * 0.0008)
    ax.set_ylim(lo - pad, hi + pad * 2)

    # legend
    ax.legend(loc="upper left", fontsize=7.5,
              facecolor=BG, edgecolor="#2a2e39", labelcolor="#d1d4dc")

    # ── Range bars ──
    bar_w_days = (times[-1] - times[0]).total_seconds() / 86400 / len(times) * 0.8
    bar_colors = [C if c >= o else "#ef5350" for c, o in zip(closes, opens)]
    axv.bar(times, [abs(h - l) for h, l in zip(highs, lows)],
            color=bar_colors, alpha=0.55, width=timedelta(days=bar_w_days))
    axv.set_yticks([])
    axv.text(1.002, 0.5, "Range", transform=axv.transAxes,
             fontsize=7, color="#787b86", va="center", ha="left")

    # ── RSI ──
    rsi_pts = [(t, v) for t, v in zip(times, rsi_vals) if v is not None]
    if rsi_pts:
        rt, rv = zip(*rsi_pts)
        axr.plot(rt, rv, color="#ce93d8", linewidth=1.2, zorder=3)
        axr.axhline(70, color="#ef5350", linestyle="--", alpha=0.45, linewidth=0.7)
        axr.axhline(50, color="#787b86", linestyle=":",  alpha=0.3,  linewidth=0.6)
        axr.axhline(30, color="#26a69a", linestyle="--", alpha=0.45, linewidth=0.7)
        axr.fill_between(rt, rv, 70, where=[v >= 70 for v in rv],
                         color="#ef5350", alpha=0.12)
        axr.fill_between(rt, rv, 30, where=[v <= 30 for v in rv],
                         color="#26a69a", alpha=0.12)
        axr.set_ylim(10, 90)
        axr.set_yticks([30, 50, 70])
        last_rsi = rv[-1]
        rsi_c = "#26a69a" if last_rsi >= 50 else "#ef5350"
        axr.text(1.002, last_rsi, f"{last_rsi:.0f}",
                 transform=axr.get_yaxis_transform(),
                 fontsize=7.5, fontweight="bold", color="white",
                 va="center", ha="left",
                 bbox=dict(boxstyle="round,pad=0.25", fc=rsi_c, ec="none"))

    axr.text(1.002, 0.5, "RSI", transform=axr.transAxes,
             fontsize=7, color="#787b86", va="center", ha="left")

    # ── X axis (Iran time) ──
    if tf_key in ("1h", "4h"):
        fmt = "%H:%M"
    elif tf_key == "1d":
        fmt = "%H:%M"
    else:
        fmt = "%m/%d"

    axr.xaxis.set_major_formatter(
        mdates.DateFormatter(fmt, tz=IRAN_TZ)
    )
    # تعداد tick مناسب
    axr.xaxis.set_major_locator(mdates.AutoDateLocator(tz=IRAN_TZ))
    plt.setp(axr.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=7.5)
    plt.setp(ax.xaxis.get_majorticklabels(), visible=False)
    plt.setp(axv.xaxis.get_majorticklabels(), visible=False)

    # ── Title (فقط انگلیسی — بدون مشکل font) ──
    sign = "+" if is_up else ""
    arrow = "▲" if is_up else "▼"
    title_line1 = f"{asset.symbol}   {label_en}   |   Iran Time (UTC+3:30)"
    title_line2 = (
        f"{arrow} {cur:.{dec}f}   {sign}{chg:.{dec}f}  ({sign}{chgp:.2f}%)   "
        f"H: {hi:.{dec}f}   L: {lo:.{dec}f}"
    )
    fig.text(0.44, 0.935, title_line1,
             ha="center", va="bottom", fontsize=10, fontweight="bold", color="#d1d4dc")
    fig.text(0.44, 0.905, title_line2,
             ha="center", va="bottom", fontsize=10, color=C)

    # watermark
    fig.text(0.87, 0.01, "@ScalpBotPro",
             ha="right", va="bottom", fontsize=6.5, color="#2a2e39")

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=140, bbox_inches="tight", facecolor=BG)
    buf.seek(0)
    plt.close(fig)
    return buf

# ══════════════════════════════════════════════════════════════════════
# CLOUD STORAGE
# ══════════════════════════════════════════════════════════════════════
class CloudStorage:
    def __init__(self):
        self._key   = JSONBIN_KEY
        self._bin   = JSONBIN_BIN
        self._url   = "https://api.jsonbin.io/v3/b"
        self._hdrs  = {"X-Master-Key": self._key, "Content-Type": "application/json"}
        self._lock  = threading.Lock()
        self._dirty = False
        self._data  = {
            "users": {}, "total_requests": 0,
            "price_history": {s: [] for s in ASSETS},
            "alerts": {}, "last_updated": None,
        }

    def load(self):
        if not (self._key and self._bin):
            log.info("CloudStorage: local only (no JSONBin config)")
            return
        try:
            r = requests.get(f"{self._url}/{self._bin}/latest",
                             headers=self._hdrs, timeout=15)
            r.raise_for_status()
            remote = r.json().get("record", {})
            with self._lock:
                for k, v in remote.items():
                    self._data[k] = v
                for sym in ASSETS:
                    self._data.setdefault("price_history", {}).setdefault(sym, [])
                self._data.setdefault("alerts", {})
            log.info("CloudStorage loaded")
        except Exception as e:
            log.error(f"CloudStorage load: {e}")

    def save(self, force=False):
        if not self._key:
            return
        if not force and not self._dirty:
            return
        with self._lock:
            self._data["last_updated"] = now_ir().isoformat()
            payload = {k: v for k, v in self._data.items()}
        try:
            if self._bin:
                r = requests.put(f"{self._url}/{self._bin}",
                                 headers=self._hdrs, json=payload, timeout=15)
            else:
                r = requests.post(self._url,
                                  headers={**self._hdrs, "X-Bin-Private": "false"},
                                  json=payload, timeout=15)
                bid = r.json().get("metadata", {}).get("id", "")
                if bid:
                    self._bin = bid
            r.raise_for_status()
            self._dirty = False
        except Exception as e:
            log.error(f"CloudStorage save: {e}")

    def ensure_user(self, uid: int, username: str = ""):
        key = str(uid)
        with self._lock:
            if key not in self._data["users"]:
                self._data["users"][key] = {
                    "username": username, "joined": now_ir().isoformat(), "requests": 0
                }
                self._dirty = True

    def inc(self, uid: int):
        key = str(uid)
        with self._lock:
            if key in self._data["users"]:
                self._data["users"][key]["requests"] += 1
            self._data["total_requests"] = self._data.get("total_requests", 0) + 1
            self._dirty = True

    def stats(self):
        return len(self._data.get("users", {})), self._data.get("total_requests", 0)

    def push_price(self, sym: str, price: float):
        with self._lock:
            h = self._data.setdefault("price_history", {}).setdefault(sym, [])
            h.append({"p": price, "t": now_ir().isoformat()})
            if len(h) > 200:
                self._data["price_history"][sym] = h[-200:]
            self._dirty = True

    def get_history(self, sym: str) -> list:
        return list(self._data.get("price_history", {}).get(sym, []))

    def add_alert(self, uid: int, sym: str, direction: str, target: float):
        key = str(uid)
        with self._lock:
            alerts = self._data.setdefault("alerts", {})
            alerts.setdefault(key, {}).setdefault(sym, []).append({
                "dir": direction, "target": target,
                "at": now_ir().isoformat(), "done": False,
            })
            self._dirty = True

    def get_alerts(self, uid: int) -> dict:
        return dict(self._data.get("alerts", {}).get(str(uid), {}))

    def mark_done(self, uid: int, sym: str, idx: int):
        try:
            with self._lock:
                self._data["alerts"][str(uid)][sym][idx]["done"] = True
                self._dirty = True
        except Exception:
            pass

    def clear_done(self, uid: int):
        key = str(uid)
        with self._lock:
            for sym in list(self._data.get("alerts", {}).get(key, {}).keys()):
                self._data["alerts"][key][sym] = [
                    a for a in self._data["alerts"][key][sym] if not a.get("done")
                ]
            self._dirty = True

cloud = CloudStorage()

# ══════════════════════════════════════════════════════════════════════
# FLASK
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route("/")
def root():
    u, r = cloud.stats()
    return f"ScalpBot Pro | users={u} | reqs={r} | {str_ir()}"

@flask_app.route("/health")
def health():
    return jsonify(status="ok", time=str_ir())

def _run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════════════════════════════
def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🥇 XAU/USD", callback_data="a:XAUUSD"),
         InlineKeyboardButton("💶 EUR/USD", callback_data="a:EURUSD")],
        [InlineKeyboardButton("💴 USD/JPY", callback_data="a:USDJPY"),
         InlineKeyboardButton("💷 GBP/USD", callback_data="a:GBPUSD")],
        [InlineKeyboardButton("📊 همه قیمت‌ها", callback_data="all"),
         InlineKeyboardButton("🔔 هشدارها",     callback_data="my_alerts")],
        [InlineKeyboardButton("📈 نمودار",       callback_data="chart_menu"),
         InlineKeyboardButton("📉 آمار",         callback_data="stats"),
         InlineKeyboardButton("❓ راهنما",       callback_data="help")],
    ])

def kb_asset(sym: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 قیمت لحظه‌ای 🔄", callback_data=f"px:{sym}")],
        [InlineKeyboardButton("📈 نمودار",            callback_data=f"chart:{sym}")],
        [InlineKeyboardButton("⬆️ هشدار صعودی",      callback_data=f"al_up:{sym}"),
         InlineKeyboardButton("⬇️ هشدار نزولی",      callback_data=f"al_dn:{sym}")],
        [InlineKeyboardButton("🔙 منوی اصلی",        callback_data="main")],
    ])

def kb_tf(sym: str) -> InlineKeyboardMarkup:
    rows = []
    tf_labels = {
        "1h":  "⚡ 1H  (scalp)",
        "4h":  "⏱ 4H",
        "1d":  "📅 1D",
        "5d":  "📆 5D",
        "1mo": "🗓 1M",
    }
    row = []
    for key, label in tf_labels.items():
        row.append(InlineKeyboardButton(label, callback_data=f"tf:{sym}:{key}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"a:{sym}")])
    return InlineKeyboardMarkup(rows)

def kb_chart_menu() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
                f"{ASSETS[s].emoji} {ASSETS[s].symbol}",
                callback_data=f"chart:{s}")] for s in ASSETS]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="main")])
    return InlineKeyboardMarkup(rows)

def kb_after_chart(sym: str) -> InlineKeyboardMarkup:
    """دکمه‌های زیر عکس چارت — FIX: reply_photo دکمه‌ها رو روی پیام عکس میذاره."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تایم دیگر",     callback_data=f"chart:{sym}"),
         InlineKeyboardButton("💰 قیمت",           callback_data=f"px:{sym}")],
        [InlineKeyboardButton("⬆️ هشدار صعودی",   callback_data=f"al_up:{sym}"),
         InlineKeyboardButton("⬇️ هشدار نزولی",   callback_data=f"al_dn:{sym}")],
        [InlineKeyboardButton("🔙 منوی اصلی",     callback_data="main")],
    ])

def kb_alerts() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 پاک کردن تریگرشده‌ها", callback_data="alerts_clean")],
        [InlineKeyboardButton("🔙 منوی اصلی", callback_data="main")],
    ])

# ══════════════════════════════════════════════════════════════════════
# MESSAGE BUILDERS
# ══════════════════════════════════════════════════════════════════════
def _price_line(sym: str, price: float) -> str:
    a = ASSETS[sym]
    hist = cloud.get_history(sym)
    if len(hist) >= 2:
        prev = hist[-2]["p"]
        diff = price - prev
        pips = round(diff / a.pip_size)
        sign = "+" if diff >= 0 else ""
        arrow = "▲" if diff >= 0 else "▼"
        return (
            f"{a.emoji} <b>{a.symbol}</b>\n"
            f"   <code>{price:.{a.decimals}f}</code>  "
            f"{arrow} <code>{sign}{diff:.{a.decimals}f}</code>  "
            f"(<code>{sign}{pips:+d} pips</code>)\n"
        )
    return f"{a.emoji} <b>{a.symbol}</b>\n   <code>{price:.{a.decimals}f}</code>\n"

def build_all_prices() -> str:
    lines = [f"💱 <b>Live Prices</b>  —  <i>{str_ir()}</i>\n"]
    for sym in ASSETS:
        p = get_price(sym)
        if p:
            cloud.push_price(sym, p)
            lines.append(_price_line(sym, p))
        else:
            a = ASSETS[sym]
            lines.append(f"{a.emoji} <b>{a.symbol}</b>\n   ⚠️ <i>error</i>\n")
    return "\n".join(lines)

def build_alerts_text(uid: int) -> str:
    alerts = cloud.get_alerts(uid)
    if not alerts:
        return (
            "🔔 <b>هشدارهای قیمت</b>\n\n"
            "📭 هیچ هشدار فعالی ندارید.\n\n"
            "از منوی دارایی → ⬆️/⬇️ هشدار بسازید."
        )
    lines = ["🔔 <b>هشدارهای فعال:</b>\n"]
    for sym, al_list in alerts.items():
        a = ASSETS.get(sym)
        if not a:
            continue
        for al in al_list:
            st = "✅" if al.get("done") else "⏳"
            dr = "⬆️" if al["dir"] == "up" else "⬇️"
            lines.append(f"{st} {a.emoji} {a.symbol}  {dr}  <code>{al['target']:.{a.decimals}f}</code>")
    lines.append("\n<i>بررسی هر ۵ دقیقه</i>")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════
async def _reg(update: Update):
    u = update.effective_user
    cloud.ensure_user(u.id, u.username or "")
    cloud.inc(u.id)

async def _send_chart(
    sym: str, tf_key: str,
    send_photo_fn,           # coroutine factory: photo, caption, parse_mode, reply_markup
    fail_reply_fn,           # coroutine factory: text, reply_markup
):
    """
    مشترک بین cmd_chart و callback tf:
    چارت میسازه و ارسال میکنه.
    دکمه‌ها روی پیام عکس هستن — هیچ پیام متنی که بشه delete کرد وجود نداره.
    """
    live = get_price(sym, force=True)
    if live:
        cloud.push_price(sym, live)

    buf = create_chart(sym, tf_key, live_price=live)
    if not buf:
        await fail_reply_fn()
        return

    a = ASSETS[sym]
    _, _, label_en, label_fa = TIMEFRAMES[tf_key]
    live_str = f"{live:.{a.decimals}f}" if live else "N/A"
    caption = (
        f"{a.emoji} <b>{a.symbol}</b>   {label_fa}\n"
        f"💰 Live: <code>{live_str}</code>   ⏱ {str_ir()}"
    )
    await send_photo_fn(buf, caption)

# ══════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reg(update)
    await update.effective_message.reply_text(
        "👋 <b>ScalpBot Pro</b>\n\n"
        "قیمت لحظه‌ای فارکس و طلا — برای اسکلپرها.\n\n"
        "<b>دارایی‌ها:</b> XAU/USD  |  EUR/USD  |  USD/JPY  |  GBP/USD\n\n"
        "منوی زیر 👇",
        parse_mode=ParseMode.HTML, reply_markup=kb_main()
    )

async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reg(update)
    msg = await update.effective_message.reply_text("⏳")
    text = build_all_prices()
    await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_main())

async def cmd_chart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reg(update)
    args = ctx.args
    sym = (args[0].upper() if args else "XAUUSD")
    if sym not in ASSETS:
        sym = "XAUUSD"
    tf  = (args[1].lower() if len(args) > 1 else "1h")
    if tf not in TIMEFRAMES:
        tf = "1h"

    msg = await update.effective_message.reply_text("📈 در حال رسم نمودار...")

    async def send(buf, caption):
        await update.effective_message.reply_photo(
            photo=buf, caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb_after_chart(sym),
        )
        try: await msg.delete()
        except Exception: pass

    async def fail():
        await msg.edit_text(
            "⚠️ دریافت داده ناموفق.\n/chart XAUUSD 4h",
            reply_markup=kb_main()
        )

    await _send_chart(sym, tf, send, fail)

async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reg(update)
    await update.effective_message.reply_text(
        build_alerts_text(update.effective_user.id),
        parse_mode=ParseMode.HTML, reply_markup=kb_alerts()
    )

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("pending_alert", None)
    await update.effective_message.reply_text("❌ لغو شد.", reply_markup=kb_main())

# ══════════════════════════════════════════════════════════════════════
# CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════════
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    await q.answer()
    d   = q.data
    uid = update.effective_user.id
    cloud.inc(uid)

    # helper: edit پیام فعلی
    async def edit(text, kb=None):
        kw = {"parse_mode": ParseMode.HTML}
        if kb is not None:
            kw["reply_markup"] = kb
        await q.edit_message_text(text, **kw)

    # ── main ──
    if d == "main":
        await edit("🏠 <b>منوی اصلی</b>\n\nیک گزینه انتخاب کنید:", kb_main()); return

    # ── all prices ──
    if d == "all":
        await edit("⏳")
        await edit(build_all_prices(), kb_main()); return

    # ── stats ──
    if d == "stats":
        u, r = cloud.stats()
        await edit(f"📊 <b>آمار</b>\n\n👥 کاربران: <b>{u}</b>\n📈 درخواست‌ها: <b>{r}</b>\n\n⏱ {str_ir()}", kb_main())
        return

    # ── help ──
    if d == "help":
        await edit(
            "❓ <b>راهنما</b>\n\n"
            "/start — منو\n/price — همه قیمت‌ها\n"
            "/chart [sym] [tf] — نمودار\n/alerts — هشدارها\n/cancel — لغو\n\n"
            "<b>tf:</b> 1h | 4h | 1d | 5d | 1mo\n"
            "<b>sym:</b> XAUUSD | EURUSD | USDJPY | GBPUSD\n\n"
            "• قیمت از ۳ منبع با fallback\n"
            "• SMA20/50 + RSI روی چارت\n"
            "• هشدار قیمت (هر ۵ دقیقه)\n"
            "• گروه + پیوی",
            kb_main()
        )
        return

    # ── asset menu ──
    if d.startswith("a:"):
        sym = d[2:]
        if sym not in ASSETS: return
        a = ASSETS[sym]
        p = get_price(sym)
        if p:
            cloud.push_price(sym, p)
            body = _price_line(sym, p)
        else:
            body = f"{a.emoji} <b>{a.symbol}</b>\n⚠️ خطا\n"
        await edit(f"{body}\n⏱ {str_ir()}\n\nیک گزینه انتخاب کنید:", kb_asset(sym))
        return

    # ── price refresh ──
    if d.startswith("px:"):
        sym = d[3:]
        if sym not in ASSETS: return
        a = ASSETS[sym]
        p = get_price(sym, force=True)
        if p:
            cloud.push_price(sym, p)
            body = _price_line(sym, p)
        else:
            body = f"{a.emoji} <b>{a.symbol}</b>\n⚠️ خطا\n"
        await edit(f"{body}\n⏱ {str_ir()}\n\nیک گزینه انتخاب کنید:", kb_asset(sym))
        return

    # ── chart menu ──
    if d == "chart_menu":
        await edit("📈 <b>انتخاب نمودار</b>\n\nدارایی را انتخاب کنید:", kb_chart_menu())
        return

    # ── chart → tf picker ──
    if d.startswith("chart:") and d.count(":") == 1:
        sym = d[6:]
        if sym not in ASSETS: return
        a = ASSETS[sym]
        await edit(
            f"📈 <b>تایم‌فریم — {a.symbol}</b>\n\n"
            f"💡 برای اسکلپ: ⚡ 1H (هر ۲ دقیقه)",
            kb_tf(sym)
        )
        return

    # ── chart with tf — FIX: دکمه‌ها روی پیام عکس, پیام قبلی edit میشه ──
    if d.startswith("tf:"):
        parts = d.split(":")
        sym, tf_key = parts[1], parts[2]
        if sym not in ASSETS or tf_key not in TIMEFRAMES: return
        a = ASSETS[sym]

        # ابتدا پیام فعلی رو edit کن (loading) تا کاربر بفهمه در حال پردازشه
        await edit(f"📈 رسم نمودار {a.symbol}  {TIMEFRAMES[tf_key][2]}…")

        async def send(buf, caption):
            # reply_photo روی چت — نه روی پیام loading
            await q.message.reply_photo(
                photo=buf, caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=kb_after_chart(sym),
            )
            # حذف پیام loading
            try: await q.message.delete()
            except Exception: pass

        async def fail():
            await q.message.reply_text(
                f"⚠️ دریافت داده ناموفق برای {a.symbol}.\n"
                f"تایم‌فریم دیگری امتحان کنید.",
                reply_markup=kb_tf(sym)
            )
            try: await q.message.delete()
            except Exception: pass

        await _send_chart(sym, tf_key, send, fail)
        return

    # ── alert up/down ──
    if d.startswith("al_up:") or d.startswith("al_dn:"):
        direction = "up" if d.startswith("al_up") else "down"
        sym = d.split(":")[1]
        if sym not in ASSETS: return
        a = ASSETS[sym]
        p = get_price(sym)
        ctx.user_data["pending_alert"] = {"sym": sym, "dir": direction}
        dir_fa = "صعودی ⬆️" if direction == "up" else "نزولی ⬇️"
        hint = f"قیمت فعلی: <code>{p:.{a.decimals}f}</code>\n\n" if p else ""
        example = "3350" if sym == "XAUUSD" else ("155.00" if sym == "USDJPY" else "1.0850")
        await edit(
            f"🔔 <b>هشدار {dir_fa} — {a.symbol}</b>\n\n"
            f"{hint}"
            f"قیمت هدف را ارسال کنید:\n"
            f"مثال: <code>{example}</code>\n\n"
            f"❌ /cancel برای لغو"
        )
        return

    # ── my alerts ──
    if d == "my_alerts":
        await edit(build_alerts_text(uid), kb_alerts()); return

    # ── clean alerts ──
    if d == "alerts_clean":
        cloud.clear_done(uid)
        await edit(build_alerts_text(uid) + "\n\n✅ تریگرشده‌ها پاک شدند.", kb_alerts())
        return

# ══════════════════════════════════════════════════════════════════════
# MESSAGE HANDLER (alert target input)
# ══════════════════════════════════════════════════════════════════════
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pending = ctx.user_data.get("pending_alert")
    if not pending:
        return
    txt = update.effective_message.text.strip()
    if txt.startswith("/"):
        return
    try:
        target = float(txt.replace(",", "."))
    except ValueError:
        await update.effective_message.reply_text(
            "⚠️ عدد معتبر وارد کنید.\nمثال: <code>3350</code>\n/cancel برای لغو",
            parse_mode=ParseMode.HTML
        )
        return

    sym = pending["sym"]
    direction = pending["dir"]
    a = ASSETS[sym]
    cloud.add_alert(update.effective_user.id, sym, direction, target)
    ctx.user_data.pop("pending_alert", None)
    dir_fa = "بالا رفتن ⬆️" if direction == "up" else "پایین آمدن ⬇️"
    await update.effective_message.reply_text(
        f"✅ <b>هشدار ثبت شد!</b>\n\n"
        f"{a.emoji} <b>{a.symbol}</b>\n"
        f"🎯 هدف: <code>{target:.{a.decimals}f}</code>  |  {dir_fa}\n\n"
        f"<i>هر ۵ دقیقه بررسی می‌شود.</i>",
        parse_mode=ParseMode.HTML, reply_markup=kb_main()
    )

# ══════════════════════════════════════════════════════════════════════
# ALERT CHECKER
# ══════════════════════════════════════════════════════════════════════
async def check_alerts(app: Application):
    try:
        with cloud._lock:
            alerts_snapshot = {
                uid: {sym: list(al) for sym, al in sym_map.items()}
                for uid, sym_map in cloud._data.get("alerts", {}).items()
            }
    except Exception as e:
        log.error(f"alert snapshot: {e}"); return

    for uid_str, sym_map in alerts_snapshot.items():
        uid = int(uid_str)
        for sym, al_list in sym_map.items():
            if not al_list or sym not in ASSETS: continue
            price = get_price(sym)
            if price is None: continue
            a = ASSETS[sym]
            for idx, al in enumerate(al_list):
                if al.get("done"): continue
                target = al["target"]
                fired = (al["dir"] == "up" and price >= target) or \
                        (al["dir"] == "down" and price <= target)
                if not fired: continue
                arrow = "📈" if al["dir"] == "up" else "📉"
                dir_fa = "بالا رفت ⬆️" if al["dir"] == "up" else "پایین آمد ⬇️"
                try:
                    await app.bot.send_message(
                        chat_id=uid, parse_mode=ParseMode.HTML,
                        text=(
                            f"{arrow} <b>هشدار قیمتی!</b>\n\n"
                            f"{a.emoji} <b>{a.symbol}</b>\n\n"
                            f"💰 قیمت: <code>{price:.{a.decimals}f}</code>\n"
                            f"🎯 هدف: <code>{target:.{a.decimals}f}</code>\n\n"
                            f"✅ {dir_fa}"
                        )
                    )
                    cloud.mark_done(uid, sym, idx)
                    log.info(f"alert fired uid={uid} {sym} {price}")
                except Exception as e:
                    log.error(f"alert send: {e}")

# ══════════════════════════════════════════════════════════════════════
# BACKGROUND LOOP
# ══════════════════════════════════════════════════════════════════════
async def bg_loop(app: Application):
    while True:
        try:
            await asyncio.sleep(300)
            log.info("bg tick")
            for sym in ASSETS:
                p = get_price(sym, force=True)
                if p: cloud.push_price(sym, p)
            await check_alerts(app)
            cloud.save(force=True)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"bg_loop: {e}")

# ══════════════════════════════════════════════════════════════════════
# LIFECYCLE
# ══════════════════════════════════════════════════════════════════════
async def on_startup(app: Application):
    threading.Thread(target=_run_flask, daemon=True).start()
    log.info(f"Flask on :{PORT}")
    asyncio.create_task(bg_loop(app))
    for sym in ASSETS:
        p = get_price(sym)
        if p: cloud.push_price(sym, p)
    log.info("warmup done")

async def on_shutdown(app: Application):
    cloud.save(force=True)
    log.info("shutdown")

async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.error(f"error: {ctx.error}", exc_info=ctx.error)

# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKEN:
        log.critical("BOT_TOKEN not set!")
        sys.exit(1)

    cloud.load()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_start))
    app.add_handler(CommandHandler("price",  cmd_price))
    app.add_handler(CommandHandler("chart",  cmd_chart))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)

    def _sig(s, f):
        cloud.save(force=True); sys.exit(0)
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)
    atexit.register(lambda: cloud.save(force=True))

    log.info("ScalpBot Pro v2 starting...")
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
