#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScalpBot Pro v3 — Candlestick + BB + MACD + 200-bar limit + 1m TF
"""

import os, sys, asyncio, logging, signal, atexit, io, threading, time
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
    "Accept-Language": "en-US,en;q=0.9",
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
    yahoo_sym: str
    yahoo_fb: str

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
# TIMEFRAMES
# Yahoo Finance interval limits:
#   1m  → range max: 7d  (ما range=1d میدیم → ~390 bar، برش به 200 آخر)
#   2m  → range max: 60d
#   5m  → range max: 60d
#   15m → range max: 60d
#   1h  → range max: 730d
#   1d  → unlimited
#
# MAX_BARS = 200 برای همه — کمتر از 200 کندل موجود باشه همه رو نشون میده
# ══════════════════════════════════════════════════════════════════════
MAX_BARS = 200

TIMEFRAMES = {
    # key: (yahoo_range, yahoo_interval, label_en, label_fa, x_fmt)
    "1m":  ("1d",  "1m",  "1M  |  1m candles",  "۱ دقیقه",  "%H:%M"),
    "5m":  ("2d",  "5m",  "5M  |  5m candles",  "۵ دقیقه",  "%H:%M"),
    "15m": ("5d",  "15m", "15M |  15m candles", "۱۵ دقیقه", "%m/%d %H:%M"),
    "1h":  ("15d", "1h",  "1H  |  1h candles",  "۱ ساعت",   "%m/%d %H:%M"),
    "4h":  ("60d", "1h",  "4H  |  1h candles",  "۴ ساعت",   "%m/%d"),
    "1d":  ("1y",  "1d",  "1D  |  1d candles",  "روزانه",   "%Y/%m/%d"),
}

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

# ══════════════════════════════════════════════════════════════════════
# INDICATORS
# ══════════════════════════════════════════════════════════════════════
def _sma(data: list, period: int) -> list:
    out = [None] * len(data)
    for i in range(period - 1, len(data)):
        out[i] = sum(data[i - period + 1 : i + 1]) / period
    return out

def _ema(data: list, period: int) -> list:
    out = [None] * len(data)
    k = 2 / (period + 1)
    start = period - 1
    while start < len(data) and data[start] is None:
        start += 1
    if start >= len(data):
        return out
    out[start] = sum(x for x in data[max(0, start - period + 1) : start + 1]
                     if x is not None) / period
    for i in range(start + 1, len(data)):
        if data[i] is not None and out[i - 1] is not None:
            out[i] = data[i] * k + out[i - 1] * (1 - k)
    return out

def _bollinger(closes: list, period: int = 20, std_mult: float = 2.0):
    mid = _sma(closes, period)
    upper, lower = [None] * len(closes), [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        mean = mid[i]
        if mean is None:
            continue
        sd = (sum((x - mean) ** 2 for x in window) / period) ** 0.5
        upper[i] = mean + std_mult * sd
        lower[i] = mean - std_mult * sd
    return upper, mid, lower

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

def _macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    signal_line = _ema(macd_line, signal)
    histogram = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, histogram

# ══════════════════════════════════════════════════════════════════════
# OHLC FETCHER
# ══════════════════════════════════════════════════════════════════════
def _fetch_ohlc(yahoo_sym: str, interval: str, range_: str) -> Optional[tuple]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}"
    # Try both v8 and v7 endpoints
    for endpoint in [
        f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}",
        f"https://query2.finance.yahoo.com/v8/finance/chart/{yahoo_sym}",
    ]:
        try:
            r = requests.get(endpoint, headers=_HEADERS,
                             params={"interval": interval, "range": range_,
                                     "includePrePost": "false"}, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            result = data["chart"]["result"][0]
            ts_list = result["timestamp"]
            q = result["indicators"]["quote"][0]

            times, opens, highs, lows, closes, vols = [], [], [], [], [], []
            for i, ts in enumerate(ts_list):
                c, o, h, l = q["close"][i], q["open"][i], q["high"][i], q["low"][i]
                if None in (c, o, h, l):
                    continue
                times.append(datetime.fromtimestamp(ts, tz=IRAN_TZ))
                opens.append(float(o)); highs.append(float(h))
                lows.append(float(l));  closes.append(float(c))
                v = q.get("volume", [None] * len(ts_list))[i]
                vols.append(float(v) if v else 0.0)

            if len(closes) < 5:
                log.warning(f"Yahoo {yahoo_sym}: only {len(closes)} valid bars")
                continue
            log.info(f"Yahoo {yahoo_sym} {range_}/{interval}: {len(closes)} bars")
            return times, opens, highs, lows, closes, vols
        except Exception as e:
            log.error(f"fetch_ohlc {yahoo_sym}: {str(e)[:80]}")
    return None

# ══════════════════════════════════════════════════════════════════════
# CHART ENGINE v3 — Full Candlestick + BB + MACD
# ══════════════════════════════════════════════════════════════════════

# رنگ‌های TradingView Dark
BG      = "#131722"
UP_C    = "#26a69a"   # سبز
DN_C    = "#ef5350"   # قرمز
UP_DIM  = "#1a7a70"
DN_DIM  = "#b03030"
GRID_C  = "#1e222d"
TEXT_C  = "#d1d4dc"
DIM_C   = "#787b86"
BB_C    = "#1565c0"
MACD_C  = "#26a69a"
SIG_C   = "#ef5350"
EMA21_C = "#f9a825"
EMA50_C = "#7b1fa2"


def _draw_candles(ax, times, opens, highs, lows, closes, bar_w):
    """رسم کندل‌استیک دستی روی ax."""
    for i, (t, o, h, l, c) in enumerate(zip(times, opens, highs, lows, closes)):
        is_up = c >= o
        color  = UP_C if is_up else DN_C
        body_b = min(o, c)
        body_h = abs(c - o) or (h - l) * 0.02   # کندل doji

        # wick
        ax.plot([t, t], [l, h], color=color, linewidth=0.7, zorder=2)
        # body
        rect = mpatches.FancyBboxPatch(
            (mdates.date2num(t) - bar_w / 2, body_b),
            bar_w, body_h,
            boxstyle="square,pad=0",
            linewidth=0,
            facecolor=color if is_up else color,
            edgecolor="none",
            zorder=3,
        )
        ax.add_patch(rect)


def create_chart(symbol: str, tf_key: str = "1m",
                 live_price: Optional[float] = None) -> Optional[io.BytesIO]:
    if symbol not in ASSETS or tf_key not in TIMEFRAMES:
        return None

    asset = ASSETS[symbol]
    yahoo_range, yahoo_interval, label_en, label_fa, x_fmt = TIMEFRAMES[tf_key]

    # ── fetch ──
    ohlc = _fetch_ohlc(asset.yahoo_sym, yahoo_interval, yahoo_range)
    if ohlc is None and asset.yahoo_fb != asset.yahoo_sym:
        ohlc = _fetch_ohlc(asset.yahoo_fb, yahoo_interval, yahoo_range)
    if ohlc is None:
        return None

    times, opens, highs, lows, closes, vols = ohlc

    # ── برش به 200 کندل آخر ──
    if len(times) > MAX_BARS:
        times  = times[-MAX_BARS:]
        opens  = opens[-MAX_BARS:]
        highs  = highs[-MAX_BARS:]
        lows   = lows[-MAX_BARS:]
        closes = closes[-MAX_BARS:]
        vols   = vols[-MAX_BARS:]

    # ── override live price ──
    if live_price is not None:
        closes[-1] = live_price
        highs[-1]  = max(highs[-1], live_price)
        lows[-1]   = min(lows[-1], live_price)

    # ── تبدیل datetime به float برای مستطیل‌ها ──
    times_num = [mdates.date2num(t) for t in times]
    bar_w = (times_num[-1] - times_num[0]) / len(times_num) * 0.7

    dec = asset.decimals

    # ── اندیکاتورها ──
    ema21      = _ema(closes, 21)
    ema50      = _ema(closes, 50)
    bb_up, bb_mid, bb_low = _bollinger(closes, 20)
    rsi_vals   = _rsi(closes, 14)
    macd_line, signal_line, histogram = _macd(closes)

    # ── آمار ──
    cur  = closes[-1]
    start = closes[0]
    hi   = max(highs)
    lo   = min(lows)
    chg  = cur - start
    chgp = (chg / start * 100) if start else 0
    is_up = chg >= 0
    main_c = UP_C if is_up else DN_C

    last_rsi = next((v for v in reversed(rsi_vals) if v is not None), None)
    last_macd = next((v for v in reversed(macd_line) if v is not None), None)
    last_sig  = next((v for v in reversed(signal_line) if v is not None), None)

    # ══ Figure Layout ══
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 8,
        "axes.unicode_minus": False,
    })

    fig = plt.figure(figsize=(14, 10), facecolor=BG)
    # 4 panels: candle(5) | volume(1) | RSI(1.5) | MACD(1.5)
    gs = fig.add_gridspec(
        4, 1,
        height_ratios=[5, 1, 1.5, 1.5],
        hspace=0.0,
        left=0.01, right=0.87,
        top=0.88, bottom=0.07,
    )
    ax_c  = fig.add_subplot(gs[0])              # candles
    ax_v  = fig.add_subplot(gs[1], sharex=ax_c) # volume
    ax_r  = fig.add_subplot(gs[2], sharex=ax_c) # RSI
    ax_m  = fig.add_subplot(gs[3], sharex=ax_c) # MACD

    for ax in (ax_c, ax_v, ax_r, ax_m):
        ax.set_facecolor(BG)
        ax.tick_params(colors=DIM_C, labelsize=7, length=2)
        ax.yaxis.set_label_position("right")
        ax.yaxis.tick_right()
        for sp in ax.spines.values():
            sp.set_color(GRID_C)
        ax.grid(True, color=GRID_C, linewidth=0.4, linestyle="-", zorder=0)

    # ══ PANEL 1: Candles + BB + EMA ══
    _draw_candles(ax_c, times, opens, highs, lows, closes, bar_w)

    # Bollinger Bands
    def _plot_valid(ax, times, data, **kw):
        pts = [(t, v) for t, v in zip(times, data) if v is not None]
        if pts:
            tt, vv = zip(*pts)
            ax.plot(tt, vv, **kw)

    _plot_valid(ax_c, times, bb_up,  color=BB_C, linewidth=0.9, alpha=0.7,
                linestyle="--", label="BB")
    _plot_valid(ax_c, times, bb_mid, color=BB_C, linewidth=0.8, alpha=0.5,
                linestyle=":")
    _plot_valid(ax_c, times, bb_low, color=BB_C, linewidth=0.9, alpha=0.7,
                linestyle="--")

    # BB fill
    bb_t = [t for t, u, l in zip(times, bb_up, bb_low) if u and l]
    bb_u = [u for u in bb_up if u is not None]
    bb_l = [l for l in bb_low if l is not None]
    if bb_t:
        ax_c.fill_between(bb_t, bb_u, bb_l, color=BB_C, alpha=0.04, zorder=1)

    # EMA
    _plot_valid(ax_c, times, ema21, color=EMA21_C, linewidth=1.1,
                alpha=0.85, label="EMA21", zorder=4)
    _plot_valid(ax_c, times, ema50, color=EMA50_C, linewidth=1.1,
                alpha=0.85, label="EMA50", zorder=4)

    # current price line
    ax_c.axhline(cur, color=main_c, linestyle="-", alpha=0.3, linewidth=0.8, zorder=1)
    ax_c.text(1.002, cur, f"{cur:.{dec}f}",
              transform=ax_c.get_yaxis_transform(),
              fontsize=8.5, fontweight="bold", color="white",
              va="center", ha="left",
              bbox=dict(boxstyle="round,pad=0.3", fc=main_c, ec="none"))

    # HIGH / LOW labels
    hi_idx = highs.index(hi)
    lo_idx = lows.index(lo)
    ax_c.annotate(f"H {hi:.{dec}f}",
                  xy=(times[hi_idx], hi), xytext=(0, 10),
                  textcoords="offset points",
                  fontsize=7.5, color=EMA21_C, fontweight="bold", ha="center",
                  arrowprops=dict(arrowstyle="-", color=EMA21_C, lw=0.6))
    ax_c.annotate(f"L {lo:.{dec}f}",
                  xy=(times[lo_idx], lo), xytext=(0, -12),
                  textcoords="offset points",
                  fontsize=7.5, color="#29b6f6", fontweight="bold", ha="center",
                  arrowprops=dict(arrowstyle="-", color="#29b6f6", lw=0.6))

    # Y padding
    pr  = hi - lo
    pad = max(pr * 0.06, cur * 0.001)
    ax_c.set_ylim(lo - pad, hi + pad * 2.5)
    ax_c.legend(loc="upper left", fontsize=7,
                facecolor=BG, edgecolor=GRID_C, labelcolor=TEXT_C,
                framealpha=0.8)

    # ══ PANEL 2: Volume ══
    vol_colors = [UP_C if c >= o else DN_C for c, o in zip(closes, opens)]
    ax_v.bar(times, vols, color=vol_colors, alpha=0.5,
             width=timedelta(seconds=bar_w * 86400 * 0.8))
    ax_v.set_yticks([])
    ax_v.text(1.002, 0.5, "VOL", transform=ax_v.transAxes,
              fontsize=7, color=DIM_C, va="center", ha="left")

    # ══ PANEL 3: RSI ══
    rsi_t = [t for t, v in zip(times, rsi_vals) if v is not None]
    rsi_v = [v for v in rsi_vals if v is not None]
    if rsi_v:
        ax_r.plot(rsi_t, rsi_v, color="#ce93d8", linewidth=1.1, zorder=3)
        ax_r.axhline(70, color=DN_C, linestyle="--", alpha=0.5, linewidth=0.7)
        ax_r.axhline(50, color=DIM_C, linestyle=":",  alpha=0.3, linewidth=0.6)
        ax_r.axhline(30, color=UP_C,  linestyle="--", alpha=0.5, linewidth=0.7)
        ax_r.fill_between(rsi_t, rsi_v, 70,
                          where=[v >= 70 for v in rsi_v],
                          color=DN_C, alpha=0.1, zorder=1)
        ax_r.fill_between(rsi_t, rsi_v, 30,
                          where=[v <= 30 for v in rsi_v],
                          color=UP_C, alpha=0.1, zorder=1)
        ax_r.set_ylim(10, 90)
        ax_r.set_yticks([30, 50, 70])
        rsi_c = UP_C if last_rsi and last_rsi >= 50 else DN_C
        if last_rsi:
            ax_r.text(1.002, last_rsi, f"RSI {last_rsi:.0f}",
                      transform=ax_r.get_yaxis_transform(),
                      fontsize=7.5, fontweight="bold", color="white",
                      va="center", ha="left",
                      bbox=dict(boxstyle="round,pad=0.25", fc=rsi_c, ec="none"))

    # ══ PANEL 4: MACD ══
    macd_t = [t for t, v in zip(times, macd_line) if v is not None]
    macd_v = [v for v in macd_line if v is not None]
    sig_t  = [t for t, v in zip(times, signal_line) if v is not None]
    sig_v  = [v for v in signal_line if v is not None]
    hist_t = [t for t, v in zip(times, histogram) if v is not None]
    hist_v = [v for v in histogram if v is not None]

    if macd_v:
        ax_m.plot(macd_t, macd_v, color=MACD_C, linewidth=1.0, label="MACD", zorder=3)
    if sig_v:
        ax_m.plot(sig_t, sig_v, color=SIG_C, linewidth=0.9, label="Signal", zorder=3)
    if hist_v:
        hist_colors = [UP_C if v >= 0 else DN_C for v in hist_v]
        ax_m.bar(hist_t, hist_v, color=hist_colors, alpha=0.5,
                 width=timedelta(seconds=bar_w * 86400 * 0.8), zorder=2)

    ax_m.axhline(0, color=DIM_C, linewidth=0.6, alpha=0.5)
    ax_m.legend(loc="upper left", fontsize=6.5,
                facecolor=BG, edgecolor=GRID_C, labelcolor=TEXT_C,
                framealpha=0.8)

    if last_macd is not None:
        macd_c = UP_C if last_macd >= 0 else DN_C
        ax_m.text(1.002, last_macd, f"{last_macd:.{max(1,dec-1)}f}",
                  transform=ax_m.get_yaxis_transform(),
                  fontsize=7, fontweight="bold", color="white",
                  va="center", ha="left",
                  bbox=dict(boxstyle="round,pad=0.2", fc=macd_c, ec="none"))
    ax_m.text(1.002, 0.5, "MACD", transform=ax_m.transAxes,
              fontsize=7, color=DIM_C, va="center", ha="left")

    # ══ X axis (Iran time) ══
    ax_m.xaxis.set_major_formatter(mdates.DateFormatter(x_fmt, tz=IRAN_TZ))
    ax_m.xaxis.set_major_locator(mdates.AutoDateLocator(tz=IRAN_TZ))
    plt.setp(ax_m.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=7)
    for a in (ax_c, ax_v, ax_r):
        plt.setp(a.xaxis.get_majorticklabels(), visible=False)

    # ══ Title ══
    sign  = "+" if is_up else ""
    arrow = "▲" if is_up else "▼"
    n_bars = len(closes)
    t1 = (f"{asset.symbol}   {label_en}   "
          f"({n_bars} candles)   |   Iran Time (UTC+3:30)")
    t2 = (f"{arrow} {cur:.{dec}f}   {sign}{chg:.{dec}f}  ({sign}{chgp:.2f}%)   "
          f"H: {hi:.{dec}f}   L: {lo:.{dec}f}")

    rsi_str  = f"RSI: {last_rsi:.0f}" if last_rsi else ""
    macd_str = f"MACD: {last_macd:.{max(1,dec-1)}f}" if last_macd else ""
    t3 = f"EMA21 / EMA50 / BB(20,2)   {rsi_str}   {macd_str}"

    fig.text(0.44, 0.935, t1, ha="center", va="bottom",
             fontsize=10, fontweight="bold", color=TEXT_C)
    fig.text(0.44, 0.908, t2, ha="center", va="bottom",
             fontsize=10, color=main_c)
    fig.text(0.44, 0.885, t3, ha="center", va="bottom",
             fontsize=8, color=DIM_C)

    fig.text(0.87, 0.01, "@ScalpBotPro",
             ha="right", va="bottom", fontsize=6.5, color=GRID_C)

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

    def stats(self): return len(self._data.get("users", {})), self._data.get("total_requests", 0)

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
        except Exception: pass

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
    return f"ScalpBot Pro v3 | users={u} | reqs={r} | {str_ir()}"

@flask_app.route("/health")
def health():
    return jsonify(status="ok", time=str_ir(), version="v3")

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
    tf_labels = {
        "1m":  "⚡ 1M  (scalp)",
        "5m":  "🔥 5M",
        "15m": "⏱ 15M",
        "1h":  "📅 1H",
        "4h":  "📆 4H",
        "1d":  "🗓 Daily",
    }
    rows = []
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
        if not a: continue
        for al in al_list:
            st = "✅" if al.get("done") else "⏳"
            dr = "⬆️" if al["dir"] == "up" else "⬇️"
            lines.append(
                f"{st} {a.emoji} {a.symbol}  {dr}  "
                f"<code>{al['target']:.{a.decimals}f}</code>"
            )
    lines.append("\n<i>بررسی هر ۵ دقیقه</i>")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════
async def _reg(update: Update):
    u = update.effective_user
    cloud.ensure_user(u.id, u.username or "")
    cloud.inc(u.id)

async def _send_chart(sym, tf_key, send_photo_fn, fail_reply_fn):
    live = get_price(sym, force=True)
    if live:
        cloud.push_price(sym, live)
    buf = create_chart(sym, tf_key, live_price=live)
    if not buf:
        await fail_reply_fn(); return
    a = ASSETS[sym]
    _, _, label_en, label_fa, _ = TIMEFRAMES[tf_key]
    live_str = f"{live:.{a.decimals}f}" if live else "N/A"
    caption = (
        f"{a.emoji} <b>{a.symbol}</b>   {label_fa}\n"
        f"💰 Live: <code>{live_str}</code>   ⏱ {str_ir()}\n"
        f"<i>Candles: EMA21/50 | BB(20,2) | RSI(14) | MACD(12,26,9)</i>"
    )
    await send_photo_fn(buf, caption)

# ══════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reg(update)
    await update.effective_message.reply_text(
        "👋 <b>ScalpBot Pro v3</b>\n\n"
        "قیمت لحظه‌ای فارکس و طلا — برای اسکلپرها.\n\n"
        "📊 <b>چارت‌ها:</b> کندل‌استیک + EMA21/50 + BB + RSI + MACD\n"
        "⏱ <b>تایم‌فریم‌ها:</b> 1m | 5m | 15m | 1h | 4h | 1d\n"
        "📉 <b>۲۰۰ کندل آخر</b> در هر تایم‌فریم\n\n"
        "منوی زیر 👇",
        parse_mode=ParseMode.HTML, reply_markup=kb_main()
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
        await msg.edit_text("⚠️ دریافت داده ناموفق.\n/chart XAUUSD 1m",
                            reply_markup=kb_main())

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

    async def edit(text, kb=None):
        kw = {"parse_mode": ParseMode.HTML}
        if kb is not None: kw["reply_markup"] = kb
        await q.edit_message_text(text, **kw)

    if d == "main":
        await edit("🏠 <b>منوی اصلی</b>\n\nیک گزینه انتخاب کنید:", kb_main()); return

    if d == "all":
        await edit("⏳")
        await edit(build_all_prices(), kb_main()); return

    if d == "stats":
        u, r = cloud.stats()
        await edit(
            f"📊 <b>آمار</b>\n\n"
            f"👥 کاربران: <b>{u}</b>\n"
            f"📈 درخواست‌ها: <b>{r}</b>\n\n"
            f"⏱ {str_ir()}",
            kb_main()
        ); return

    if d == "help":
        await edit(
            "❓ <b>راهنما — ScalpBot Pro v3</b>\n\n"
            "/start — منو\n/price — همه قیمت‌ها\n"
            "/chart [sym] [tf] — نمودار\n/alerts — هشدارها\n/cancel — لغو\n\n"
            "<b>تایم‌فریم:</b> 1m | 5m | 15m | 1h | 4h | 1d\n"
            "<b>نماد:</b> XAUUSD | EURUSD | USDJPY | GBPUSD\n\n"
            "📊 <b>اندیکاتورها:</b>\n"
            "• کندل‌استیک (OHLC)\n"
            "• EMA 21 / EMA 50\n"
            "• Bollinger Bands (20, 2)\n"
            "• RSI (14)\n"
            "• MACD (12, 26, 9)\n"
            "• Volume\n\n"
            "• ۲۰۰ کندل آخر هر تایم‌فریم\n"
            "• قیمت از ۳ منبع با fallback\n"
            "• هشدار قیمت (هر ۵ دقیقه)",
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
            f"⚡ 1M = اسکلپ (کندل ۱ دقیقه‌ای)\n"
            f"🔥 5M = کوتاه‌مدت\n"
            f"⏱ 15M / 1H / 4H / Daily",
            kb_tf(sym)
        ); return

    if d.startswith("tf:"):
        parts = d.split(":")
        sym, tf_key = parts[1], parts[2]
        if sym not in ASSETS or tf_key not in TIMEFRAMES: return
        a = ASSETS[sym]
        await edit(f"📈 رسم نمودار {a.symbol}  {TIMEFRAMES[tf_key][2]}…")

        async def send(buf, caption):
            await q.message.reply_photo(
                photo=buf, caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=kb_after_chart(sym),
            )
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
        await edit(build_alerts_text(uid) + "\n\n✅ تریگرشده‌ها پاک شدند.", kb_alerts())
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
                fired = ((al["dir"] == "up" and price >= target) or
                         (al["dir"] == "down" and price <= target))
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
    log.info("ScalpBot Pro v3 warmup done")

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

    def _sig(s, f): cloud.save(force=True); sys.exit(0)
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)
    atexit.register(lambda: cloud.save(force=True))

    log.info("ScalpBot Pro v3 starting...")
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
