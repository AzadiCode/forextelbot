#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║           🚀 ScalpBot Pro — Forex & Gold Telegram Bot        ║
║           Designed for serious scalpers                      ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, sys, json, asyncio, logging, signal, atexit, io, threading
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

import requests, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np
from flask import Flask, jsonify

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

# ══════════════════════════════════════════════════════════════════════
# ⚙️  LOGGING
# ══════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("ScalpBot")

# ══════════════════════════════════════════════════════════════════════
# ⚙️  CONFIG
# ══════════════════════════════════════════════════════════════════════
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
PORT           = int(os.environ.get("PORT", 5000))
JSONBIN_KEY    = os.environ.get("JSONBIN_API_KEY", "")
JSONBIN_BIN    = os.environ.get("JSONBIN_BIN_ID", "")

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))

def now_ir() -> datetime:
    return datetime.now(IRAN_TZ)

def str_ir(fmt="%H:%M:%S") -> str:
    return now_ir().strftime(fmt)

# ══════════════════════════════════════════════════════════════════════
# 💱  ASSET DEFINITIONS
# ══════════════════════════════════════════════════════════════════════
@dataclass
class Source:
    name: str
    url: str
    parser: callable
    min_val: float
    max_val: float

@dataclass
class Asset:
    key: str
    name: str
    emoji: str
    symbol: str
    decimals: int
    pip_size: float          # اندازه یک pip (برای نمایش spread)
    sources: list[Source]
    yahoo_spot: str          # نماد Yahoo Finance Spot
    yahoo_futures: str       # نماد Yahoo Finance Futures (برای چارت)

ASSETS: dict[str, Asset] = {
    "XAUUSD": Asset(
        key="XAUUSD", name="طلا", emoji="🥇", symbol="XAU/USD",
        decimals=2, pip_size=0.1,
        yahoo_spot="XAUUSD=X", yahoo_futures="GC=F",
        sources=[
            Source("GoldPrice.org",
                   "https://data-asg.goldprice.org/dbXRates/USD",
                   lambda d: d["items"][0]["xauPrice"], 1000, 10000),
            Source("Swissquote",
                   "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD",
                   lambda d: (float(d[0]["spreadProfilePrices"][0]["ask"]) +
                              float(d[0]["spreadProfilePrices"][0]["bid"])) / 2, 1000, 10000),
            Source("Yahoo Spot",
                   "https://query1.finance.yahoo.com/v8/finance/chart/XAUUSD=X",
                   lambda d: d["chart"]["result"][0]["meta"]["regularMarketPrice"], 1000, 10000),
        ],
    ),
    "EURUSD": Asset(
        key="EURUSD", name="EUR/USD", emoji="💶", symbol="EUR/USD",
        decimals=5, pip_size=0.0001,
        yahoo_spot="EURUSD=X", yahoo_futures="EURUSD=X",
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
        yahoo_spot="USDJPY=X", yahoo_futures="USDJPY=X",
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
        yahoo_spot="GBPUSD=X", yahoo_futures="GBPUSD=X",
        sources=[
            Source("Swissquote",
                   "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/GBP/USD",
                   lambda d: (float(d[0]["spreadProfilePrices"][0]["ask"]) +
                              float(d[0]["spreadProfilePrices"][0]["bid"])) / 2, 1.1, 1.5),
            Source("Yahoo",
                   "https://query1.finance.yahoo.com/v8/finance/chart/GBPUSD=X",
                   lambda d: d["chart"]["result"][0]["meta"]["regularMarketPrice"], 1.1, 1.5),
        ],
    ),
}

# ══════════════════════════════════════════════════════════════════════
# 📡  PRICE FETCHER  (با cache و retry)
# ══════════════════════════════════════════════════════════════════════
CACHE_TTL = 15  # ثانیه — cache قیمت

_price_cache: dict[str, tuple[float, float]] = {}  # {symbol: (price, timestamp)}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

def get_price(symbol: str, force: bool = False) -> Optional[float]:
    """دریافت قیمت لحظه‌ای با کش ۱۵ ثانیه و fallback چند منبعی."""
    if symbol not in ASSETS:
        return None

    # بررسی cache
    if not force and symbol in _price_cache:
        price, ts = _price_cache[symbol]
        if time.time() - ts < CACHE_TTL:
            return price

    asset = ASSETS[symbol]
    for src in asset.sources:
        try:
            resp = requests.get(src.url, headers=_HEADERS, timeout=10)
            resp.raise_for_status()
            price = float(src.parser(resp.json()))
            if src.min_val < price < src.max_val:
                _price_cache[symbol] = (price, time.time())
                log.info(f"✅ {symbol} @ {price} [{src.name}]")
                return round(price, asset.decimals)
        except Exception as e:
            log.warning(f"⚠️ {symbol}/{src.name}: {e!s:.80}")

    log.error(f"❌ {symbol}: همه منابع ناموفق")
    return None

# ══════════════════════════════════════════════════════════════════════
# 📊  CHART ENGINE
# ══════════════════════════════════════════════════════════════════════

TIMEFRAMES = {
    # label: (range, interval, bar_label)
    "1h":  ("1h",  "1m",  "۱ ساعت / هر دقیقه"),
    "4h":  ("4h",  "5m",  "۴ ساعت / هر ۵ دقیقه"),
    "1d":  ("1d",  "15m", "۱ روز / هر ۱۵ دقیقه"),
    "5d":  ("5d",  "1h",  "۵ روز / هر ساعت"),
    "1mo": ("1mo", "1d",  "۱ ماه / روزانه"),
}

def _fetch_yahoo(yahoo_sym: str, interval: str, range_: str):
    """دریافت داده تاریخی از Yahoo Finance — برمی‌گرداند (times_ir, closes, highs, lows, opens) یا None."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}"
    params = {"interval": interval, "range": range_}
    try:
        resp = requests.get(url, headers=_HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        res = data["chart"]["result"][0]
        ts_list = res["timestamp"]
        q = res["indicators"]["quote"][0]

        times, opens, highs, lows, closes = [], [], [], [], []
        for i, ts in enumerate(ts_list):
            c = q["close"][i]
            o = q["open"][i]
            h = q["high"][i]
            l = q["low"][i]
            if None in (c, o, h, l):
                continue
            # تبدیل به تایم ایران
            dt = datetime.fromtimestamp(ts, tz=IRAN_TZ)
            times.append(dt)
            opens.append(float(o))
            highs.append(float(h))
            lows.append(float(l))
            closes.append(float(c))

        if len(closes) < 5:
            return None
        log.info(f"✅ Yahoo {yahoo_sym} {range_}/{interval}: {len(closes)} candles")
        return times, opens, highs, lows, closes
    except Exception as e:
        log.error(f"❌ Yahoo {yahoo_sym}: {e!s:.100}")
        return None

def _sma(data: list, period: int) -> list:
    result = [None] * len(data)
    for i in range(period - 1, len(data)):
        result[i] = sum(data[i - period + 1 : i + 1]) / period
    return result

def _rsi(closes: list, period: int = 14) -> list:
    """RSI ساده"""
    rsi = [None] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
        if i >= period:
            avg_g = sum(gains[-period:]) / period
            avg_l = sum(losses[-period:]) / period
            if avg_l == 0:
                rsi[i] = 100.0
            else:
                rs = avg_g / avg_l
                rsi[i] = 100 - 100 / (1 + rs)
    return rsi

def create_chart(symbol: str, tf_key: str = "1h") -> Optional[io.BytesIO]:
    """
    رسم نمودار حرفه‌ای با:
    - خط قیمت (line chart سریع‌تر از کندل برای موبایل)
    - SMA 20 & 50
    - RSI پانل پایین
    - نمایش واضح HIGH / LOW / تغییر
    """
    if symbol not in ASSETS or tf_key not in TIMEFRAMES:
        return None

    asset = ASSETS[symbol]
    range_, interval, bar_label = TIMEFRAMES[tf_key]

    result = _fetch_yahoo(asset.yahoo_spot, interval, range_)
    if result is None:
        # fallback به futures برای طلا
        if asset.yahoo_futures != asset.yahoo_spot:
            result = _fetch_yahoo(asset.yahoo_futures, interval, range_)
    if result is None:
        return None

    times, opens, highs, lows, closes = result
    dec = asset.decimals

    # ── آمار ──
    cur   = closes[-1]
    start = closes[0]
    hi    = max(highs)
    lo    = min(lows)
    chg   = cur - start
    chg_p = (chg / start) * 100 if start else 0
    is_up = chg >= 0
    clr   = "#00e676" if is_up else "#ff1744"
    clr_dim = "#00c853" if is_up else "#d50000"

    # ── اندیکاتورها ──
    sma20 = _sma(closes, min(20, len(closes)))
    sma50 = _sma(closes, min(50, len(closes)))
    rsi_vals = _rsi(closes)

    # ── Figure ──
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
    fig = plt.figure(figsize=(14, 9), facecolor="#0b0e14")
    gs = fig.add_gridspec(
        3, 1, height_ratios=[4, 1, 1.2],
        hspace=0.04, left=0.07, right=0.97, top=0.92, bottom=0.07
    )
    ax_price = fig.add_subplot(gs[0])
    ax_vol   = fig.add_subplot(gs[1], sharex=ax_price)
    ax_rsi   = fig.add_subplot(gs[2], sharex=ax_price)

    for ax in (ax_price, ax_vol, ax_rsi):
        ax.set_facecolor("#0b0e14")
        ax.tick_params(colors="#546e7a", labelsize=8)
        ax.yaxis.tick_right()
        for spine in ax.spines.values():
            spine.set_color("#1c2430")

    # ── قیمت ──
    ax_price.plot(times, closes, color=clr, linewidth=1.8, zorder=3)
    ax_price.fill_between(times, closes, lo * 0.999,
                          color=clr, alpha=0.07, zorder=1)

    # SMA lines
    valid_t20 = [t for t, v in zip(times, sma20) if v is not None]
    valid_s20 = [v for v in sma20 if v is not None]
    valid_t50 = [t for t, v in zip(times, sma50) if v is not None]
    valid_s50 = [v for v in sma50 if v is not None]

    if valid_t20:
        ax_price.plot(valid_t20, valid_s20, color="#ffa726", linewidth=1,
                      alpha=0.8, linestyle="--", label="SMA 20", zorder=2)
    if valid_t50:
        ax_price.plot(valid_t50, valid_s50, color="#ab47bc", linewidth=1,
                      alpha=0.8, linestyle="--", label="SMA 50", zorder=2)

    # خط قیمت فعلی
    ax_price.axhline(cur, color=clr, linestyle="-", alpha=0.5, linewidth=0.8)

    # Price box سمت راست
    ax_price.annotate(
        f"{cur:.{dec}f}",
        xy=(1, cur), xycoords=("axes fraction", "data"),
        fontsize=10, fontweight="bold", color="#0b0e14",
        ha="left", va="center",
        bbox=dict(boxstyle="round,pad=0.35", fc=clr, ec="none"),
    )

    # HIGH / LOW markers
    hi_idx = highs.index(hi)
    lo_idx = lows.index(lo)
    ax_price.annotate(
        f"H: {hi:.{dec}f}",
        xy=(times[hi_idx], hi),
        xytext=(0, 10), textcoords="offset points",
        fontsize=8, color="#ffa726", fontweight="bold", ha="center",
        arrowprops=dict(arrowstyle="-", color="#ffa726", lw=0.7),
    )
    ax_price.annotate(
        f"L: {lo:.{dec}f}",
        xy=(times[lo_idx], lo),
        xytext=(0, -12), textcoords="offset points",
        fontsize=8, color="#29b6f6", fontweight="bold", ha="center",
        arrowprops=dict(arrowstyle="-", color="#29b6f6", lw=0.7),
    )

    # grid
    ax_price.grid(True, color="#1c2430", linewidth=0.7, linestyle="-")
    ax_price.legend(loc="upper left", fontsize=8,
                    facecolor="#0b0e14", edgecolor="#1c2430", labelcolor="white")

    # Padding دقیق
    price_range = hi - lo
    pad = max(price_range * 0.08, cur * 0.001)
    ax_price.set_ylim(lo - pad, hi + pad * 1.5)

    # ── عنوان ──
    sign = "+" if is_up else ""
    arrow = "▲" if is_up else "▼"
    fig.text(
        0.5, 0.96,
        f"{asset.emoji}  {asset.symbol}  |  {asset.name}  |  {bar_label}",
        ha="center", va="top", fontsize=12, fontweight="bold",
        color="white",
    )
    fig.text(
        0.5, 0.935,
        f"{arrow} {cur:.{dec}f}    {sign}{chg:.{dec}f}  ({sign}{chg_p:.2f}%)"
        f"    H: {hi:.{dec}f}  L: {lo:.{dec}f}",
        ha="center", va="top", fontsize=10,
        color=clr,
    )

    # ── Volume (ارتفاع کندل = نوسان) ──
    vol_vals = [abs(h - l) for h, l in zip(highs, lows)]
    bar_colors = [clr if c >= o else "#ff1744"
                  for c, o in zip(closes, opens)]
    ax_vol.bar(times, vol_vals, color=bar_colors, alpha=0.6, width=0.002)
    ax_vol.set_ylabel("Range", color="#546e7a", fontsize=7)
    ax_vol.grid(True, color="#1c2430", linewidth=0.5)

    # ── RSI ──
    valid_rsi_t = [t for t, v in zip(times, rsi_vals) if v is not None]
    valid_rsi_v = [v for v in rsi_vals if v is not None]

    if valid_rsi_v:
        rsi_colors = ["#00e676" if v >= 50 else "#ff1744" for v in valid_rsi_v]
        ax_rsi.plot(valid_rsi_t, valid_rsi_v, color="#e040fb",
                    linewidth=1.3, zorder=3)
        ax_rsi.axhline(70, color="#ff1744", linestyle="--", alpha=0.5, linewidth=0.8)
        ax_rsi.axhline(30, color="#00e676", linestyle="--", alpha=0.5, linewidth=0.8)
        ax_rsi.fill_between(valid_rsi_t, valid_rsi_v, 50,
                             where=[v >= 50 for v in valid_rsi_v],
                             color="#00e676", alpha=0.08)
        ax_rsi.fill_between(valid_rsi_t, valid_rsi_v, 50,
                             where=[v < 50 for v in valid_rsi_v],
                             color="#ff1744", alpha=0.08)
        ax_rsi.set_ylim(0, 100)
        ax_rsi.set_yticks([30, 50, 70])
        ax_rsi.set_ylabel("RSI", color="#546e7a", fontsize=7)

        last_rsi = valid_rsi_v[-1]
        rsi_clr = "#00e676" if last_rsi >= 50 else "#ff1744"
        ax_rsi.annotate(
            f"{last_rsi:.1f}",
            xy=(1, last_rsi), xycoords=("axes fraction", "data"),
            fontsize=8, color="#0b0e14", ha="left", va="center",
            bbox=dict(boxstyle="round,pad=0.25", fc=rsi_clr, ec="none"),
        )
    ax_rsi.grid(True, color="#1c2430", linewidth=0.5)

    # ── محور X ──
    if tf_key in ("1h", "4h", "1d"):
        fmt = mdates.DateFormatter("%H:%M", tz=IRAN_TZ)
    else:
        fmt = mdates.DateFormatter("%m/%d", tz=IRAN_TZ)
    ax_rsi.xaxis.set_major_formatter(fmt)
    plt.setp(ax_rsi.xaxis.get_majorticklabels(), rotation=35, ha="right")
    plt.setp(ax_price.xaxis.get_majorticklabels(), visible=False)
    plt.setp(ax_vol.xaxis.get_majorticklabels(), visible=False)

    # ── Watermark ──
    fig.text(0.97, 0.02, "@ScalpBot", ha="right", va="bottom",
             fontsize=7, color="#1c2430", fontweight="bold")

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor="#0b0e14")
    buf.seek(0)
    plt.close(fig)
    return buf

# ══════════════════════════════════════════════════════════════════════
# ☁️  CLOUD STORAGE  (JSONBin.io)
# ══════════════════════════════════════════════════════════════════════

class CloudStorage:
    _DEFAULT = lambda self: {
        "users": {},
        "total_requests": 0,
        "price_history": {s: [] for s in ASSETS},
        "alerts": {},
        "last_updated": None,
    }

    def __init__(self):
        self._key    = JSONBIN_KEY
        self._bin    = JSONBIN_BIN
        self._url    = "https://api.jsonbin.io/v3/b"
        self._hdrs   = {"X-Master-Key": self._key, "Content-Type": "application/json"}
        self._lock   = threading.Lock()
        self._dirty  = False
        self._data   = self._DEFAULT()

    # ── internal helpers ──
    def _get(self, *keys, default=None):
        d = self._data
        for k in keys:
            if not isinstance(d, dict) or k not in d:
                return default
            d = d[k]
        return d

    def _set(self, *keys_and_value):
        *keys, value = keys_and_value
        d = self._data
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value
        self._dirty = True

    # ── public API ──
    def load(self):
        if not (self._key and self._bin):
            log.info("☁️ CloudStorage: کانفیگ نشده — local only")
            return
        try:
            r = requests.get(f"{self._url}/{self._bin}/latest",
                             headers=self._hdrs, timeout=15)
            r.raise_for_status()
            remote = r.json().get("record", {})
            # merge ایمن
            with self._lock:
                for k, v in remote.items():
                    self._data[k] = v
                # اطمینان از وجود تمام کلیدها
                for sym in ASSETS:
                    self._data.setdefault("price_history", {})\
                              .setdefault(sym, [])
                self._data.setdefault("alerts", {})
            log.info("☁️ داده‌ها بارگذاری شد.")
        except Exception as e:
            log.error(f"☁️ خطا در بارگذاری: {e}")

    def save(self, force=False):
        if not self._key:
            return
        if not force and not self._dirty:
            return
        with self._lock:
            self._data["last_updated"] = now_ir().isoformat()
            payload = dict(self._data)
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
                    log.info(f"☁️ Bin جدید: {bid}")
            r.raise_for_status()
            self._dirty = False
        except Exception as e:
            log.error(f"☁️ خطا در ذخیره: {e}")

    # ── users ──
    def ensure_user(self, uid: int, username: str = ""):
        key = str(uid)
        with self._lock:
            if key not in self._data["users"]:
                self._data["users"][key] = {
                    "username": username,
                    "joined": now_ir().isoformat(),
                    "requests": 0,
                }
                self._dirty = True

    def inc_requests(self, uid: int):
        key = str(uid)
        with self._lock:
            if key in self._data["users"]:
                self._data["users"][key]["requests"] += 1
            self._data["total_requests"] = self._data.get("total_requests", 0) + 1
            self._dirty = True

    def stats(self):
        return len(self._data.get("users", {})), self._data.get("total_requests", 0)

    # ── price history ──
    def push_price(self, symbol: str, price: float):
        with self._lock:
            hist = self._data.setdefault("price_history", {}).setdefault(symbol, [])
            hist.append({"p": price, "t": now_ir().isoformat()})
            if len(hist) > 200:
                self._data["price_history"][symbol] = hist[-200:]
            self._dirty = True

    def get_history(self, symbol: str) -> list:
        return list(self._data.get("price_history", {}).get(symbol, []))

    # ── alerts ──
    def add_alert(self, uid: int, symbol: str, direction: str, target: float):
        key = str(uid)
        with self._lock:
            user_alerts = self._data.setdefault("alerts", {}).setdefault(key, {})
            sym_alerts  = user_alerts.setdefault(symbol, [])
            sym_alerts.append({
                "dir": direction,
                "target": target,
                "at": now_ir().isoformat(),
                "done": False,
            })
            self._dirty = True

    def get_alerts(self, uid: int, symbol: str = None) -> dict | list:
        key = str(uid)
        all_a = self._data.get("alerts", {}).get(key, {})
        if symbol:
            return list(all_a.get(symbol, []))
        return dict(all_a)

    def count_active_alerts(self, uid: int) -> int:
        all_a = self.get_alerts(uid)
        return sum(
            1 for sym_list in all_a.values()
            for a in sym_list if not a.get("done")
        )

    def mark_done(self, uid: int, symbol: str, idx: int):
        key = str(uid)
        try:
            with self._lock:
                self._data["alerts"][key][symbol][idx]["done"] = True
                self._dirty = True
        except Exception:
            pass

    def remove_alert(self, uid: int, symbol: str, idx: int):
        key = str(uid)
        try:
            with self._lock:
                del self._data["alerts"][key][symbol][idx]
                self._dirty = True
        except Exception:
            pass

    def clear_done_alerts(self, uid: int):
        key = str(uid)
        with self._lock:
            for sym in list(self._data.get("alerts", {}).get(key, {}).keys()):
                self._data["alerts"][key][sym] = [
                    a for a in self._data["alerts"][key][sym] if not a.get("done")
                ]
            self._dirty = True

cloud = CloudStorage()

# ══════════════════════════════════════════════════════════════════════
# 🌐  FLASK  (keep-alive)
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route("/")
def root():
    u, r = cloud.stats()
    return f"🚀 ScalpBot Pro | users={u} | requests={r} | {str_ir()}"

@flask_app.route("/health")
def health():
    return jsonify(status="ok", time=str_ir())

def _run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════
# ⌨️  KEYBOARDS
# ══════════════════════════════════════════════════════════════════════

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🥇 طلا",    callback_data="a:XAUUSD"),
            InlineKeyboardButton("💶 EUR/USD", callback_data="a:EURUSD"),
        ],
        [
            InlineKeyboardButton("💴 USD/JPY", callback_data="a:USDJPY"),
            InlineKeyboardButton("💷 GBP/USD", callback_data="a:GBPUSD"),
        ],
        [
            InlineKeyboardButton("📊 همه قیمت‌ها",  callback_data="all"),
            InlineKeyboardButton("🔔 هشدارهای من",  callback_data="my_alerts"),
        ],
        [
            InlineKeyboardButton("📈 نمودار",  callback_data="chart_menu"),
            InlineKeyboardButton("📉 آمار",    callback_data="stats"),
            InlineKeyboardButton("❓ راهنما",  callback_data="help"),
        ],
    ])

def kb_asset(symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 قیمت لحظه‌ای 🔄", callback_data=f"px:{symbol}")],
        [InlineKeyboardButton("📈 نمودار",           callback_data=f"chart:{symbol}")],
        [
            InlineKeyboardButton("⬆️ هشدار صعودی",  callback_data=f"al_up:{symbol}"),
            InlineKeyboardButton("⬇️ هشدار نزولی",  callback_data=f"al_dn:{symbol}"),
        ],
        [InlineKeyboardButton("🔙 منوی اصلی", callback_data="main")],
    ])

def kb_tf(symbol: str) -> InlineKeyboardMarkup:
    rows = []
    for key, (_, _, label) in TIMEFRAMES.items():
        rows.append([InlineKeyboardButton(f"⏱ {label}", callback_data=f"tf:{symbol}:{key}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"a:{symbol}")])
    return InlineKeyboardMarkup(rows)

def kb_chart_menu() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{ASSETS[s].emoji} {ASSETS[s].symbol}", callback_data=f"chart:{s}")]
            for s in ASSETS]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="main")])
    return InlineKeyboardMarkup(rows)

def kb_alerts_manage(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 پاک کردن تریگرشده‌ها", callback_data="alerts_clean")],
        [InlineKeyboardButton("🔙 منوی اصلی", callback_data="main")],
    ])

# ══════════════════════════════════════════════════════════════════════
# 📝  MESSAGE BUILDERS
# ══════════════════════════════════════════════════════════════════════

def _fmt_price(symbol: str, price: float) -> str:
    """قیمت فرمت‌شده با pip info"""
    a = ASSETS[symbol]
    dec = a.decimals
    # محاسبه تغییر با آخرین قیمت ذخیره‌شده
    hist = cloud.get_history(symbol)
    if len(hist) >= 2:
        prev = hist[-2]["p"]
        diff = price - prev
        pips = round(diff / a.pip_size)
        sign = "+" if diff >= 0 else ""
        clr_tag = "▲" if diff >= 0 else "▼"
        diff_str = f"\n   {clr_tag} <code>{sign}{diff:.{dec}f}</code>  ({sign}{pips} pips)"
    else:
        diff_str = ""
    return (
        f"{a.emoji} <b>{a.symbol}</b>\n"
        f"   💰 <code>{price:.{dec}f}</code>{diff_str}\n"
    )

def build_all_prices() -> str:
    lines = ["💱 <b>قیمت‌های لحظه‌ای</b>\n"]
    for sym in ASSETS:
        p = get_price(sym)
        if p:
            cloud.push_price(sym, p)
            lines.append(_fmt_price(sym, p))
        else:
            lines.append(f"{ASSETS[sym].emoji} <b>{ASSETS[sym].symbol}</b>\n   ⚠️ <i>خطا</i>\n")
    lines.append(f"\n⏱ <i>{str_ir()}</i>")
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
            status = "✅" if al.get("done") else "⏳"
            direction = "⬆️" if al["dir"] == "up" else "⬇️"
            lines.append(
                f"{status} {a.emoji} {a.symbol}  {direction}  "
                f"<code>{al['target']:.{a.decimals}f}</code>"
            )
    lines.append(f"\n<i>هر ۵ دقیقه بررسی می‌شوند</i>")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════
# 🤖  HANDLERS
# ══════════════════════════════════════════════════════════════════════

async def _register(update: Update):
    u = update.effective_user
    cloud.ensure_user(u.id, u.username or "")
    cloud.inc_requests(u.id)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _register(update)
    text = (
        "👋 <b>ScalpBot Pro</b>\n\n"
        "ربات حرفه‌ای قیمت لحظه‌ای فارکس و طلا — "
        "طراحی‌شده برای <b>اسکلپرها</b>.\n\n"
        "📌 <b>دارایی‌ها:</b>\n"
        "• 🥇 XAU/USD — طلا\n"
        "• 💶 EUR/USD — یورو\n"
        "• 💴 USD/JPY — ین\n"
        "• 💷 GBP/USD — پوند\n\n"
        "از منوی زیر شروع کنید 👇"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                    reply_markup=kb_main())

async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _register(update)
    msg = await update.message.reply_text("⏳ در حال دریافت قیمت‌ها...")
    text = build_all_prices()
    await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_main())

async def cmd_chart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _register(update)
    args = ctx.args
    sym = (args[0].upper() if args else "XAUUSD")
    if sym not in ASSETS:
        sym = "XAUUSD"
    await update.message.reply_chat_action("upload_photo")
    buf = create_chart(sym, "1h")
    if buf:
        await update.message.reply_photo(
            photo=buf,
            caption=f"{ASSETS[sym].emoji} <b>{ASSETS[sym].symbol}</b> — ۱ ساعت\n⏱ {str_ir()}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_tf(sym),
        )
    else:
        await update.message.reply_text("⚠️ داده کافی برای نمودار نیست.",
                                        reply_markup=kb_main())

async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _register(update)
    text = build_alerts_text(update.effective_user.id)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                    reply_markup=kb_alerts_manage(update.effective_user.id))

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("pending_alert", None)
    await update.message.reply_text("❌ لغو شد.", reply_markup=kb_main())

# ── Callback ──
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid  = update.effective_user.id
    cloud.inc_requests(uid)

    # ── main menu ──
    if data == "main":
        await q.edit_message_text(
            "🏠 <b>منوی اصلی</b>\n\nیک گزینه انتخاب کنید:",
            parse_mode=ParseMode.HTML, reply_markup=kb_main(),
        )
        return

    # ── all prices ──
    if data == "all":
        await q.edit_message_text("⏳ ...", parse_mode=ParseMode.HTML)
        text = build_all_prices()
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_main())
        return

    # ── stats ──
    if data == "stats":
        u, r = cloud.stats()
        await q.edit_message_text(
            f"📊 <b>آمار ربات</b>\n\n👥 کاربران: <b>{u}</b>\n📈 درخواست‌ها: <b>{r}</b>\n\n⏱ {str_ir()}",
            parse_mode=ParseMode.HTML, reply_markup=kb_main(),
        )
        return

    # ── help ──
    if data == "help":
        await q.edit_message_text(
            "❓ <b>راهنما</b>\n\n"
            "<b>دستورات:</b>\n"
            "/start — منو\n"
            "/price — همه قیمت‌ها\n"
            "/chart [symbol] — نمودار\n"
            "/alerts — هشدارها\n"
            "/cancel — لغو عملیات\n\n"
            "<b>ویژگی‌ها:</b>\n"
            "• قیمت از چند منبع معتبر\n"
            "• نمودار با SMA 20/50 و RSI\n"
            "• تایم‌فریم‌های متعدد\n"
            "• هشدار قیمت هدف (هر ۵ دقیقه)\n"
            "• ۴ جفت‌ارز + طلا",
            parse_mode=ParseMode.HTML, reply_markup=kb_main(),
        )
        return

    # ── asset menu ──
    if data.startswith("a:"):
        sym = data.split(":")[1]
        if sym not in ASSETS:
            return
        a = ASSETS[sym]
        p = get_price(sym)
        if p:
            cloud.push_price(sym, p)
            body = _fmt_price(sym, p)
        else:
            body = f"{a.emoji} <b>{a.symbol}</b>\n⚠️ خطا در دریافت\n"
        await q.edit_message_text(
            f"{body}\n⏱ {str_ir()}\n\nیک گزینه انتخاب کنید:",
            parse_mode=ParseMode.HTML, reply_markup=kb_asset(sym),
        )
        return

    # ── price refresh ──
    if data.startswith("px:"):
        sym = data.split(":")[1]
        p = get_price(sym, force=True)
        a = ASSETS[sym]
        if p:
            cloud.push_price(sym, p)
            body = _fmt_price(sym, p)
        else:
            body = f"{a.emoji} <b>{a.symbol}</b>\n⚠️ خطا\n"
        await q.edit_message_text(
            f"{body}\n⏱ {str_ir()}\n\nیک گزینه انتخاب کنید:",
            parse_mode=ParseMode.HTML, reply_markup=kb_asset(sym),
        )
        return

    # ── chart menu ──
    if data == "chart_menu":
        await q.edit_message_text(
            "📈 <b>انتخاب نمودار</b>\n\nدارایی را انتخاب کنید:",
            parse_mode=ParseMode.HTML, reply_markup=kb_chart_menu(),
        )
        return

    # ── chart → timeframe picker ──
    if data.startswith("chart:") and data.count(":") == 1:
        sym = data.split(":")[1]
        if sym not in ASSETS:
            return
        a = ASSETS[sym]
        await q.edit_message_text(
            f"📈 <b>تایم‌فریم — {a.symbol}</b>\n\n"
            f"💡 برای اسکلپ: ۱ ساعت هر دقیقه",
            parse_mode=ParseMode.HTML, reply_markup=kb_tf(sym),
        )
        return

    # ── chart with timeframe ──
    if data.startswith("tf:"):
        _, sym, tf_key = data.split(":")
        if sym not in ASSETS:
            return
        a = ASSETS[sym]
        await q.edit_message_text(f"📈 در حال رسم نمودار {a.symbol}…")
        buf = create_chart(sym, tf_key)
        if buf:
            _, _, label = TIMEFRAMES[tf_key]
            await q.message.reply_photo(
                photo=buf,
                caption=(
                    f"{a.emoji} <b>{a.symbol}</b> — {label}\n"
                    f"⏱ {str_ir()}"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🔄 تایم دیگر",    callback_data=f"chart:{sym}"),
                        InlineKeyboardButton("💰 قیمت",         callback_data=f"px:{sym}"),
                    ],
                    [InlineKeyboardButton("🔙 بازگشت", callback_data=f"a:{sym}")],
                ]),
            )
            try:
                await q.message.delete()
            except Exception:
                pass
        else:
            await q.message.reply_text(
                "⚠️ دریافت داده ناموفق بود. لطفاً تایم‌فریم دیگری امتحان کنید.",
                reply_markup=kb_tf(sym),
            )
        return

    # ── alert setup ──
    if data.startswith("al_up:") or data.startswith("al_dn:"):
        direction = "up" if data.startswith("al_up") else "down"
        sym = data.split(":")[1]
        a = ASSETS[sym]
        p = get_price(sym)
        ctx.user_data["pending_alert"] = {"sym": sym, "dir": direction}
        dir_fa = "صعودی ⬆️" if direction == "up" else "نزولی ⬇️"
        hint = f"(قیمت فعلی: <code>{p:.{a.decimals}f}</code>)" if p else ""
        await q.edit_message_text(
            f"🔔 <b>هشدار {dir_fa} — {a.symbol}</b>\n\n"
            f"{hint}\n\n"
            f"قیمت هدف را وارد کنید:\n"
            f"مثال: <code>{'3350' if sym == 'XAUUSD' else '1.0850'}</code>\n\n"
            f"❌ /cancel برای لغو",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── my alerts ──
    if data == "my_alerts":
        text = build_alerts_text(uid)
        await q.edit_message_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=kb_alerts_manage(uid),
        )
        return

    # ── clean done alerts ──
    if data == "alerts_clean":
        cloud.clear_done_alerts(uid)
        text = build_alerts_text(uid)
        await q.edit_message_text(
            text + "\n\n✅ هشدارهای تریگر شده پاک شدند.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_alerts_manage(uid),
        )
        return

# ── Text messages (برای دریافت قیمت هدف) ──
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pending = ctx.user_data.get("pending_alert")
    if not pending:
        return

    txt = update.message.text.strip()
    if txt.startswith("/"):
        return  # دستورات جدا handle میشن

    try:
        target = float(txt.replace(",", "."))
    except ValueError:
        await update.message.reply_text(
            "⚠️ لطفاً یک عدد معتبر وارد کنید.\n"
            f"مثال: <code>3350</code>\n❌ /cancel",
            parse_mode=ParseMode.HTML,
        )
        return

    sym = pending["sym"]
    direction = pending["dir"]
    a = ASSETS[sym]
    cloud.add_alert(update.effective_user.id, sym, direction, target)
    ctx.user_data.pop("pending_alert", None)

    dir_fa = "بالا رفتن ⬆️" if direction == "up" else "پایین آمدن ⬇️"
    await update.message.reply_text(
        f"✅ <b>هشدار ثبت شد!</b>\n\n"
        f"{a.emoji} <b>{a.symbol}</b>\n"
        f"🎯 هدف: <code>{target:.{a.decimals}f}</code>\n"
        f"📊 جهت: {dir_fa}\n\n"
        f"<i>هر ۵ دقیقه بررسی می‌شود.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_main(),
    )

# ══════════════════════════════════════════════════════════════════════
# 🔔  ALERT CHECKER
# ══════════════════════════════════════════════════════════════════════

async def check_alerts(app: Application):
    alerts_data = dict(cloud._data.get("alerts", {}))
    for uid_str, sym_map in alerts_data.items():
        uid = int(uid_str)
        for sym, al_list in dict(sym_map).items():
            if not al_list or sym not in ASSETS:
                continue
            a = ASSETS[sym]
            price = get_price(sym)
            if price is None:
                continue
            for idx, al in enumerate(al_list):
                if al.get("done"):
                    continue
                target = al["target"]
                triggered = (al["dir"] == "up" and price >= target) or \
                            (al["dir"] == "down" and price <= target)
                if not triggered:
                    continue
                arrow = "📈" if al["dir"] == "up" else "📉"
                dir_fa = "بالا رفت ⬆️" if al["dir"] == "up" else "پایین آمد ⬇️"
                try:
                    await app.bot.send_message(
                        chat_id=uid,
                        text=(
                            f"{arrow} <b>هشدار قیمتی!</b>\n\n"
                            f"{a.emoji} <b>{a.symbol}</b>\n\n"
                            f"💰 قیمت فعلی: <code>{price:.{a.decimals}f}</code>\n"
                            f"🎯 هدف شما: <code>{target:.{a.decimals}f}</code>\n\n"
                            f"✅ قیمت {dir_fa}"
                        ),
                        parse_mode=ParseMode.HTML,
                    )
                    cloud.mark_done(uid, sym, idx)
                    log.info(f"🔔 Alert fired uid={uid} {sym} {price}")
                except Exception as e:
                    log.error(f"Alert send error: {e}")

# ══════════════════════════════════════════════════════════════════════
# 🔄  BACKGROUND TASKS
# ══════════════════════════════════════════════════════════════════════

async def background_loop(app: Application):
    """هر ۵ دقیقه: ذخیره + چک alerts + refresh prices"""
    INTERVAL = 300
    while True:
        try:
            await asyncio.sleep(INTERVAL)
            log.info("🔄 periodic tick")
            # warm-up prices
            for sym in ASSETS:
                p = get_price(sym, force=True)
                if p:
                    cloud.push_price(sym, p)
            await check_alerts(app)
            cloud.save(force=True)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"background_loop error: {e}")

# ══════════════════════════════════════════════════════════════════════
# 🚀  LIFECYCLE
# ══════════════════════════════════════════════════════════════════════

async def on_startup(app: Application):
    # Flask در thread جداگانه
    threading.Thread(target=_run_flask, daemon=True).start()
    log.info(f"🌐 Flask on :{PORT}")

    asyncio.create_task(background_loop(app))
    log.info("🔄 background loop started")

    # warm-up اولیه
    for sym in ASSETS:
        p = get_price(sym)
        if p:
            cloud.push_price(sym, p)
            log.info(f"🚀 warm-up {sym}: {p}")

async def on_shutdown(app: Application):
    cloud.save(force=True)
    log.info("👋 shutdown complete")

async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.error(f"Unhandled error: {ctx.error}", exc_info=ctx.error)

# ══════════════════════════════════════════════════════════════════════
# 🎯  MAIN
# ══════════════════════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        log.critical("❌ BOT_TOKEN not set!")
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

    # graceful shutdown
    def _sig(signum, frame):
        log.info(f"signal {signum}")
        cloud.save(force=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)
    atexit.register(lambda: cloud.save(force=True))

    log.info("🚀 ScalpBot Pro is running…")
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
