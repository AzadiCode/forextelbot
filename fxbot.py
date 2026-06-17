#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScalpBot Pro v5
منبع داده: Twelve Data (realtime, بدون تاخیر)
پلن رایگان: 8 کال/دقیقه | 800 کال/روز
XAU/USD, EUR/USD, USD/JPY, GBP/USD — 1m تا 1d
"""

import os, sys, asyncio, logging, signal, atexit, io, threading, time, statistics
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional

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
log = logging.getLogger("ScalpBot")

# ══════════════════════════════════════════════════════════════════════
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
PORT           = int(os.environ.get("PORT", 5000))
JSONBIN_KEY    = os.environ.get("JSONBIN_API_KEY", "")
JSONBIN_BIN    = os.environ.get("JSONBIN_BIN_ID", "")
TWELVEDATA_KEY = os.environ.get("TWELVEDATA_API_KEY", "")   # ← کلید جدید

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
def now_ir(): return datetime.now(IRAN_TZ)
def str_ir(fmt="%H:%M:%S"): return now_ir().strftime(fmt)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
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
    symbol: str          # نمایشی
    td_symbol: str       # Twelve Data symbol
    decimals: int
    pip_size: float
    sources: list        # fallback price sources

ASSETS: dict[str, Asset] = {
    "XAUUSD": Asset(
        key="XAUUSD", name="Gold", emoji="🥇", symbol="XAU/USD",
        td_symbol="XAU/USD",
        decimals=2, pip_size=0.1,
        sources=[
            Source("GoldPrice",
                   "https://data-asg.goldprice.org/dbXRates/USD",
                   lambda d: d["items"][0]["xauPrice"], 1000, 12000),
            Source("Swissquote",
                   "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD",
                   lambda d: (float(d[0]["spreadProfilePrices"][0]["ask"]) +
                              float(d[0]["spreadProfilePrices"][0]["bid"])) / 2, 1000, 12000),
        ],
    ),
    "EURUSD": Asset(
        key="EURUSD", name="EUR/USD", emoji="💶", symbol="EUR/USD",
        td_symbol="EUR/USD",
        decimals=5, pip_size=0.0001,
        sources=[
            Source("Swissquote",
                   "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/EUR/USD",
                   lambda d: (float(d[0]["spreadProfilePrices"][0]["ask"]) +
                              float(d[0]["spreadProfilePrices"][0]["bid"])) / 2, 0.8, 1.5),
        ],
    ),
    "USDJPY": Asset(
        key="USDJPY", name="USD/JPY", emoji="💴", symbol="USD/JPY",
        td_symbol="USD/JPY",
        decimals=3, pip_size=0.01,
        sources=[
            Source("Swissquote",
                   "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/USD/JPY",
                   lambda d: (float(d[0]["spreadProfilePrices"][0]["ask"]) +
                              float(d[0]["spreadProfilePrices"][0]["bid"])) / 2, 100, 200),
        ],
    ),
    "GBPUSD": Asset(
        key="GBPUSD", name="GBP/USD", emoji="💷", symbol="GBP/USD",
        td_symbol="GBP/USD",
        decimals=5, pip_size=0.0001,
        sources=[
            Source("Swissquote",
                   "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/GBP/USD",
                   lambda d: (float(d[0]["spreadProfilePrices"][0]["ask"]) +
                              float(d[0]["spreadProfilePrices"][0]["bid"])) / 2, 1.1, 1.6),
        ],
    ),
}

# ══════════════════════════════════════════════════════════════════════
# TIMEFRAMES
# Twelve Data intervals: 1min 5min 15min 30min 45min 1h 2h 4h 8h 1day 1week 1month
# outputsize max = 5000
# ══════════════════════════════════════════════════════════════════════
MAX_BARS = 200

TIMEFRAMES = {
    # key: (td_interval, label_en, label_fa, x_fmt)
    "1m":  ("1min",  "1M  |  1m candles",  "۱ دقیقه",  "%H:%M"),
    "5m":  ("5min",  "5M  |  5m candles",  "۵ دقیقه",  "%m/%d %H:%M"),
    "15m": ("15min", "15M |  15m candles", "۱۵ دقیقه", "%m/%d %H:%M"),
    "1h":  ("1h",    "1H  |  1h candles",  "۱ ساعت",   "%m/%d"),
    "4h":  ("4h",    "4H  |  4h candles",  "۴ ساعت",   "%m/%d"),
    "1d":  ("1day",  "1D  |  1d candles",  "روزانه",   "%Y/%m/%d"),
}

# ══════════════════════════════════════════════════════════════════════
# PRICE FETCHER (قیمت لحظه‌ای)
# اول Twelve Data price endpoint، بعد fallback به منابع دیگر
# ══════════════════════════════════════════════════════════════════════
_price_cache: dict[str, tuple[float, float]] = {}
CACHE_TTL = 12

def _get_price_twelvedata(asset: Asset) -> Optional[float]:
    """قیمت لحظه‌ای از Twelve Data /price endpoint"""
    if not TWELVEDATA_KEY:
        return None
    try:
        r = requests.get(
            "https://api.twelvedata.com/price",
            params={"symbol": asset.td_symbol, "apikey": TWELVEDATA_KEY},
            headers=_HEADERS, timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if "price" in data:
            p = float(data["price"])
            if asset.sources[0].min_val < p < asset.sources[0].max_val:
                return round(p, asset.decimals)
    except Exception as e:
        log.warning(f"TD price {asset.key}: {str(e)[:60]}")
    return None

def get_price(symbol: str, force: bool = False) -> Optional[float]:
    if symbol not in ASSETS:
        return None
    if not force and symbol in _price_cache:
        p, ts = _price_cache[symbol]
        if time.time() - ts < CACHE_TTL:
            return p
    asset = ASSETS[symbol]

    # اول Twelve Data
    p = _get_price_twelvedata(asset)
    if p:
        _price_cache[symbol] = (p, time.time())
        log.info(f"price {symbol}={p} [TwelveData]")
        return p

    # fallback منابع دیگر
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
# OHLC FETCHER — Twelve Data /time_series
# ══════════════════════════════════════════════════════════════════════
def fetch_ohlc_twelvedata(asset: Asset, interval: str, outputsize: int = MAX_BARS) -> Optional[tuple]:
    """
    داده کندل از Twelve Data.
    interval: 1min | 5min | 15min | 1h | 4h | 1day
    """
    if not TWELVEDATA_KEY:
        log.error("TWELVEDATA_API_KEY not set!")
        return None
    try:
        r = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol":     asset.td_symbol,
                "interval":   interval,
                "outputsize": outputsize,
                "apikey":     TWELVEDATA_KEY,
                "timezone":   "Asia/Tehran",   # مستقیم به وقت ایران
                "order":      "ASC",           # قدیمی‌ترین اول
            },
            headers=_HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()

        if data.get("status") == "error":
            log.error(f"TD OHLC error: {data.get('message')}")
            return None

        values = data.get("values")
        if not values:
            log.error(f"TD OHLC: no values for {asset.td_symbol}")
            return None

        times, opens, highs, lows, closes, vols = [], [], [], [], [], []
        for bar in values:
            try:
                # Twelve Data datetime با timezone=Asia/Tehran می‌آید
                # فرمت: "2024-06-17 08:15:00"
                dt_naive = datetime.strptime(bar["datetime"], "%Y-%m-%d %H:%M:%S")
                dt = dt_naive.replace(tzinfo=IRAN_TZ)
            except ValueError:
                # 1day فرمت: "2024-06-17"
                try:
                    dt_naive = datetime.strptime(bar["datetime"], "%Y-%m-%d")
                    dt = dt_naive.replace(tzinfo=IRAN_TZ)
                except Exception:
                    continue

            o = float(bar["open"])
            h = float(bar["high"])
            l = float(bar["low"])
            c = float(bar["close"])
            v = float(bar.get("volume") or 0)

            times.append(dt)
            opens.append(o)
            highs.append(h)
            lows.append(l)
            closes.append(c)
            vols.append(v)

        if len(closes) < 5:
            log.warning(f"TD OHLC: only {len(closes)} bars for {asset.td_symbol}")
            return None

        log.info(f"TD OHLC OK: {asset.td_symbol} {interval} → {len(closes)} bars")
        return times, opens, highs, lows, closes, vols

    except Exception as e:
        log.error(f"fetch_ohlc_twelvedata {asset.td_symbol}: {str(e)[:100]}")
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
# CHART ENGINE v5
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
    bodies = [abs(c - o) for c, o in zip(closes[:-1], opens[:-1])]
    ranges = [h - l for h, l in zip(highs[:-1], lows[:-1])]
    median_body  = statistics.median(bodies) if bodies else 1.0
    median_range = statistics.median(ranges) if ranges else 1.0

    for i, (t, o, h, l, c) in enumerate(zip(times, opens, highs, lows, closes)):
        is_up  = c >= o
        color  = UP_C if is_up else DN_C

        # آخرین کندل: cap wick و body
        if i == len(closes) - 1:
            cap_r = median_range * 2.5
            h = min(h, c + cap_r)
            l = max(l, c - cap_r)
            body_h = min(abs(c - o), median_body * 2)
            body_b = (c - body_h) if is_up else c
        else:
            body_h = abs(c - o)
            body_b = min(o, c)

        body_h = max(body_h, median_body * 0.05)   # doji حداقل

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
    td_interval, label_en, label_fa, x_fmt = TIMEFRAMES[tf_key]

    # ── fetch از Twelve Data ──
    ohlc = fetch_ohlc_twelvedata(asset, td_interval, outputsize=MAX_BARS)
    if ohlc is None:
        log.error(f"create_chart: no data for {symbol} {tf_key}")
        return None

    times, opens, highs, lows, closes, vols = ohlc

    # ── bar width با median gap (مقاوم در برابر gap نامساوی) ──
    times_num = [mdates.date2num(t) for t in times]
    if len(times_num) > 1:
        gaps = [times_num[i+1] - times_num[i] for i in range(len(times_num) - 1)]
        typical_gap = statistics.median(gaps)
        bar_w = typical_gap * 0.7
    else:
        bar_w = 0.0007

    # ── override live price روی کندل آخر ──
    if live_price is not None:
        closes[-1] = live_price
        candle_ranges = [h - l for h, l in zip(highs[:-1], lows[:-1])]
        med_range = statistics.median(candle_ranges) if candle_ranges else (live_price * 0.001)
        max_wick = med_range * 3
        raw_high = max(highs[-1], live_price)
        raw_low  = min(lows[-1],  live_price)
        highs[-1] = min(raw_high, live_price + max_wick)
        lows[-1]  = max(raw_low,  live_price - max_wick)

    dec = asset.decimals

    # ── اندیکاتورها ──
    ema21                 = _ema(closes, 21)
    ema50                 = _ema(closes, 50)
    bb_up, bb_mid, bb_low = _bollinger(closes, 20)
    rsi_vals              = _rsi(closes, 14)
    macd_l, sig_l, hist_l = _macd(closes)

    # ── آمار کلی ──
    cur    = closes[-1]
    start  = closes[0]
    hi     = max(highs)
    lo     = min(lows)
    chg    = cur - start
    chgp   = (chg / start * 100) if start else 0
    is_up  = chg >= 0
    main_c = UP_C if is_up else DN_C

    last_rsi  = next((v for v in reversed(rsi_vals) if v is not None), None)
    last_macd = next((v for v in reversed(macd_l)   if v is not None), None)

    # ══ Figure ══
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

    # ── candles ──
    _draw_candles(ax_c, times, opens, highs, lows, closes, bar_w)

    # ── helper plot valid ──
    def _pv(ax, ts, data, **kw):
        pts = [(t, v) for t, v in zip(ts, data) if v is not None]
        if pts:
            tt, vv = zip(*pts)
            ax.plot(tt, vv, **kw)

    # ── Bollinger ──
    _pv(ax_c, times, bb_up,  color=BB_C, lw=0.9, alpha=0.7, ls="--", label="BB(20)")
    _pv(ax_c, times, bb_mid, color=BB_C, lw=0.7, alpha=0.5, ls=":")
    _pv(ax_c, times, bb_low, color=BB_C, lw=0.9, alpha=0.7, ls="--")
    bb_pairs = [(t, u, l) for t, u, l in zip(times, bb_up, bb_low)
                if u is not None and l is not None]
    if bb_pairs:
        bbt, bbu, bbl = zip(*bb_pairs)
        ax_c.fill_between(bbt, bbu, bbl, color=BB_C, alpha=0.04, zorder=1)

    # ── EMA ──
    _pv(ax_c, times, ema21, color=EMA21_C, lw=1.1, alpha=0.9, label="EMA21", zorder=4)
    _pv(ax_c, times, ema50, color=EMA50_C, lw=1.1, alpha=0.9, label="EMA50", zorder=4)

    # ── قیمت جاری ──
    ax_c.axhline(cur, color=main_c, ls="-", alpha=0.25, lw=0.8, zorder=1)
    ax_c.text(1.002, cur, f"{cur:.{dec}f}",
              transform=ax_c.get_yaxis_transform(),
              fontsize=8.5, fontweight="bold", color="white",
              va="center", ha="left",
              bbox=dict(boxstyle="round,pad=0.3", fc=main_c, ec="none"))

    # ── H/L ──
    hi_i = highs.index(hi)
    lo_i = lows.index(lo)
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

    pr  = hi - lo
    pad = max(pr * 0.06, cur * 0.001)
    ax_c.set_ylim(lo - pad, hi + pad * 2.5)
    ax_c.legend(loc="upper left", fontsize=7,
                facecolor=BG, edgecolor=GRID_C, labelcolor=TEXT_C, framealpha=0.8)

    # ── Volume ──
    bar_td = timedelta(days=bar_w * 0.8)
    vcols = [UP_C if c >= o else DN_C for c, o in zip(closes, opens)]
    ax_v.bar(times, vols, color=vcols, alpha=0.5, width=bar_td)
    ax_v.set_yticks([])
    ax_v.text(1.002, 0.5, "VOL", transform=ax_v.transAxes,
              fontsize=7, color=DIM_C, va="center", ha="left")

    # ── RSI ──
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

    # ── MACD ──
    mpts = [(t, v) for t, v in zip(times, macd_l) if v is not None]
    spts = [(t, v) for t, v in zip(times, sig_l)  if v is not None]
    hpts = [(t, v) for t, v in zip(times, hist_l) if v is not None]
    if mpts:
        mt, mv = zip(*mpts)
        ax_m.plot(mt, mv, color=MACD_C, lw=1.0, label="MACD", zorder=3)
    if spts:
        st, sv = zip(*spts)
        ax_m.plot(st, sv, color=SIG_C, lw=0.9, label="Sig", zorder=3)
    if hpts:
        ht, hv = zip(*hpts)
        hc = [UP_C if v >= 0 else DN_C for v in hv]
        ax_m.bar(ht, hv, color=hc, alpha=0.5, width=bar_td, zorder=2)
    ax_m.axhline(0, color=DIM_C, lw=0.6, alpha=0.5)
    ax_m.legend(loc="upper left", fontsize=6.5,
                facecolor=BG, edgecolor=GRID_C, labelcolor=TEXT_C, framealpha=0.8)
    if last_macd is not None:
        mc = UP_C if last_macd >= 0 else DN_C
        ax_m.text(1.002, last_macd, f"{last_macd:.{max(1, dec-1)}f}",
                  transform=ax_m.get_yaxis_transform(),
                  fontsize=7, fontweight="bold", color="white",
                  va="center", ha="left",
                  bbox=dict(boxstyle="round,pad=0.2", fc=mc, ec="none"))
    ax_m.text(1.002, 0.5, "MACD", transform=ax_m.transAxes,
              fontsize=7, color=DIM_C, va="center", ha="left")

    # ── X axis ──
    ax_m.xaxis.set_major_formatter(mdates.DateFormatter(x_fmt, tz=IRAN_TZ))
    ax_m.xaxis.set_major_locator(mdates.AutoDateLocator(tz=IRAN_TZ))
    plt.setp(ax_m.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=7)
    for a in (ax_c, ax_v, ax_r):
        plt.setp(a.xaxis.get_majorticklabels(), visible=False)

    # ── Title ──
    sign   = "+" if is_up else ""
    arrow  = "▲" if is_up else "▼"
    rsi_s  = f"RSI: {last_rsi:.0f}" if last_rsi else ""
    macd_s = f"MACD: {last_macd:.{max(1, dec-1)}f}" if last_macd else ""
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
    fig.text(0.87, 0.01, "@ScalpBotPro", ha="right", fontsize=6.5, color=GRID_C)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=BG)
    buf.seek(0)
    plt.close(fig)
    return buf

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
            "alerts": {}, "last_updated": None,
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

cloud = CloudStorage()

# ══════════════════════════════════════════════════════════════════════
# FLASK
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route("/")
def root():
    u, r = cloud.stats()
    return f"ScalpBot Pro v5 | users={u} | reqs={r} | {str_ir()}"

@flask_app.route("/health")
def health():
    return jsonify(status="ok", time=str_ir(), version="v5")

def _run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════════════════════════════
def kb_main():
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
# MESSAGE BUILDERS
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
# HELPERS
# ══════════════════════════════════════════════════════════════════════
async def _reg(update):
    u = update.effective_user
    cloud.ensure_user(u.id, u.username or "")
    cloud.inc(u.id)

async def _send_chart(sym, tf_key, chat_id, reply_to_msg_id, bot, fail_text_fn):
    loading = await bot.send_message(
        chat_id=chat_id,
        text=f"📈 رسم نمودار {ASSETS[sym].symbol} ({TIMEFRAMES[tf_key][2]})…",
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
    _, label_en, label_fa, _ = TIMEFRAMES[tf_key]
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
        "👋 <b>ScalpBot Pro v5</b>\n\n"
        "قیمت realtime فارکس و طلا — ویژه اسکلپرها.\n"
        "منبع داده: <b>Twelve Data</b> (بدون تاخیر)\n\n"
        "📊 <b>چارت:</b> کندل‌استیک | EMA21/50 | BB | RSI | MACD | Volume\n"
        "⏱ <b>TF:</b> 1m | 5m | 15m | 1h | 4h | 1d  (۲۰۰ کندل آخر)\n\n"
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

    async def edit(text, kb=None):
        try:
            kw = {"parse_mode": ParseMode.HTML}
            if kb is not None: kw["reply_markup"] = kb
            await q.edit_message_text(text, **kw)
        except Exception as e:
            log.warning(f"edit failed: {e}")
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
            "❓ <b>راهنما — ScalpBot Pro v5</b>\n\n"
            "/start — منو\n/price — همه قیمت‌ها\n"
            "/chart [sym] [tf] — نمودار\n/alerts — هشدارها\n/cancel — لغو\n\n"
            "<b>TF:</b> 1m | 5m | 15m | 1h | 4h | 1d\n"
            "<b>sym:</b> XAUUSD | EURUSD | USDJPY | GBPUSD\n\n"
            "📡 منبع: Twelve Data (realtime)\n"
            "🔔 هشدار هر ۵ دقیقه بررسی می‌شود",
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
                f"{TIMEFRAMES[tf_key][1]}…\n⏳"
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
        _, label_en, label_fa, _ = TIMEFRAMES[tf_key]
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

# ══════════════════════════════════════════════════════════════════════
# MESSAGE HANDLER
# ══════════════════════════════════════════════════════════════════════
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pending = ctx.user_data.get("pending_alert")
    if not pending: return
    txt = update.effective_message.text.strip()
    if txt.startswith("/"): return
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
                fired = ((al["dir"] == "up"  and price >= target) or
                         (al["dir"] == "down" and price <= target))
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
    if not TWELVEDATA_KEY:
        log.critical("⚠️  TWELVEDATA_API_KEY not set! Charts will fail.")
    asyncio.create_task(bg_loop(app))
    for sym in ASSETS:
        p = get_price(sym)
        if p: cloud.push_price(sym, p)
    log.info("ScalpBot Pro v5 ready")

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
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)

    def _sig(s, f): cloud.save(force=True); sys.exit(0)
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)
    atexit.register(lambda: cloud.save(force=True))

    log.info("ScalpBot Pro v5 starting...")
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
