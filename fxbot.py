#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PropTrader Bot v1.0  (هسته: ScalpBot Pro v4.1)
─────────────────────────────────────────────
شبیه‌ساز کامل چالش پراپ‌فرم روی Telegram:
  • ثبت‌نام خودکار + اکانت دمو ۱۰,۰۰۰ دلاری
  • باز/بسته کردن پوزیشن (BUY/SELL) با لات واقعی + لوریج ۱:۱۰۰
  • SL / TP خودکار (هر ۲۰ ثانیه چک می‌شود)
  • فازبندی FTMO-style: فاز۱ (تارگت ۸٪) → فاز۲ (تارگت ۵٪) → فاندد
  • قوانین ریسک: حداکثر ضرر روزانه ۵٪ / حداکثر ضرر کل ۱۰٪ (شکست خودکار)
  • همون هسته قیمت‌گیری (۳ منبع + fallback) و چارت کندل‌استیک حرفه‌ای

fixes از نسخه قبل (بدون تغییر):
  - آخرین کندل live: wick و body هر دو cap می‌شن
  - Y-axis از داده‌های کندل‌های بسته + buffer منطقی محاسبه میشه
  - live_price override: highs/lows آخرین کندل بازسازی میشه
"""

import os, sys, asyncio, logging, signal, atexit, io, threading, time, statistics
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

from telegram import WebAppInfo  # این خط رو اضافه کن
from flask_cors import CORS

import requests, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import numpy as np
from flask import Flask, jsonify

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

# ══════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO, stream=sys.stdout,
)
log = logging.getLogger("PropTrader")

# ══════════════════════════════════════════════════════════════════════
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
PORT        = int(os.environ.get("PORT", 5000))
JSONBIN_KEY = os.environ.get("JSONBIN_API_KEY", "")
JSONBIN_BIN = os.environ.get("JSONBIN_BIN_ID", "")

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
def now_ir(): return datetime.now(IRAN_TZ)
def str_ir(fmt="%H:%M:%S"): return now_ir().strftime(fmt)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finance.yahoo.com/",
}

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
    yahoo_syms: list
    # ── فیلدهای موتور ترید پراپ ──
    contract_size: float = 100000.0     # واحد در هر ۱.۰ لات استاندارد
    quote_is_usd: bool = True           # آیا ارز quote دلار است؟ (محاسبه مستقیم P/L)
    notional_direct_usd: bool = False   # آیا notional لات مستقیماً دلاری است (بدون ضرب در قیمت)؟

ASSETS: dict[str, Asset] = {
    "XAUUSD": Asset(
        key="XAUUSD", name="Gold", emoji="🥇", symbol="XAU/USD",
        decimals=2, pip_size=0.1,
        yahoo_syms=["XAUUSD=X", "GC=F"],
        contract_size=100.0, quote_is_usd=True, notional_direct_usd=False,
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
        yahoo_syms=["EURUSD=X"],
        contract_size=100000.0, quote_is_usd=True, notional_direct_usd=False,
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
        yahoo_syms=["USDJPY=X"],
        contract_size=100000.0, quote_is_usd=False, notional_direct_usd=True,
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
        yahoo_syms=["GBPUSD=X"],
        contract_size=100000.0, quote_is_usd=True, notional_direct_usd=False,
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

TIMEFRAMES = {
    "1m":  ("1m",  "1d",  "1M  |  1m candles",  "۱ دقیقه",  "%H:%M"),
    "5m":  ("5m",  "5d",  "5M  |  5m candles",  "۵ دقیقه",  "%m/%d %H:%M"),
    "15m": ("15m", "10d", "15M |  15m candles", "۱۵ دقیقه", "%m/%d %H:%M"),
    "1h":  ("1h",  "30d", "1H  |  1h candles",  "۱ ساعت",   "%m/%d"),
    "4h":  ("1h",  "60d", "4H  |  1h candles",  "۴ ساعت",   "%m/%d"),
    "1d":  ("1d",  "1y",  "1D  |  1d candles",  "روزانه",   "%Y/%m/%d"),
}

MAX_BARS = 200

# لینک Vercel یا GitHub Pages خودت رو اینجا بذار
MINI_APP_URL = "https://2ccf8bab.proptrader-dashboard.pages.dev/"  # این رو عوض کن!

# ══════════════════════════════════════════════════════════════════════
# PRICE FETCHER
# ══════════════════════════════════════════════════════════════════════
_price_cache: dict[str, tuple[float, float]] = {}
CACHE_TTL = 12

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

def _all_prices(force: bool = False) -> dict:
    return {s: get_price(s, force=force) for s in ASSETS}

# ══════════════════════════════════════════════════════════════════════
# OHLC FETCHER
# ══════════════════════════════════════════════════════════════════════
_YAHOO_HOSTS = [
    "https://query1.finance.yahoo.com",
    "https://query2.finance.yahoo.com",
]

def _fetch_ohlc(yahoo_sym: str, interval: str, range_: str) -> Optional[tuple]:
    for host in _YAHOO_HOSTS:
        url = f"{host}/v8/finance/chart/{yahoo_sym}"
        try:
            r = requests.get(
                url, headers=_HEADERS,
                params={
                    "interval": interval,
                    "range": range_,
                    "includePrePost": "false",
                    "events": "div,splits",
                },
                timeout=15,
            )
            if r.status_code != 200:
                log.warning(f"Yahoo {host} {yahoo_sym} HTTP {r.status_code}")
                continue

            data = r.json()
            result_list = data.get("chart", {}).get("result")
            if not result_list:
                log.warning(f"Yahoo {host} {yahoo_sym}: no result")
                continue
            result = result_list[0]
            ts_list = result.get("timestamp", [])
            if not ts_list:
                log.warning(f"Yahoo {host} {yahoo_sym}: empty timestamp")
                continue

            q = result["indicators"]["quote"][0]
            times, opens, highs, lows, closes, vols = [], [], [], [], [], []
            for i, ts in enumerate(ts_list):
                c = q["close"][i]
                o = q["open"][i]
                h = q["high"][i]
                l = q["low"][i]
                if None in (c, o, h, l):
                    continue
                times.append(datetime.fromtimestamp(ts, tz=IRAN_TZ))
                opens.append(float(o))
                highs.append(float(h))
                lows.append(float(l))
                closes.append(float(c))
                v = (q.get("volume") or [None] * len(ts_list))[i]
                vols.append(float(v) if v else 0.0)

            if len(closes) < 5:
                log.warning(f"Yahoo {yahoo_sym}: only {len(closes)} valid bars")
                continue

            log.info(f"OK: {host}/{yahoo_sym} {interval}/{range_} → {len(closes)} bars")
            return times, opens, highs, lows, closes, vols

        except Exception as e:
            log.error(f"fetch_ohlc {host}/{yahoo_sym}: {str(e)[:80]}")

    return None


def fetch_ohlc_asset(asset: Asset, interval: str, range_: str) -> Optional[tuple]:
    for sym in asset.yahoo_syms:
        result = _fetch_ohlc(sym, interval, range_)
        if result is not None:
            return result
    return None

# ══════════════════════════════════════════════════════════════════════
# INDICATORS
# ══════════════════════════════════════════════════════════════════════
def _sma(data, period):
    out = [None] * len(data)
    for i in range(period - 1, len(data)):
        out[i] = sum(data[i - period + 1: i + 1]) / period
    return out

def _ema(data, period):
    out = [None] * len(data)
    k = 2 / (period + 1)
    start = next((i for i, v in enumerate(data) if v is not None), None)
    if start is None or start + period > len(data):
        return out
    vals = [v for v in data[start: start + period] if v is not None]
    if len(vals) < period:
        return out
    out[start + period - 1] = sum(vals) / period
    for i in range(start + period, len(data)):
        if data[i] is not None and out[i - 1] is not None:
            out[i] = data[i] * k + out[i - 1] * (1 - k)
    return out

def _bollinger(closes, period=20, mult=2.0):
    mid = _sma(closes, period)
    upper = [None] * len(closes)
    lower = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        if mid[i] is None:
            continue
        w = closes[i - period + 1: i + 1]
        sd = (sum((x - mid[i]) ** 2 for x in w) / period) ** 0.5
        upper[i] = mid[i] + mult * sd
        lower[i] = mid[i] - mult * sd
    return upper, mid, lower

def _rsi(closes, period=14):
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

def _macd(closes, fast=12, slow=26, signal=9):
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    ml = [(f - s) if (f and s) else None for f, s in zip(ef, es)]
    sl = _ema(ml, signal)
    hl = [(m - s) if (m is not None and s is not None) else None for m, s in zip(ml, sl)]
    return ml, sl, hl

# ══════════════════════════════════════════════════════════════════════
# LAST CANDLE NORMALIZER  ← هسته اصلی فیکس
# ══════════════════════════════════════════════════════════════════════
def _build_live_candle(opens, highs, lows, closes, times, vols, live_price, interval_str):
    """
    ساخت کندل live واقعی — رویکرد محافظه‌کارانه:

    Yahoo داده آخرین ۱-۲ کندل رو با تأخیر و غلط میده.
    راه‌حل:
      1. آخرین ۲ کندل Yahoo رو DROP می‌کنیم (هر دو ممکنه کثیف باشن)
      2. یه کندل doji ساده می‌سازیم:
             open = close = high = low = live_price
         → هیچ wick کاذبی ندارد
         → جایگاهش روی چارت دقیقاً قیمت فعلی رو نشون میده
      3. زمان: آخرین زمان معتبر + interval

    این ساده‌ترین و دقیق‌ترین نمایش ممکن از قیمت لحظه‌ای هست.
    """
    opens  = list(opens)
    highs  = list(highs)
    lows   = list(lows)
    closes = list(closes)
    times  = list(times)
    vols   = list(vols)

    if len(closes) < 4:
        return opens, highs, lows, closes, times, vols

    _iv_map = {
        "1m":  timedelta(minutes=1),
        "2m":  timedelta(minutes=2),
        "5m":  timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "30m": timedelta(minutes=30),
        "1h":  timedelta(hours=1),
        "1d":  timedelta(days=1),
    }
    iv_td = _iv_map.get(interval_str, timedelta(minutes=5))

    # آخرین زمان معتبر = سومین کندل از آخر (قبل از ۲ کندل مشکوک)
    last_valid_time = times[-3]
    new_time = last_valid_time + iv_td * 2   # دو تا interval جلوتر

    # DROP دو کندل آخر Yahoo
    for _ in range(2):
        opens.pop(); highs.pop(); lows.pop()
        closes.pop(); times.pop(); vols.pop()

    # کندل live: doji روی قیمت فعلی — بدون wick
    opens.append(live_price)
    highs.append(live_price)
    lows.append(live_price)
    closes.append(live_price)
    times.append(new_time)
    vols.append(0.0)

    return opens, highs, lows, closes, times, vols


def _cap_last_candle(opens, highs, lows, closes):
    """
    وقتی live_price نداریم، فقط wick کندل آخر رو cap می‌کنیم
    تا چارت کشیده نشه.
    """
    if len(closes) < 3:
        return opens, highs, lows, closes

    opens  = list(opens)
    highs  = list(highs)
    lows   = list(lows)
    closes = list(closes)

    closed_wicks = [h - l for h, l in zip(highs[:-1], lows[:-1])]
    median_wick  = statistics.median(closed_wicks) if closed_wicks else 1.0
    max_wick     = median_wick * 3.0

    idx      = len(closes) - 1
    o, c     = opens[idx], closes[idx]
    min_high = max(o, c)
    max_low  = min(o, c)

    upper_wick = highs[idx] - min_high
    lower_wick = max_low - lows[idx]

    highs[idx] = min_high + min(upper_wick, max_wick * 0.5)
    lows[idx]  = max_low  - min(lower_wick, max_wick * 0.5)

    return opens, highs, lows, closes


# ══════════════════════════════════════════════════════════════════════
# YLIM CALCULATOR  ← فیکس فشردگی چارت
# ══════════════════════════════════════════════════════════════════════
def _calc_ylim(highs, lows, closes, n_closed=None):
    """
    Y-axis رو بر اساس کندل‌های بسته حساب می‌کنه.
    آخرین کندل (که normalize شده) هم درنظر می‌گیره ولی وزن کمتری داره.
    """
    if n_closed is None:
        n_closed = max(len(closes) - 1, 1)

    closed_h = highs[:n_closed]
    closed_l = lows[:n_closed]

    if not closed_h:
        closed_h = highs
        closed_l = lows

    hi = max(closed_h)
    lo = min(closed_l)
    pr = hi - lo

    if pr == 0:
        pr = closes[-1] * 0.001

    pad = pr * 0.08
    return lo - pad, hi + pad * 2.5   # بیشتر فضا بالا برای label


# ══════════════════════════════════════════════════════════════════════
# CHART COLORS
# ══════════════════════════════════════════════════════════════════════
BG      = "#131722"
UP_C    = "#26a69a"
DN_C    = "#ef5350"
GRID_C  = "#1e222d"
TEXT_C  = "#d1d4dc"
DIM_C   = "#787b86"
BB_C    = "#1565c0"
EMA21_C = "#f9a825"
EMA50_C = "#ab47bc"
MACD_C  = "#26a69a"
SIG_C   = "#ef5350"


def _draw_candles(ax, times, opens, highs, lows, closes, bar_w_num):
    """رسم کندل‌استیک — داده‌ها قبلاً normalize شدن."""
    for i, (t, o, h, l, c) in enumerate(zip(times, opens, highs, lows, closes)):
        is_up  = c >= o
        color  = UP_C if is_up else DN_C
        body_b = min(o, c)
        body_h = abs(c - o)

        if body_h == 0:
            median_b = statistics.median([abs(cc - oo) for cc, oo in zip(closes[:-1], opens[:-1])]) if len(closes) > 1 else 1.0
            body_h = max(body_h, median_b * 0.05)

        ax.plot([t, t], [l, h], color=color, linewidth=0.7, zorder=2)
        rect = mpatches.FancyBboxPatch(
            (mdates.date2num(t) - bar_w_num / 2, body_b),
            bar_w_num, body_h,
            boxstyle="square,pad=0",
            linewidth=0,
            facecolor=color,
            zorder=3,
        )
        ax.add_patch(rect)


def create_chart(
    symbol: str,
    tf_key: str = "1m",
    live_price: Optional[float] = None,
) -> Optional[io.BytesIO]:

    if symbol not in ASSETS or tf_key not in TIMEFRAMES:
        return None

    asset = ASSETS[symbol]
    interval, range_, label_en, label_fa, x_fmt = TIMEFRAMES[tf_key]

    ohlc = fetch_ohlc_asset(asset, interval, range_)
    if ohlc is None:
        log.error(f"create_chart: no data for {symbol} {tf_key}")
        return None

    times, opens, highs, lows, closes, vols = ohlc

    slice_n = MAX_BARS + 2 if live_price is not None else MAX_BARS
    if len(times) > slice_n:
        times  = times[-slice_n:]
        opens  = opens[-slice_n:]
        highs  = highs[-slice_n:]
        lows   = lows[-slice_n:]
        closes = closes[-slice_n:]
        vols   = vols[-slice_n:]

    if live_price is not None:
        opens, highs, lows, closes, times, vols = _build_live_candle(
            opens, highs, lows, closes, times, vols,
            live_price=live_price,
            interval_str=interval,
        )
        if len(times) > MAX_BARS:
            times  = times[-MAX_BARS:]
            opens  = opens[-MAX_BARS:]
            highs  = highs[-MAX_BARS:]
            lows   = lows[-MAX_BARS:]
            closes = closes[-MAX_BARS:]
            vols   = vols[-MAX_BARS:]
    else:
        opens, highs, lows, closes = _cap_last_candle(opens, highs, lows, closes)

    times_num = [mdates.date2num(t) for t in times]
    if len(times_num) > 1:
        bar_w = (times_num[-1] - times_num[0]) / (len(times_num) - 1) * 0.7
    else:
        bar_w = 0.0007

    dec = asset.decimals

    ema21               = _ema(closes, 21)
    ema50               = _ema(closes, 50)
    bb_up, bb_mid, bb_low = _bollinger(closes, 20)
    rsi_vals            = _rsi(closes, 14)
    macd_l, sig_l, hist_l = _macd(closes)

    cur   = closes[-1]
    start = closes[0]
    chg   = cur - start
    chgp  = (chg / start * 100) if start else 0
    is_up = chg >= 0
    main_c = UP_C if is_up else DN_C

    n_closed = max(len(closes) - 1, 1)
    hi = max(highs[:n_closed])
    lo = min(lows[:n_closed])
    hi = max(hi, highs[-1])
    lo = min(lo, lows[-1])

    hi_i = highs.index(hi)
    lo_i = lows.index(lo)

    last_rsi  = next((v for v in reversed(rsi_vals) if v is not None), None)
    last_macd = next((v for v in reversed(macd_l)   if v is not None), None)

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 8,
        "axes.unicode_minus": False,
    })
    fig = plt.figure(figsize=(14, 10), facecolor=BG)
    gs = fig.add_gridspec(
        4, 1,
        height_ratios=[5, 1, 1.5, 1.5],
        hspace=0.0,
        left=0.01, right=0.87,
        top=0.88, bottom=0.07,
    )
    ax_c = fig.add_subplot(gs[0])
    ax_v = fig.add_subplot(gs[1], sharex=ax_c)
    ax_r = fig.add_subplot(gs[2], sharex=ax_c)
    ax_m = fig.add_subplot(gs[3], sharex=ax_c)

    for ax in (ax_c, ax_v, ax_r, ax_m):
        ax.set_facecolor(BG)
        ax.tick_params(colors=DIM_C, labelsize=7, length=2)
        ax.yaxis.set_label_position("right")
        ax.yaxis.tick_right()
        for sp in ax.spines.values():
            sp.set_color(GRID_C)
        ax.grid(True, color=GRID_C, linewidth=0.4, zorder=0)

    _draw_candles(ax_c, times, opens, highs, lows, closes, bar_w)

    def _pv(ax, ts, data, **kw):
        pts = [(t, v) for t, v in zip(ts, data) if v is not None]
        if pts:
            tt, vv = zip(*pts)
            ax.plot(tt, vv, **kw)

    _pv(ax_c, times, bb_up,  color=BB_C, lw=0.9, alpha=0.7, ls="--", label="BB(20)")
    _pv(ax_c, times, bb_mid, color=BB_C, lw=0.7, alpha=0.5, ls=":")
    _pv(ax_c, times, bb_low, color=BB_C, lw=0.9, alpha=0.7, ls="--")

    bb_pairs = [(t, u, l) for t, u, l in zip(times, bb_up, bb_low)
                if u is not None and l is not None]
    if bb_pairs:
        bbt, bbu, bbl = zip(*bb_pairs)
        ax_c.fill_between(bbt, bbu, bbl, color=BB_C, alpha=0.04, zorder=1)

    _pv(ax_c, times, ema21, color=EMA21_C, lw=1.1, alpha=0.9, label="EMA21", zorder=4)
    _pv(ax_c, times, ema50, color=EMA50_C, lw=1.1, alpha=0.9, label="EMA50", zorder=4)

    ax_c.axhline(cur, color=main_c, ls="-", alpha=0.25, lw=0.8, zorder=1)
    ax_c.text(1.002, cur, f"{cur:.{dec}f}",
              transform=ax_c.get_yaxis_transform(),
              fontsize=8.5, fontweight="bold", color="white",
              va="center", ha="left",
              bbox=dict(boxstyle="round,pad=0.3", fc=main_c, ec="none"))

    ax_c.annotate(f"H {hi:.{dec}f}",
                  xy=(times[hi_i], hi), xytext=(0, 10),
                  textcoords="offset points",
                  fontsize=7.5, color=EMA21_C, fontweight="bold", ha="center",
                  arrowprops=dict(arrowstyle="-", color=EMA21_C, lw=0.5))
    ax_c.annotate(f"L {lo:.{dec}f}",
                  xy=(times[lo_i], lo), xytext=(0, -12),
                  textcoords="offset points",
                  fontsize=7.5, color="#29b6f6", fontweight="bold", ha="center",
                  arrowprops=dict(arrowstyle="-", color="#29b6f6", lw=0.5))

    y_lo, y_hi = _calc_ylim(highs, lows, closes, n_closed=n_closed)
    ax_c.set_ylim(y_lo, y_hi)

    ax_c.legend(loc="upper left", fontsize=7,
                facecolor=BG, edgecolor=GRID_C, labelcolor=TEXT_C, framealpha=0.8)

    bar_td = timedelta(days=bar_w * 0.8)
    vcols = [UP_C if c >= o else DN_C for c, o in zip(closes, opens)]
    ax_v.bar(times, vols, color=vcols, alpha=0.5, width=bar_td)
    ax_v.set_yticks([])
    ax_v.text(1.002, 0.5, "VOL", transform=ax_v.transAxes,
              fontsize=7, color=DIM_C, va="center", ha="left")

    rsi_pts = [(t, v) for t, v in zip(times, rsi_vals) if v is not None]
    if rsi_pts:
        rt, rv = zip(*rsi_pts)
        ax_r.plot(rt, rv, color="#ce93d8", lw=1.1, zorder=3)
        ax_r.axhline(70, color=DN_C, ls="--", alpha=0.5, lw=0.7)
        ax_r.axhline(50, color=DIM_C, ls=":",  alpha=0.3, lw=0.6)
        ax_r.axhline(30, color=UP_C,  ls="--", alpha=0.5, lw=0.7)
        ax_r.fill_between(rt, rv, 70, where=[v >= 70 for v in rv],
                          color=DN_C, alpha=0.1, zorder=1)
        ax_r.fill_between(rt, rv, 30, where=[v <= 30 for v in rv],
                          color=UP_C, alpha=0.1, zorder=1)
        ax_r.set_ylim(10, 90)
        ax_r.set_yticks([30, 50, 70])
        if last_rsi is not None:
            rc = UP_C if last_rsi >= 50 else DN_C
            ax_r.text(1.002, last_rsi, f"RSI {last_rsi:.0f}",
                      transform=ax_r.get_yaxis_transform(),
                      fontsize=7.5, fontweight="bold", color="white",
                      va="center", ha="left",
                      bbox=dict(boxstyle="round,pad=0.25", fc=rc, ec="none"))

    mpts = [(t, v) for t, v in zip(times, macd_l) if v is not None]
    spts = [(t, v) for t, v in zip(times, sig_l)  if v is not None]
    hpts = [(t, v) for t, v in zip(times, hist_l) if v is not None]
    if mpts:
        mt, mv = zip(*mpts); ax_m.plot(mt, mv, color=MACD_C, lw=1.0, label="MACD", zorder=3)
    if spts:
        st, sv = zip(*spts); ax_m.plot(st, sv, color=SIG_C,  lw=0.9, label="Sig",  zorder=3)
    if hpts:
        ht, hv = zip(*hpts)
        hc = [UP_C if v >= 0 else DN_C for v in hv]
        ax_m.bar(ht, hv, color=hc, alpha=0.5, width=bar_td, zorder=2)
    ax_m.axhline(0, color=DIM_C, lw=0.6, alpha=0.5)
    ax_m.legend(loc="upper left", fontsize=6.5,
                facecolor=BG, edgecolor=GRID_C, labelcolor=TEXT_C, framealpha=0.8)
    if last_macd is not None:
        mc = UP_C if last_macd >= 0 else DN_C
        ax_m.text(1.002, last_macd, f"{last_macd:.{max(1,dec-1)}f}",
                  transform=ax_m.get_yaxis_transform(),
                  fontsize=7, fontweight="bold", color="white",
                  va="center", ha="left",
                  bbox=dict(boxstyle="round,pad=0.2", fc=mc, ec="none"))
    ax_m.text(1.002, 0.5, "MACD", transform=ax_m.transAxes,
              fontsize=7, color=DIM_C, va="center", ha="left")

    ax_m.xaxis.set_major_formatter(mdates.DateFormatter(x_fmt, tz=IRAN_TZ))
    ax_m.xaxis.set_major_locator(mdates.AutoDateLocator(tz=IRAN_TZ))
    plt.setp(ax_m.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=7)
    for a in (ax_c, ax_v, ax_r):
        plt.setp(a.xaxis.get_majorticklabels(), visible=False)

    sign  = "+" if is_up else ""
    arrow = "▲" if is_up else "▼"
    rsi_s  = f"RSI: {last_rsi:.0f}" if last_rsi else ""
    macd_s = f"MACD: {last_macd:.{max(1,dec-1)}f}" if last_macd else ""
    fig.text(0.44, 0.935,
             f"{asset.symbol}   {label_en}   ({len(closes)} candles)   |   Iran Time (UTC+3:30)",
             ha="center", fontsize=10, fontweight="bold", color=TEXT_C)
    fig.text(0.44, 0.908,
             f"{arrow} {cur:.{dec}f}   {sign}{chg:.{dec}f}  ({sign}{chgp:.2f}%)   "
             f"H: {hi:.{dec}f}   L: {lo:.{dec}f}",
             ha="center", fontsize=10, color=main_c)
    fig.text(0.44, 0.885,
             f"EMA21 / EMA50 / BB(20,2)   {rsi_s}   {macd_s}",
             ha="center", fontsize=8, color=DIM_C)
    fig.text(0.87, 0.01, "@PropTraderBot", ha="right", fontsize=6.5, color=GRID_C)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=BG)
    buf.seek(0)
    plt.close(fig)
    return buf

# ══════════════════════════════════════════════════════════════════════
# PROP FIRM CONFIG
# ══════════════════════════════════════════════════════════════════════
PROP_INITIAL_BALANCE = 10_000.0
PROP_LEVERAGE         = 100          # 1:100
PROP_MIN_LOT          = 0.01
PROP_MAX_LOT          = 20.0

PROP_PHASES = {
    1: {"name": "فاز اول",  "tag": "PHASE 1", "target_pct": 0.08,
        "max_daily_loss_pct": 0.05, "max_total_loss_pct": 0.10, "min_days": 4},
    2: {"name": "فاز دوم",  "tag": "PHASE 2", "target_pct": 0.05,
        "max_daily_loss_pct": 0.05, "max_total_loss_pct": 0.10, "min_days": 4},
    3: {"name": "فاندد",    "tag": "FUNDED",  "target_pct": None,
        "max_daily_loss_pct": 0.05, "max_total_loss_pct": 0.10, "min_days": 0},
}

# ══════════════════════════════════════════════════════════════════════
# CLOUD STORAGE
# ══════════════════════════════════════════════════════════════════════
class CloudStorage:
    def __init__(self):
        self._key  = JSONBIN_KEY
        self._bin  = JSONBIN_BIN
        self._url  = "https://api.jsonbin.io/v3/b"
        self._hdrs = {"X-Master-Key": self._key, "Content-Type": "application/json"}
        self._lock = threading.Lock()
        self._dirty = False
        self._data = {
            "users": {}, "total_requests": 0,
            "price_history": {s: [] for s in ASSETS},
            "alerts": {}, "prop_accounts": {}, "last_updated": None,
        }

    def load(self):
        if not (self._key and self._bin):
            log.info("CloudStorage: local only"); return
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
                self._data.setdefault("prop_accounts", {})
            log.info("CloudStorage loaded")
        except Exception as e:
            log.error(f"CloudStorage load: {e}")

    def save(self, force=False):
        if not self._key: return
        if not force and not self._dirty: return
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
                if bid: self._bin = bid
            r.raise_for_status()
            self._dirty = False
        except Exception as e:
            log.error(f"CloudStorage save: {e}")

    def ensure_user(self, uid, username=""):
        key = str(uid)
        with self._lock:
            if key not in self._data["users"]:
                self._data["users"][key] = {
                    "username": username, "joined": now_ir().isoformat(), "requests": 0
                }
                self._dirty = True

    def inc(self, uid):
        key = str(uid)
        with self._lock:
            if key in self._data["users"]:
                self._data["users"][key]["requests"] += 1
            self._data["total_requests"] = self._data.get("total_requests", 0) + 1
            self._dirty = True

    def stats(self):
        return len(self._data.get("users", {})), self._data.get("total_requests", 0)

    def push_price(self, sym, price):
        with self._lock:
            h = self._data.setdefault("price_history", {}).setdefault(sym, [])
            h.append({"p": price, "t": now_ir().isoformat()})
            if len(h) > 200:
                self._data["price_history"][sym] = h[-200:]
            self._dirty = True

    def get_history(self, sym):
        return list(self._data.get("price_history", {}).get(sym, []))

    def add_alert(self, uid, sym, direction, target):
        key = str(uid)
        with self._lock:
            self._data.setdefault("alerts", {}).setdefault(key, {}).setdefault(sym, []).append({
                "dir": direction, "target": target,
                "at": now_ir().isoformat(), "done": False,
            })
            self._dirty = True

    def get_alerts(self, uid):
        return dict(self._data.get("alerts", {}).get(str(uid), {}))

    def mark_done(self, uid, sym, idx):
        try:
            with self._lock:
                self._data["alerts"][str(uid)][sym][idx]["done"] = True
                self._dirty = True
        except Exception: pass

    def clear_done(self, uid):
        key = str(uid)
        with self._lock:
            for sym in list(self._data.get("alerts", {}).get(key, {}).keys()):
                self._data["alerts"][key][sym] = [
                    a for a in self._data["alerts"][key][sym] if not a.get("done")
                ]
            self._dirty = True

    # ── PROP ACCOUNTS ──
    def _new_prop_account(self):
        today = now_ir().date().isoformat()
        return {
            "phase": 1, "status": "active",
            "balance": PROP_INITIAL_BALANCE, "initial_balance": PROP_INITIAL_BALANCE,
            "equity": PROP_INITIAL_BALANCE, "daily_start_equity": PROP_INITIAL_BALANCE,
            "daily_date": today, "trading_days": [],
            "positions": {}, "history": [], "next_id": 1,
            "fail_reason": None, "phase_started": now_ir().isoformat(),
        }

    def ensure_prop(self, uid):
        key = str(uid)
        with self._lock:
            accs = self._data.setdefault("prop_accounts", {})
            if key not in accs:
                accs[key] = self._new_prop_account()
                self._dirty = True
            return accs[key]

    def get_prop(self, uid):
        with self._lock:
            return self._data.get("prop_accounts", {}).get(str(uid))

    def save_prop(self, uid, acc):
        with self._lock:
            self._data.setdefault("prop_accounts", {})[str(uid)] = acc
            self._dirty = True

    def reset_prop(self, uid):
        with self._lock:
            self._data.setdefault("prop_accounts", {})[str(uid)] = self._new_prop_account()
            self._dirty = True

    def all_prop_uids(self):
        with self._lock:
            return list(self._data.get("prop_accounts", {}).keys())

cloud = CloudStorage()

# ══════════════════════════════════════════════════════════════════════
# PROP ENGINE — توابع خالص (مارجین، P/L، فازبندی، ریسک)
# ══════════════════════════════════════════════════════════════════════
def _notional_usd(asset: Asset, lot: float, price: float) -> float:
    if asset.notional_direct_usd:
        return asset.contract_size * lot
    return asset.contract_size * lot * price

def _margin_required(asset: Asset, lot: float, price: float) -> float:
    return _notional_usd(asset, lot, price) / PROP_LEVERAGE

def _position_pnl(asset: Asset, pos: dict, price: float) -> float:
    diff = (price - pos["open"]) if pos["dir"] == "buy" else (pos["open"] - price)
    raw = diff * asset.contract_size * pos["lot"]
    if asset.quote_is_usd:
        return raw
    return raw / price if price else 0.0

def _used_margin(acc: dict, prices: dict) -> float:
    total = 0.0
    for pos in acc["positions"].values():
        a = ASSETS.get(pos["sym"])
        if a:
            total += _margin_required(a, pos["lot"], prices.get(pos["sym"]) or pos["open"])
    return total

def _floating_pnl(acc: dict, prices: dict) -> float:
    total = 0.0
    for pos in acc["positions"].values():
        a = ASSETS.get(pos["sym"])
        p = prices.get(pos["sym"])
        if a and p:
            total += _position_pnl(a, pos, p)
    return total

def _phase_rule(acc: dict) -> dict:
    return PROP_PHASES.get(acc["phase"], PROP_PHASES[3])

def free_margin(acc: dict, prices: dict) -> float:
    equity = acc["balance"] + _floating_pnl(acc, prices)
    return equity - _used_margin(acc, prices)

def _check_daily_reset(acc: dict, prices: dict):
    today = now_ir().date().isoformat()
    if acc.get("daily_date") != today:
        acc["daily_date"] = today
        acc["daily_start_equity"] = acc["balance"] + _floating_pnl(acc, prices)

def _close_position(acc: dict, pid: str, price: float, reason: str):
    pos = acc["positions"].pop(pid, None)
    if not pos: return
    a = ASSETS[pos["sym"]]
    pnl = _position_pnl(a, pos, price)
    acc["balance"] += pnl
    day = now_ir().date().isoformat()
    if day not in acc["trading_days"]:
        acc["trading_days"].append(day)
    acc["history"].append({
        "sym": pos["sym"], "dir": pos["dir"], "lot": pos["lot"],
        "open": pos["open"], "close": price, "pnl": pnl,
        "reason": reason, "open_time": pos["open_time"],
        "close_time": now_ir().isoformat(),
    })
    if len(acc["history"]) > 100:
        acc["history"] = acc["history"][-100:]

def _fail_account(acc: dict, reason: str, prices: dict):
    for pid, pos in list(acc["positions"].items()):
        a = ASSETS.get(pos["sym"])
        p = prices.get(pos["sym"]) or pos["open"]
        if a:
            _close_position(acc, pid, p, "force_close")
    acc["status"] = "failed"
    acc["fail_reason"] = reason
    acc["equity"] = acc["balance"]

def _advance_phase(acc: dict):
    nxt = acc["phase"] + 1
    if nxt >= 3:
        acc["phase"] = 3
        acc["status"] = "funded"
    else:
        acc["phase"] = nxt
        acc["status"] = "active"
    acc["balance"] = PROP_INITIAL_BALANCE
    acc["initial_balance"] = PROP_INITIAL_BALANCE
    acc["equity"] = PROP_INITIAL_BALANCE
    acc["daily_start_equity"] = PROP_INITIAL_BALANCE
    acc["daily_date"] = now_ir().date().isoformat()
    acc["trading_days"] = []
    acc["positions"] = {}
    acc["phase_started"] = now_ir().isoformat()

def recompute(acc: dict, prices: dict) -> list:
    """equity/SL-TP/ریسک رو آپدیت می‌کنه و لیست ایونت‌ها (sl/tp/fail/pass) رو برمی‌گردونه."""
    events = []
    if acc["status"] not in ("active",):
        return events

    _check_daily_reset(acc, prices)

    for pid, pos in list(acc["positions"].items()):
        a = ASSETS.get(pos["sym"])
        p = prices.get(pos["sym"])
        if not a or not p:
            continue
        hit = None
        if pos["dir"] == "buy":
            if pos.get("sl") and p <= pos["sl"]: hit = ("sl", pos["sl"])
            elif pos.get("tp") and p >= pos["tp"]: hit = ("tp", pos["tp"])
        else:
            if pos.get("sl") and p >= pos["sl"]: hit = ("sl", pos["sl"])
            elif pos.get("tp") and p <= pos["tp"]: hit = ("tp", pos["tp"])
        if hit:
            reason, close_p = hit
            pnl = _position_pnl(a, pos, close_p)
            _close_position(acc, pid, close_p, reason)
            events.append({"type": reason, "sym": pos["sym"], "pnl": pnl})

    floating = _floating_pnl(acc, prices)
    acc["equity"] = acc["balance"] + floating

    rule = _phase_rule(acc)
    daily_floor = acc["daily_start_equity"] * (1 - rule["max_daily_loss_pct"])
    total_floor = acc["initial_balance"] * (1 - rule["max_total_loss_pct"])

    if acc["equity"] <= daily_floor:
        _fail_account(acc, "daily_loss", prices)
        events.append({"type": "fail", "reason": "daily_loss"})
    elif acc["equity"] <= total_floor:
        _fail_account(acc, "total_loss", prices)
        events.append({"type": "fail", "reason": "total_loss"})
    elif rule["target_pct"] is not None:
        target_val = acc["initial_balance"] * (1 + rule["target_pct"])
        days_ok = len(acc.get("trading_days", [])) >= rule["min_days"]
        if acc["balance"] >= target_val and days_ok and not acc["positions"]:
            old_phase = acc["phase"]
            _advance_phase(acc)
            events.append({"type": "pass", "from_phase": old_phase, "to_phase": acc["phase"]})

    return events

def open_position(acc: dict, sym: str, direction: str, lot: float, price: float,
                   sl: Optional[float] = None, tp: Optional[float] = None,
                   prices: Optional[dict] = None):
    a = ASSETS[sym]
    prices = prices or {}
    margin_need = _margin_required(a, lot, price)
    used = _used_margin(acc, prices)
    equity = acc["balance"] + _floating_pnl(acc, prices)
    if margin_need > (equity - used):
        return None, "insufficient_margin"
    pid = str(acc.get("next_id", 1))
    acc["next_id"] = int(pid) + 1
    acc["positions"][pid] = {
        "sym": sym, "dir": direction, "lot": lot,
        "open": price, "sl": sl, "tp": tp,
        "open_time": now_ir().isoformat(),
    }
    return pid, None

def close_position_manual(acc: dict, pid: str, price: float) -> Optional[dict]:
    if pid not in acc["positions"]:
        return None
    pos = dict(acc["positions"][pid])
    _close_position(acc, pid, price, "manual")
    return pos

def edit_position(acc: dict, pid: str, sl=None, tp=None, clear_sl=False, clear_tp=False) -> bool:
    pos = acc["positions"].get(pid)
    if not pos: return False
    if clear_sl: pos["sl"] = None
    elif sl is not None: pos["sl"] = sl
    if clear_tp: pos["tp"] = None
    elif tp is not None: pos["tp"] = tp
    return True

# ══════════════════════════════════════════════════════════════════════
# FLASK
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)
CORS(flask_app)  # اجازه دسترسی مینی‌اپ به API
@flask_app.route("/")
def root():
    u, r = cloud.stats()
    return f"PropTrader Bot v1.0 | users={u} | reqs={r} | {str_ir()}"

@flask_app.route("/health")
def health():
    return jsonify(status="ok", time=str_ir(), version="v1.0-prop")

@flask_app.route("/api/prop/<int:uid>")
def api_prop_account(uid):
    """API برای دریافت اطلاعات اکانت پراپ کاربر از طریق مینی‌اپ"""
    try:
        acc = cloud.get_prop(uid)
        if not acc:
            return jsonify({"error": "Account not found"}), 404
        
        prices = _all_prices()
        floating = _floating_pnl(acc, prices)
        equity = acc["balance"] + floating
        
        rule = _phase_rule(acc)
        daily_floor = acc["daily_start_equity"] * (1 - rule["max_daily_loss_pct"])
        total_floor = acc["initial_balance"] * (1 - rule["max_total_loss_pct"])
        
        positions_data = []
        for pid, pos in acc["positions"].items():
            a = ASSETS[pos["sym"]]
            p = prices.get(pos["sym"]) or pos["open"]
            pnl = _position_pnl(a, pos, p)
            positions_data.append({
                "pid": pid,
                "sym": pos["sym"],
                "dir": pos["dir"],
                "lot": pos["lot"],
                "open": pos["open"],
                "now": p,
                "sl": pos.get("sl"),
                "tp": pos.get("tp"),
                "pnl": pnl
            })
        
        # محاسبه پیشرفت تارگت
        target_total = acc["initial_balance"] * (rule["target_pct"] or 0)
        target_progress = max(0, acc["balance"] - acc["initial_balance"])
        
        return jsonify({
            "phase": acc["phase"],
            "status": acc["status"],
            "balance": acc["balance"],
            "equity": equity,
            "floating": floating,
            "daily_room": equity - daily_floor,
            "total_room": equity - total_floor,
            "positions": positions_data,
            "trading_days": len(acc.get("trading_days", [])),
            "min_days": rule.get("min_days", 0),
            "target_progress": target_progress,
            "target_total": target_total
        })
    except Exception as e:
        log.error(f"API error: {e}")
        return jsonify({"error": str(e)}), 500

def _run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════
# KEYBOARDS — عمومی
# ══════════════════════════════════════════════════════════════════════
def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 ورود به داشبورد حرفه‌ای", web_app=WebAppInfo(url=MINI_APP_URL))],
        [InlineKeyboardButton("💰 همه قیمت‌ها", callback_data="all")],
        [InlineKeyboardButton("📈 نمودار", callback_data="chart_menu")],
        [InlineKeyboardButton("💼 ترید پراپ 10K", callback_data="pt:main")],
        [InlineKeyboardButton("❓ راهنما", callback_data="help")],
    ])

def kb_asset(sym):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 قیمت لحظه‌ای 🔄", callback_data=f"px:{sym}")],
        [InlineKeyboardButton("📈 نمودار",            callback_data=f"chart:{sym}")],
        [InlineKeyboardButton("⬆️ هشدار صعودی",      callback_data=f"al_up:{sym}"),
         InlineKeyboardButton("⬇️ هشدار نزولی",      callback_data=f"al_dn:{sym}")],
        [InlineKeyboardButton("🔙 منوی اصلی",        callback_data="main")],
    ])

def kb_tf(sym):
    labels = {
        "1m":  "⚡ 1M  (scalp)",
        "5m":  "🔥 5M",
        "15m": "⏱ 15M",
        "1h":  "📅 1H",
        "4h":  "📆 4H",
        "1d":  "🗓 Daily",
    }
    rows, row = [], []
    for key, label in labels.items():
        row.append(InlineKeyboardButton(label, callback_data=f"tf:{sym}:{key}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"a:{sym}")])
    return InlineKeyboardMarkup(rows)

def kb_chart_menu():
    rows = [[InlineKeyboardButton(f"{ASSETS[s].emoji} {ASSETS[s].symbol}",
                                  callback_data=f"chart:{s}")] for s in ASSETS]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="main")])
    return InlineKeyboardMarkup(rows)

def kb_after_chart(sym):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تایم دیگر",   callback_data=f"chart:{sym}"),
         InlineKeyboardButton("💰 قیمت",         callback_data=f"px:{sym}")],
        [InlineKeyboardButton("⬆️ هشدار صعودی", callback_data=f"al_up:{sym}"),
         InlineKeyboardButton("⬇️ هشدار نزولی", callback_data=f"al_dn:{sym}")],
        [InlineKeyboardButton("🏠 منوی اصلی",   callback_data="main")],
    ])

def kb_alerts():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 پاک کردن تریگرشده‌ها", callback_data="alerts_clean")],
        [InlineKeyboardButton("🔙 منوی اصلی", callback_data="main")],
    ])

# ══════════════════════════════════════════════════════════════════════
# KEYBOARDS — پراپ
# ══════════════════════════════════════════════════════════════════════
def kb_prop_main(acc):
    rows = [[InlineKeyboardButton("🟢 پوزیشن جدید", callback_data="pt:new")]]
    if acc["positions"]:
        rows.append([InlineKeyboardButton(f"📂 پوزیشن‌های باز ({len(acc['positions'])})", callback_data="pt:positions")])
    rows.append([InlineKeyboardButton("📜 تاریخچه", callback_data="pt:history")])
    if acc["status"] == "failed":
        rows.append([InlineKeyboardButton("🔄 شروع چالش جدید", callback_data="pt:reset")])
    rows.append([InlineKeyboardButton("🔙 منوی اصلی", callback_data="main")])
    return InlineKeyboardMarkup(rows)

def kb_prop_assets():
    rows = [[InlineKeyboardButton(f"{ASSETS[s].emoji} {ASSETS[s].symbol}", callback_data=f"pt:sym:{s}")] for s in ASSETS]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="pt:main")])
    return InlineKeyboardMarkup(rows)

def kb_prop_dir(sym):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 BUY", callback_data=f"pt:dir:{sym}:buy"),
         InlineKeyboardButton("🔴 SELL", callback_data=f"pt:dir:{sym}:sell")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="pt:new")],
    ])

def kb_skip(stage):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏭ رد شدن", callback_data=f"pt:skip:{stage}")]])

def kb_positions(acc):
    rows = []
    for pid in acc["positions"]:
        rows.append([
            InlineKeyboardButton(f"❌ بستن #{pid}", callback_data=f"pt:close:{pid}"),
            InlineKeyboardButton(f"✏️ SL/TP #{pid}", callback_data=f"pt:edit:{pid}"),
        ])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="pt:main")])
    return InlineKeyboardMarkup(rows)

def kb_edit_pos(pid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تغییر SL", callback_data=f"pt:setsl:{pid}"),
         InlineKeyboardButton("✏️ تغییر TP", callback_data=f"pt:settp:{pid}")],
        [InlineKeyboardButton("🗑 حذف SL", callback_data=f"pt:clrsl:{pid}"),
         InlineKeyboardButton("🗑 حذف TP", callback_data=f"pt:clrtp:{pid}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="pt:positions")],
    ])

# ══════════════════════════════════════════════════════════════════════
# MESSAGE BUILDERS — عمومی
# ══════════════════════════════════════════════════════════════════════
def _price_line(sym, price):
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

def build_all_prices():
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

def build_alerts_text(uid):
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
        if not a: continue
        for al in al_list:
            st = "✅" if al.get("done") else "⏳"
            dr = "⬆️" if al["dir"] == "up" else "⬇️"
            lines.append(f"{st} {a.emoji} {a.symbol}  {dr}  <code>{al['target']:.{a.decimals}f}</code>")
    lines.append("\n<i>بررسی هر ۵ دقیقه</i>")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════
# MESSAGE BUILDERS — پراپ
# ══════════════════════════════════════════════════════════════════════
def build_dashboard_text(acc, prices):
    rule = _phase_rule(acc)
    floating = _floating_pnl(acc, prices)
    equity = acc["balance"] + floating
    daily_floor = acc["daily_start_equity"] * (1 - rule["max_daily_loss_pct"])
    total_floor = acc["initial_balance"] * (1 - rule["max_total_loss_pct"])
    daily_room = equity - daily_floor
    total_room = equity - total_floor
    status_fa = {"active": "🟢 فعال", "failed": "🔴 شکست‌خورده", "funded": "🏆 فاندد"}.get(acc["status"], acc["status"])

    lines = [
        f"💼 <b>پنل ترید پراپ — {rule['tag']}</b>\n",
        f"وضعیت: {status_fa}\n",
        f"💰 بالانس: <code>${acc['balance']:,.2f}</code>",
        f"📊 اکوییتی: <code>${equity:,.2f}</code>",
        f"📈 شناور: <code>{'+' if floating >= 0 else ''}${floating:,.2f}</code>\n",
        f"🛑 سقف ضرر روزانه: <code>${daily_floor:,.2f}</code>  (فاصله: ${daily_room:,.2f})",
        f"🛑 سقف ضرر کل: <code>${total_floor:,.2f}</code>  (فاصله: ${total_room:,.2f})",
    ]
    if rule["target_pct"]:
        target_val = acc["initial_balance"] * (1 + rule["target_pct"])
        need = target_val - acc["initial_balance"]
        progress = max(0.0, acc["balance"] - acc["initial_balance"])
        pct = min(100, progress / need * 100) if need else 100
        lines.append(f"\n🎯 تارگت: <code>${target_val:,.2f}</code>  ({pct:.0f}٪ پیشرفت)")
        lines.append(f"📅 روزهای معاملاتی: {len(acc.get('trading_days', []))}/{rule['min_days']}")
    if acc["status"] == "failed":
        reason_fa = {"daily_loss": "عبور از سقف ضرر روزانه", "total_loss": "عبور از سقف ضرر کل"}.get(acc.get("fail_reason"), "نامعلوم")
        lines.append(f"\n❌ دلیل شکست: {reason_fa}")
    lines.append(f"\n⏱ {str_ir()}")
    return "\n".join(lines)

def build_positions_text(acc, prices):
    if not acc["positions"]:
        return "📭 پوزیشن باز ندارید."
    lines = ["📂 <b>پوزیشن‌های باز:</b>\n"]
    for pid, pos in acc["positions"].items():
        a = ASSETS[pos["sym"]]
        p = prices.get(pos["sym"])
        pnl = _position_pnl(a, pos, p) if p else 0.0
        sign = "+" if pnl >= 0 else ""
        arrow = "🟢" if pos["dir"] == "buy" else "🔴"
        now_s = f"{p:.{a.decimals}f}" if p else "N/A"
        sl_s = f"{pos['sl']:.{a.decimals}f}" if pos.get("sl") else "—"
        tp_s = f"{pos['tp']:.{a.decimals}f}" if pos.get("tp") else "—"
        lines.append(
            f"{arrow} <b>{a.symbol}</b> {pos['dir'].upper()}  {pos['lot']} لات\n"
            f"   ورود: <code>{pos['open']:.{a.decimals}f}</code>   فعلی: <code>{now_s}</code>\n"
            f"   SL: <code>{sl_s}</code>   TP: <code>{tp_s}</code>\n"
            f"   P/L: <b>{sign}${pnl:,.2f}</b>   <code>#{pid}</code>\n\n"
        )
    return "".join(lines)

def build_history_text(acc):
    hist = list(reversed(acc.get("history", [])))[:15]
    if not hist:
        return "📭 تاریخچه‌ای موجود نیست."
    lines = ["📜 <b>۱۵ معامله آخر:</b>\n"]
    for h in hist:
        a = ASSETS[h["sym"]]
        sign = "+" if h["pnl"] >= 0 else ""
        dir_fa = "خرید" if h["dir"] == "buy" else "فروش"
        lines.append(f"{a.emoji} {a.symbol} {dir_fa} {h['lot']} لات — <b>{sign}${h['pnl']:,.2f}</b> ({h['reason']})")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════
async def _reg(update):
    u = update.effective_user
    cloud.ensure_user(u.id, u.username or "")
    cloud.ensure_prop(u.id)
    cloud.inc(u.id)

async def _notify_events(bot, uid, events):
    for ev in events:
        try:
            if ev["type"] in ("sl", "tp"):
                a = ASSETS[ev["sym"]]
                tag = "🛑 استاپ‌لاس" if ev["type"] == "sl" else "🎯 تارگت"
                sign = "+" if ev["pnl"] >= 0 else ""
                await bot.send_message(uid, parse_mode=ParseMode.HTML, text=(
                    f"{tag} فعال شد!\n\n{a.emoji} {a.symbol}\nP/L: <b>{sign}${ev['pnl']:,.2f}</b>"
                ))
            elif ev["type"] == "fail":
                reason_fa = {"daily_loss": "عبور از سقف ضرر روزانه ❌", "total_loss": "عبور از سقف ضرر کل ❌"}.get(ev["reason"], "نقض قوانین")
                await bot.send_message(uid, parse_mode=ParseMode.HTML, text=(
                    f"💀 <b>چالش ناموفق بود!</b>\n\nدلیل: {reason_fa}\n\n"
                    f"برای شروع دوباره، از پنل ترید روی «🔄 شروع چالش جدید» بزنید."
                ))
            elif ev["type"] == "pass":
                to = ev["to_phase"]
                if to == 3:
                    text = "🏆 <b>تبریک! فاندد شدید!</b>\n\nاکانت فاندد با بالانس $10,000 فعال شد."
                else:
                    name = PROP_PHASES[to]["name"]
                    text = f"🎉 <b>تبریک! فاز قبلی پاس شد!</b>\n\nوارد <b>{name}</b> با بالانس تازه $10,000 شدید."
                await bot.send_message(uid, parse_mode=ParseMode.HTML, text=text)
        except Exception as e:
            log.error(f"notify uid={uid}: {e}")

async def _send_chart(sym, tf_key, chat_id, reply_to_msg_id, bot, fail_text_fn):
    loading = await bot.send_message(
        chat_id=chat_id,
        text=f"📈 رسم نمودار {ASSETS[sym].symbol} ({TIMEFRAMES[tf_key][3]})…",
        reply_to_message_id=reply_to_msg_id,
    )

    live = get_price(sym, force=True)
    if live:
        cloud.push_price(sym, live)

    buf = create_chart(sym, tf_key, live_price=live)
    if not buf:
        await loading.edit_text(fail_text_fn(), reply_markup=kb_tf(sym))
        return

    a = ASSETS[sym]
    _, _, label_en, label_fa, _ = TIMEFRAMES[tf_key]
    live_str = f"{live:.{a.decimals}f}" if live else "N/A"
    caption = (
        f"{a.emoji} <b>{a.symbol}</b>   {label_fa}\n"
        f"💰 Live: <code>{live_str}</code>   ⏱ {str_ir()}\n"
        f"<i>Candles | EMA21/50 | BB(20,2) | RSI(14) | MACD(12,26,9)</i>"
    )

    await bot.send_photo(
        chat_id=chat_id,
        photo=buf,
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=kb_after_chart(sym),
    )

    try:
        await loading.delete()
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reg(update)
    await update.effective_message.reply_text(
        "👋 <b>PropTrader Bot</b>\n\n"
        "شبیه‌ساز چالش پراپ‌فرم با قیمت لحظه‌ای فارکس و طلا.\n\n"
        f"💰 اکانت دمو <b>${PROP_INITIAL_BALANCE:,.0f}</b> برای شما فعال شد!\n"
        "🎯 فاز۱: تارگت ۸٪  →  فاز۲: تارگت ۵٪  →  🏆 فاندد\n"
        "🛑 حداکثر ضرر روزانه ۵٪  |  حداکثر ضرر کل ۱۰٪\n"
        "⚙️ لوریج ۱:۱۰۰  |  لات واقعی  |  SL/TP خودکار\n\n"
        "📊 <b>چارت زنده:</b> کندل‌استیک + EMA + BB + RSI + MACD\n\n"
        "منوی زیر 👇",
        parse_mode=ParseMode.HTML, reply_markup=kb_main(),
    )

async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reg(update)
    msg = await update.effective_message.reply_text("⏳")
    await msg.edit_text(build_all_prices(), parse_mode=ParseMode.HTML, reply_markup=kb_main())

async def cmd_chart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reg(update)
    args = ctx.args
    sym = (args[0].upper() if args else "XAUUSD")
    if sym not in ASSETS: sym = "XAUUSD"
    tf  = (args[1].lower() if len(args) > 1 else "1m")
    if tf not in TIMEFRAMES: tf = "1m"

    msg = update.effective_message
    await _send_chart(
        sym, tf,
        chat_id=msg.chat_id,
        reply_to_msg_id=msg.message_id,
        bot=ctx.bot,
        fail_text_fn=lambda: f"⚠️ دریافت داده ناموفق.\n/chart {sym} {tf}",
    )

async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reg(update)
    await update.effective_message.reply_text(
        build_alerts_text(update.effective_user.id),
        parse_mode=ParseMode.HTML, reply_markup=kb_alerts(),
    )

async def cmd_trade(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reg(update)
    uid = update.effective_user.id
    acc = cloud.ensure_prop(uid)
    prices = _all_prices()
    events = recompute(acc, prices)
    cloud.save_prop(uid, acc)
    if events:
        await _notify_events(ctx.bot, uid, events)
    await update.effective_message.reply_text(
        build_dashboard_text(acc, prices), parse_mode=ParseMode.HTML, reply_markup=kb_prop_main(acc)
    )

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("pending_alert", None)
    ctx.user_data.pop("pending_trade", None)
    ctx.user_data.pop("pending_edit", None)
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

    async def edit(text, kb=None):
        try:
            kw = {"parse_mode": ParseMode.HTML}
            if kb is not None: kw["reply_markup"] = kb
            await q.edit_message_text(text, **kw)
        except Exception as e:
            log.warning(f"edit_message_text failed: {e} — sending new message")
            kw2 = {"chat_id": q.message.chat_id, "text": text, "parse_mode": ParseMode.HTML}
            if kb is not None: kw2["reply_markup"] = kb
            await ctx.bot.send_message(**kw2)

    if d == "main":
        await edit("🏠 <b>منوی اصلی</b>\n\nیک گزینه انتخاب کنید:", kb_main()); return

    if d == "all":
        await edit("⏳")
        await edit(build_all_prices(), kb_main()); return

    if d == "stats":
        u, r = cloud.stats()
        await edit(
            f"📊 <b>آمار</b>\n\n"
            f"👥 کاربران: <b>{u}</b>\n📈 درخواست‌ها: <b>{r}</b>\n\n⏱ {str_ir()}",
            kb_main()
        ); return

    if d == "help":
        await edit(
            "❓ <b>راهنما — PropTrader Bot</b>\n\n"
            "/start — منو\n/price — همه قیمت‌ها\n"
            "/chart [sym] [tf] — نمودار\n/alerts — هشدارها\n"
            "/trade — پنل ترید پراپ\n/cancel — لغو\n\n"
            "<b>TF:</b> 1m | 5m | 15m | 1h | 4h | 1d\n"
            "<b>sym:</b> XAUUSD | EURUSD | USDJPY | GBPUSD\n\n"
            "💼 <b>پراپ:</b>\n"
            "• بالانس شروع: $10,000  |  لوریج 1:100\n"
            "• فاز۱: تارگت ۸٪  →  فاز۲: تارگت ۵٪  →  فاندد\n"
            "• حداکثر ضرر روزانه ۵٪  |  حداکثر ضرر کل ۱۰٪\n"
            "• حداقل ۴ روز معاملاتی هر فاز\n"
            "• SL/TP هر ۲۰ ثانیه بررسی می‌شود\n\n"
            "📊 <b>اندیکاتور چارت:</b> EMA21/50 | BB(20,2) | RSI(14) | MACD(12,26,9) | Volume",
            kb_main()
        ); return

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

    if d == "chart_menu":
        await edit("📈 <b>انتخاب نمودار</b>\n\nدارایی را انتخاب کنید:", kb_chart_menu())
        return

    if d.startswith("chart:") and d.count(":") == 1:
        sym = d[6:]
        if sym not in ASSETS: return
        a = ASSETS[sym]
        await edit(
            f"📈 <b>تایم‌فریم — {a.symbol}</b>\n\n"
            f"⚡ 1M = کندل ۱ دقیقه‌ای (اسکلپ)\n"
            f"🔥 5M / ⏱ 15M / 📅 1H / 📆 4H / 🗓 Daily",
            kb_tf(sym)
        ); return

    if d.startswith("tf:"):
        parts = d.split(":")
        if len(parts) != 3: return
        sym, tf_key = parts[1], parts[2]
        if sym not in ASSETS or tf_key not in TIMEFRAMES: return

        try:
            await q.edit_message_text(
                f"📈 رسم نمودار {ASSETS[sym].symbol}  "
                f"{TIMEFRAMES[tf_key][2]}…\n⏳"
            )
        except Exception:
            pass

        live = get_price(sym, force=True)
        if live:
            cloud.push_price(sym, live)

        buf = create_chart(sym, tf_key, live_price=live)

        if not buf:
            try:
                await q.edit_message_text(
                    f"⚠️ دریافت داده ناموفق برای {ASSETS[sym].symbol}.\n"
                    f"تایم‌فریم دیگری امتحان کنید.",
                    reply_markup=kb_tf(sym)
                )
            except Exception:
                await ctx.bot.send_message(
                    chat_id=q.message.chat_id,
                    text=f"⚠️ خطا در دریافت داده {ASSETS[sym].symbol}.",
                    reply_markup=kb_tf(sym)
                )
            return

        a = ASSETS[sym]
        _, _, label_en, label_fa, _ = TIMEFRAMES[tf_key]
        live_str = f"{live:.{a.decimals}f}" if live else "N/A"
        caption = (
            f"{a.emoji} <b>{a.symbol}</b>   {label_fa}\n"
            f"💰 Live: <code>{live_str}</code>   ⏱ {str_ir()}\n"
            f"<i>Candles | EMA21/50 | BB(20,2) | RSI(14) | MACD(12,26,9)</i>"
        )

        await ctx.bot.send_photo(
            chat_id=q.message.chat_id,
            photo=buf,
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=kb_after_chart(sym),
        )

        try:
            await q.message.delete()
        except Exception:
            pass
        return

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
        ); return

    if d == "my_alerts":
        await edit(build_alerts_text(uid), kb_alerts()); return

    if d == "alerts_clean":
        cloud.clear_done(uid)
        await edit(build_alerts_text(uid) + "\n\n✅ پاک شد.", kb_alerts())
        return

    # ── PROP: داشبورد ──
    if d == "pt:main":
        acc = cloud.ensure_prop(uid)
        prices = _all_prices()
        events = recompute(acc, prices)
        cloud.save_prop(uid, acc)
        if events:
            await _notify_events(ctx.bot, uid, events)
        await edit(build_dashboard_text(acc, prices), kb_prop_main(acc))
        return

    # ── PROP: پوزیشن جدید ──
    if d == "pt:new":
        acc = cloud.ensure_prop(uid)
        if acc["status"] != "active":
            await edit("⚠️ چالش شما فعال نیست. ابتدا چالش جدید شروع کنید.", kb_prop_main(acc)); return
        await edit("🎯 دارایی مورد نظر را انتخاب کنید:", kb_prop_assets())
        return

    if d.startswith("pt:sym:"):
        sym = d.split(":")[2]
        if sym not in ASSETS: return
        a = ASSETS[sym]
        p = get_price(sym)
        p_s = f"{p:.{a.decimals}f}" if p else "N/A"
        await edit(
            f"{a.emoji} <b>{a.symbol}</b>\nقیمت لحظه‌ای: <code>{p_s}</code>\n\n"
            f"جهت معامله را انتخاب کنید:",
            kb_prop_dir(sym)
        ); return

    if d.startswith("pt:dir:"):
        _, _, sym, direction = d.split(":")
        if sym not in ASSETS: return
        ctx.user_data["pending_trade"] = {"stage": "lot", "sym": sym, "dir": direction}
        await edit(
            f"📏 حجم معامله (لات) را وارد کنید:\n"
            f"حداقل {PROP_MIN_LOT} — حداکثر {PROP_MAX_LOT}\nمثال: <code>0.10</code>\n\n"
            f"❌ /cancel برای لغو"
        ); return

    if d.startswith("pt:skip:"):
        stage = d.split(":")[2]
        pend = ctx.user_data.get("pending_trade")
        if not pend: return
        if stage == "sl":
            pend["sl"] = None
            pend["stage"] = "tp"
            await edit("🎯 قیمت TP (هدف سود) را وارد کنید یا رد شوید:", kb_skip("tp"))
        elif stage == "tp":
            pend["tp"] = None
            async def responder(text, kb):
                await edit(text, kb)
            await _open_and_report(uid, pend, ctx, responder)
        return

    # ── PROP: لیست پوزیشن‌ها ──
    if d == "pt:positions":
        acc = cloud.ensure_prop(uid)
        prices = _all_prices()
        await edit(build_positions_text(acc, prices), kb_positions(acc))
        return

    if d.startswith("pt:close:"):
        pid = d.split(":")[2]
        acc = cloud.ensure_prop(uid)
        pos = acc["positions"].get(pid)
        if not pos:
            await edit("⚠️ این پوزیشن یافت نشد.", kb_prop_main(acc)); return
        prices = _all_prices(force=True)
        price = prices.get(pos["sym"]) or get_price(pos["sym"], force=True)
        a = ASSETS[pos["sym"]]
        pnl_preview = _position_pnl(a, pos, price) if price else 0.0
        close_position_manual(acc, pid, price)
        events = recompute(acc, prices)
        cloud.save_prop(uid, acc)
        if events:
            await _notify_events(ctx.bot, uid, events)
        sign = "+" if pnl_preview >= 0 else ""
        await edit(f"✅ پوزیشن #{pid} بسته شد.\nP/L: <b>{sign}${pnl_preview:,.2f}</b>", kb_prop_main(acc))
        return

    if d.startswith("pt:edit:"):
        pid = d.split(":")[2]
        acc = cloud.ensure_prop(uid)
        if pid not in acc["positions"]:
            await edit("⚠️ یافت نشد.", kb_prop_main(acc)); return
        await edit(f"✏️ ویرایش پوزیشن #{pid}", kb_edit_pos(pid))
        return

    if d.startswith("pt:setsl:") or d.startswith("pt:settp:"):
        field = "sl" if "setsl" in d else "tp"
        pid = d.split(":")[2]
        ctx.user_data["pending_edit"] = {"pid": pid, "field": field}
        await edit(f"✏️ مقدار جدید {field.upper()} را وارد کنید:\n\n❌ /cancel برای لغو")
        return

    if d.startswith("pt:clrsl:") or d.startswith("pt:clrtp:"):
        field = "sl" if "clrsl" in d else "tp"
        pid = d.split(":")[2]
        acc = cloud.ensure_prop(uid)
        edit_position(acc, pid, clear_sl=(field == "sl"), clear_tp=(field == "tp"))
        cloud.save_prop(uid, acc)
        await edit(f"✅ {field.upper()} حذف شد.", kb_positions(acc))
        return

    # ── PROP: تاریخچه / ریست ──
    if d == "pt:history":
        acc = cloud.ensure_prop(uid)
        await edit(build_history_text(acc), kb_prop_main(acc))
        return

    if d == "pt:reset":
        cloud.reset_prop(uid)
        acc = cloud.ensure_prop(uid)
        await edit(
            "🔄 <b>چالش جدید شروع شد!</b>\nبالانس: $10,000\n\n" + build_dashboard_text(acc, _all_prices()),
            kb_prop_main(acc)
        ); return

# ══════════════════════════════════════════════════════════════════════
# PROP: تکمیل معامله (مشترک بین کال‌بک و پیام متنی)
# ══════════════════════════════════════════════════════════════════════
async def _open_and_report(uid, pend, ctx, responder):
    sym, direction = pend["sym"], pend["dir"]
    a = ASSETS[sym]
    price = get_price(sym, force=True)
    acc = cloud.ensure_prop(uid)
    prices = _all_prices()
    ctx.user_data.pop("pending_trade", None)

    if price is None:
        await responder("⚠️ خطا در دریافت قیمت. دوباره تلاش کنید.", kb_prop_main(acc)); return

    pid, err = open_position(acc, sym, direction, pend["lot"], price,
                              sl=pend.get("sl"), tp=pend.get("tp"), prices=prices)
    if err == "insufficient_margin":
        await responder("⚠️ مارجین کافی نیست. حجم کمتری انتخاب کنید.", kb_prop_main(acc)); return

    cloud.save_prop(uid, acc)
    dir_fa = "خرید 🟢" if direction == "buy" else "فروش 🔴"
    sl_s = f"{pend['sl']:.{a.decimals}f}" if pend.get("sl") else "—"
    tp_s = f"{pend['tp']:.{a.decimals}f}" if pend.get("tp") else "—"
    text = (
        f"✅ <b>پوزیشن باز شد!</b>\n\n{a.emoji} {a.symbol}  {dir_fa}\n"
        f"حجم: {pend['lot']} لات\nقیمت ورود: <code>{price:.{a.decimals}f}</code>\n"
        f"SL: <code>{sl_s}</code>   TP: <code>{tp_s}</code>   #{pid}"
    )
    await responder(text, kb_prop_main(acc))

# ══════════════════════════════════════════════════════════════════════
# MESSAGE HANDLER  (alert / trade-lot / sl / tp / edit-sl-tp)
# ══════════════════════════════════════════════════════════════════════
async def _handle_trade_input(update, ctx, pend, txt):
    msg = update.effective_message
    sym = pend["sym"]; a = ASSETS[sym]
    stage = pend["stage"]
    try:
        val = float(txt.replace(",", "."))
    except ValueError:
        await msg.reply_text("⚠️ عدد معتبر وارد کنید.\n❌ /cancel برای لغو"); return

    if stage == "lot":
        if not (PROP_MIN_LOT <= val <= PROP_MAX_LOT):
            await msg.reply_text(f"⚠️ حجم باید بین {PROP_MIN_LOT} و {PROP_MAX_LOT} باشد."); return
        pend["lot"] = round(val, 2)
        pend["stage"] = "sl"
        await msg.reply_text("🛑 قیمت SL (حد ضرر) را وارد کنید یا رد شوید:", reply_markup=kb_skip("sl"))
        return

    if stage == "sl":
        pend["sl"] = val
        pend["stage"] = "tp"
        await msg.reply_text("🎯 قیمت TP (هدف سود) را وارد کنید یا رد شوید:", reply_markup=kb_skip("tp"))
        return

    if stage == "tp":
        pend["tp"] = val
        async def responder(text, kb):
            await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        await _open_and_report(update.effective_user.id, pend, ctx, responder)
        return

async def _handle_edit_input(update, ctx, pend, txt):
    msg = update.effective_message
    try:
        val = float(txt.replace(",", "."))
    except ValueError:
        await msg.reply_text("⚠️ عدد معتبر وارد کنید.\n❌ /cancel برای لغو"); return

    uid = update.effective_user.id
    acc = cloud.ensure_prop(uid)
    pid, field = pend["pid"], pend["field"]
    ok = edit_position(acc, pid, **{field: val})
    ctx.user_data.pop("pending_edit", None)
    if not ok:
        await msg.reply_text("⚠️ پوزیشن یافت نشد.", reply_markup=kb_prop_main(acc)); return
    cloud.save_prop(uid, acc)
    await msg.reply_text(f"✅ {field.upper()} به‌روزرسانی شد.", reply_markup=kb_positions(acc))

async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.effective_message.text.strip()
    if txt.startswith("/"): return

    pend_trade = ctx.user_data.get("pending_trade")
    if pend_trade:
        await _handle_trade_input(update, ctx, pend_trade, txt); return

    pend_edit = ctx.user_data.get("pending_edit")
    if pend_edit:
        await _handle_edit_input(update, ctx, pend_edit, txt); return

    pending = ctx.user_data.get("pending_alert")
    if not pending: return
    try:
        target = float(txt.replace(",", "."))
    except ValueError:
        await update.effective_message.reply_text(
            "⚠️ عدد معتبر وارد کنید.\nمثال: <code>3350</code>\n/cancel برای لغو",
            parse_mode=ParseMode.HTML
        ); return

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
        parse_mode=ParseMode.HTML, reply_markup=kb_main(),
    )

# ══════════════════════════════════════════════════════════════════════
# ALERT CHECKER
# ══════════════════════════════════════════════════════════════════════
async def check_alerts(app: Application):
    try:
        with cloud._lock:
            snap = {
                uid: {sym: list(al) for sym, al in smap.items()}
                for uid, smap in cloud._data.get("alerts", {}).items()
            }
    except Exception as e:
        log.error(f"alert snap: {e}"); return

    for uid_str, smap in snap.items():
        uid = int(uid_str)
        for sym, al_list in smap.items():
            if not al_list or sym not in ASSETS: continue
            price = get_price(sym)
            if price is None: continue
            a = ASSETS[sym]
            for idx, al in enumerate(al_list):
                if al.get("done"): continue
                target = al["target"]
                fired = ((al["dir"] == "up"   and price >= target) or
                         (al["dir"] == "down"  and price <= target))
                if not fired: continue
                arrow  = "📈" if al["dir"] == "up" else "📉"
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
# PROP LOOP — موتور ریسک/SL/TP پس‌زمینه
# ══════════════════════════════════════════════════════════════════════
async def prop_loop(app: Application):
    while True:
        try:
            await asyncio.sleep(20)
            prices = _all_prices()
            for uid_str in cloud.all_prop_uids():
                uid = int(uid_str)
                acc = cloud.get_prop(uid)
                if not acc or acc["status"] != "active":
                    continue
                events = recompute(acc, prices)
                if events:
                    cloud.save_prop(uid, acc)
                    await _notify_events(app.bot, uid, events)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"prop_loop: {e}")

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
    asyncio.create_task(prop_loop(app))
    for sym in ASSETS:
        p = get_price(sym)
        if p: cloud.push_price(sym, p)
    log.info("PropTrader Bot v1.0 ready")

async def on_shutdown(app: Application):
    cloud.save(force=True)
    log.info("shutdown")

async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.error(f"error: {ctx.error}", exc_info=ctx.error)


async def handle_web_app_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """دریافت دستورات از Mini App"""
    import json
    
    data_str = update.effective_message.web_app_data.data
    uid = update.effective_user.id
    
    try:
        data = json.loads(data_str)
        action = data.get("action")
        
        if action == "request_new_trade":
            acc = cloud.ensure_prop(uid)
            if acc["status"] != "active":
                await update.effective_message.reply_text(
                    "⚠️ چالش شما فعال نیست.",
                    reply_markup=kb_prop_main(acc)
                )
                return
            await update.effective_message.reply_text(
                "🎯 <b>انتخاب دارایی برای ترید جدید</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_prop_assets()
            )
            
        elif action == "close_position":
            pid = str(data.get("pid"))
            acc = cloud.ensure_prop(uid)
            pos = acc["positions"].get(pid)
            
            if not pos:
                await update.effective_message.reply_text("⚠️ پوزیشن یافت نشد.")
                return
            
            prices = _all_prices(force=True)
            price = prices.get(pos["sym"]) or get_price(pos["sym"], force=True)
            if not price:
                await update.effective_message.reply_text("⚠️ خطا در دریافت قیمت.")
                return
            
            a = ASSETS[pos["sym"]]
            pnl_preview = _position_pnl(a, pos, price)
            close_position_manual(acc, pid, price)
            events = recompute(acc, prices)
            cloud.save_prop(uid, acc)
            
            if events:
                await _notify_events(ctx.bot, uid, events)
            
            sign = "+" if pnl_preview >= 0 else ""
            await update.effective_message.reply_text(
                f"✅ پوزیشن #{pid} بسته شد.\nP/L: <b>{sign}${pnl_preview:,.2f}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_prop_main(acc)
            )
            
        elif action == "show_history":
            acc = cloud.ensure_prop(uid)
            await update.effective_message.reply_text(
                build_history_text(acc),
                parse_mode=ParseMode.HTML,
                reply_markup=kb_prop_main(acc)
            )
            
    except json.JSONDecodeError:
        log.error("Invalid JSON from WebApp")
    except Exception as e:
        log.error(f"WebApp handler error: {e}")

# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKEN:
        log.critical("BOT_TOKEN not set!"); sys.exit(1)
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
    app.add_handler(CommandHandler("trade",  cmd_trade))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    app.add_error_handler(on_error)

    def _sig(s, f): cloud.save(force=True); sys.exit(0)
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)
    atexit.register(lambda: cloud.save(force=True))

    log.info("PropTrader Bot v1.0 starting...")
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
