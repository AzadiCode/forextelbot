#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScalpBot Pro v6
- چارت کاملاً بازنویسی شده شبیه TradingView
- منبع: Twelve Data (realtime)
- کنترل دسترسی از داخل کد
"""

import os, sys, asyncio, logging, signal, atexit, io, threading, time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional

import requests, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.ticker import FuncFormatter, MaxNLocator
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
# ── ACCESS CONTROL (از داخل کد تنظیم کن) ──
# PUBLIC_MODE = True  → همه می‌توانند استفاده کنند
# PUBLIC_MODE = False → فقط ادمین‌های زیر
# ══════════════════════════════════════════════════════════════════════
PUBLIC_MODE = True
ADMIN_IDS   = {123456789}   # ← ID عددی ادمین‌ها را اینجا بنویس

# ══════════════════════════════════════════════════════════════════════
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
PORT           = int(os.environ.get("PORT", 5000))
JSONBIN_KEY    = os.environ.get("JSONBIN_API_KEY", "")
JSONBIN_BIN    = os.environ.get("JSONBIN_BIN_ID", "")
TWELVEDATA_KEY = os.environ.get("TWELVEDATA_API_KEY", "")

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
def now_ir(): return datetime.now(IRAN_TZ)
def str_ir(fmt="%H:%M:%S"): return now_ir().strftime(fmt)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# ══════════════════════════════════════════════════════════════════════
# ACCESS GUARD
# ══════════════════════════════════════════════════════════════════════
async def _check_access(update: Update) -> bool:
    if PUBLIC_MODE:
        return True
    uid = update.effective_user.id
    if uid in ADMIN_IDS:
        return True
    await update.effective_message.reply_text(
        "🔒 ربات در حالت خصوصی است.\n"
        "فقط ادمین‌ها می‌توانند استفاده کنند."
    )
    return False

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
    td_symbol: str
    decimals: int
    pip_size: float
    sources: list

ASSETS: dict[str, Asset] = {
    "XAUUSD": Asset(
        key="XAUUSD", name="Gold", emoji="🥇", symbol="XAU/USD",
        td_symbol="XAU/USD", decimals=2, pip_size=0.1,
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
        td_symbol="EUR/USD", decimals=5, pip_size=0.0001,
        sources=[
            Source("Swissquote",
                   "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/EUR/USD",
                   lambda d: (float(d[0]["spreadProfilePrices"][0]["ask"]) +
                              float(d[0]["spreadProfilePrices"][0]["bid"])) / 2, 0.8, 1.5),
        ],
    ),
    "USDJPY": Asset(
        key="USDJPY", name="USD/JPY", emoji="💴", symbol="USD/JPY",
        td_symbol="USD/JPY", decimals=3, pip_size=0.01,
        sources=[
            Source("Swissquote",
                   "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/USD/JPY",
                   lambda d: (float(d[0]["spreadProfilePrices"][0]["ask"]) +
                              float(d[0]["spreadProfilePrices"][0]["bid"])) / 2, 100, 200),
        ],
    ),
    "GBPUSD": Asset(
        key="GBPUSD", name="GBP/USD", emoji="💷", symbol="GBP/USD",
        td_symbol="GBP/USD", decimals=5, pip_size=0.0001,
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
# ══════════════════════════════════════════════════════════════════════
# هر تایم‌فریم: (td_interval, bars, label_en, label_fa, x_fmt)
# bars = تعداد کندل واقعی که می‌خوایم نمایش بدیم
TIMEFRAMES = {
    "1m":  ("1min",  200, "1M  | 1m candles",  "۱ دقیقه",  "%H:%M"),
    "5m":  ("5min",  200, "5M  | 5m candles",  "۵ دقیقه",  "%H:%M"),
    "15m": ("15min", 200, "15M | 15m candles", "۱۵ دقیقه", "%m/%d %H:%M"),
    "1h":  ("1h",    200, "1H  | 1h candles",  "۱ ساعت",   "%m/%d"),
    "4h":  ("4h",    200, "4H  | 4h candles",  "۴ ساعت",   "%m/%d"),
    "1d":  ("1day",  200, "1D  | 1d candles",  "روزانه",   "%Y/%m/%d"),
}

# ══════════════════════════════════════════════════════════════════════
# PRICE FETCHER
# ══════════════════════════════════════════════════════════════════════
_price_cache: dict[str, tuple[float, float]] = {}
CACHE_TTL = 12

def _td_price(asset: Asset) -> Optional[float]:
    if not TWELVEDATA_KEY:
        return None
    try:
        r = requests.get(
            "https://api.twelvedata.com/price",
            params={"symbol": asset.td_symbol, "apikey": TWELVEDATA_KEY},
            headers=_HEADERS, timeout=10,
        )
        r.raise_for_status()
        d = r.json()
        if "price" in d:
            p = float(d["price"])
            lo, hi = asset.sources[0].min_val, asset.sources[0].max_val
            if lo < p < hi:
                return round(p, asset.decimals)
    except Exception as e:
        log.warning(f"TD price {asset.key}: {e}")
    return None

def get_price(symbol: str, force: bool = False) -> Optional[float]:
    if symbol not in ASSETS:
        return None
    if not force and symbol in _price_cache:
        p, ts = _price_cache[symbol]
        if time.time() - ts < CACHE_TTL:
            return p
    asset = ASSETS[symbol]
    p = _td_price(asset)
    if p:
        _price_cache[symbol] = (p, time.time())
        log.info(f"price {symbol}={p} [TD]")
        return p
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
            log.warning(f"src {src.name}/{symbol}: {e}")
    return None

# ══════════════════════════════════════════════════════════════════════
# OHLC FETCHER — Twelve Data
# ══════════════════════════════════════════════════════════════════════
def fetch_ohlc(asset: Asset, interval: str, outputsize: int) -> Optional[tuple]:
    if not TWELVEDATA_KEY:
        log.error("TWELVEDATA_API_KEY not set")
        return None
    try:
        r = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol":     asset.td_symbol,
                "interval":   interval,
                "outputsize": outputsize,
                "apikey":     TWELVEDATA_KEY,
                "timezone":   "Asia/Tehran",
                "order":      "ASC",
            },
            headers=_HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()

        if data.get("status") == "error":
            log.error(f"TD error: {data.get('message')}")
            return None

        values = data.get("values", [])
        if not values:
            return None

        times, opens, highs, lows, closes, vols = [], [], [], [], [], []
        for bar in values:
            try:
                dt = datetime.strptime(bar["datetime"], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    dt = datetime.strptime(bar["datetime"], "%Y-%m-%d")
                except Exception:
                    continue
            dt = dt.replace(tzinfo=IRAN_TZ)
            times.append(dt)
            opens.append(float(bar["open"]))
            highs.append(float(bar["high"]))
            lows.append(float(bar["low"]))
            closes.append(float(bar["close"]))
            vols.append(float(bar.get("volume") or 0))

        if len(closes) < 5:
            return None

        log.info(f"TD OHLC: {asset.td_symbol} {interval} → {len(closes)} bars")
        return times, opens, highs, lows, closes, vols

    except Exception as e:
        log.error(f"fetch_ohlc {asset.td_symbol}: {e}")
        return None

# ══════════════════════════════════════════════════════════════════════
# INDICATORS
# ══════════════════════════════════════════════════════════════════════
def _ema(data, period):
    out = [None] * len(data)
    k = 2 / (period + 1)
    valid = [(i, v) for i, v in enumerate(data) if v is not None]
    if len(valid) < period:
        return out
    start_i = valid[0][0]
    seed_vals = [v for _, v in valid[:period]]
    if len(seed_vals) < period:
        return out
    out[valid[period-1][0]] = sum(seed_vals) / period
    for j in range(period, len(valid)):
        i, v = valid[j]
        prev_i = valid[j-1][0]
        if out[prev_i] is not None:
            out[i] = v * k + out[prev_i] * (1 - k)
    return out

def _sma(data, period):
    out = [None] * len(data)
    for i in range(period - 1, len(data)):
        w = [v for v in data[i-period+1:i+1] if v is not None]
        if len(w) == period:
            out[i] = sum(w) / period
    return out

def _bollinger(closes, period=20, mult=2.0):
    mid = _sma(closes, period)
    upper = [None] * len(closes)
    lower = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        if mid[i] is None:
            continue
        w = closes[i-period+1:i+1]
        sd = (sum((x - mid[i])**2 for x in w) / period) ** 0.5
        upper[i] = mid[i] + mult * sd
        lower[i] = mid[i] - mult * sd
    return upper, mid, lower

def _rsi(closes, period=14):
    out = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
        if i >= period:
            ag = sum(gains[-period:]) / period
            al = sum(losses[-period:]) / period
            out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out

def _macd(closes, fast=12, slow=26, sig=9):
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    ml = [f - s if (f is not None and s is not None) else None for f, s in zip(ef, es)]
    sl = _ema(ml, sig)
    hl = [m - s if (m is not None and s is not None) else None for m, s in zip(ml, sl)]
    return ml, sl, hl

# ══════════════════════════════════════════════════════════════════════
# CHART ENGINE v6 — شبیه TradingView
# ══════════════════════════════════════════════════════════════════════
BG      = "#131722"
UP_C    = "#26a69a"
DN_C    = "#ef5350"
GRID_C  = "#1e222d"
TEXT_C  = "#d1d4dc"
DIM_C   = "#787b86"
BB_C    = "#2196f3"
EMA21_C = "#f9a825"
EMA50_C = "#9c27b0"
MACD_C  = "#26a69a"
SIG_C   = "#ef5350"


def _plot_valid(ax, xs, data, **kw):
    pts = [(x, v) for x, v in zip(xs, data) if v is not None]
    if len(pts) > 1:
        xx, vv = zip(*pts)
        ax.plot(xx, vv, **kw)


def _draw_candles(ax, x_idx, opens, highs, lows, closes, bar_w, price_range):
    """
    رسم کندل دقیق و بدون دستکاری دیتا:
    - x_idx ایندکس صحیح کندل‌ها (نه زمان واقعی) — برای حذف گپ بصری
      ناشی از بسته‌بودن بازار (آخر هفته/سشن‌ها)، دقیقاً مثل TradingView
    - هیچ clamp/cap‌ای روی high/low/body کندل آخر اعمال نمی‌شود؛
      shadow و body همیشه دقیقاً برابر دیتای واقعی رسم می‌شوند
    - برای دوجی واقعی (open == close) فقط یک خط نازک افقی برای
      دیده‌شدن رسم می‌شود، بدون فیک‌کردن اندازه‌ی بدنه
    """
    n = len(x_idx)
    if n == 0:
        return

    # حداقل ارتفاع صرفاً برای جلوگیری از محو شدن کامل دوجی در رندر —
    # این مقدار به نسبت رِیج واقعی قیمت چارت است، نه به دیتای کندل‌ها
    min_visible = price_range * 0.0006 if price_range > 0 else 0.0

    for i in range(n):
        x  = x_idx[i]
        o  = opens[i]
        h  = highs[i]
        l  = lows[i]
        c  = closes[i]
        up = c >= o
        col = UP_C if up else DN_C

        # wick — از کمترین تا بیشترین واقعی، بدون هیچ تغییری
        ax.plot(
            [x, x], [l, h],
            color=col, linewidth=1.0, zorder=2, solid_capstyle="butt"
        )

        body_size   = abs(c - o)
        body_bottom = min(o, c)

        if body_size < min_visible:
            # دوجی واقعی: فقط یک خط نازک افقی، بدون فیک‌کردن بدنه
            ax.plot(
                [x - bar_w / 2, x + bar_w / 2], [c, c],
                color=col, linewidth=1.3, zorder=3, solid_capstyle="butt"
            )
        else:
            ax.add_patch(mpatches.Rectangle(
                (x - bar_w / 2, body_bottom),
                bar_w,
                body_size,
                linewidth=0,
                facecolor=col,
                zorder=3,
            ))


def create_chart(
    symbol: str,
    tf_key: str = "1m",
    live_price: Optional[float] = None,
) -> Optional[io.BytesIO]:

    if symbol not in ASSETS or tf_key not in TIMEFRAMES:
        return None

    asset = ASSETS[symbol]
    td_interval, n_bars, label_en, label_fa, x_fmt = TIMEFRAMES[tf_key]

    # ── fetch ──
    ohlc = fetch_ohlc(asset, td_interval, outputsize=n_bars)
    if not ohlc:
        return None
    times, opens, highs, lows, closes, vols = ohlc

    # ── live price override روی آخرین کندل ──
    if live_price is not None:
        closes[-1] = live_price
        highs[-1]  = max(highs[-1], live_price)
        lows[-1]   = min(lows[-1],  live_price)

    n = len(times)
    dec = asset.decimals

    # ── محور x ایندکس‌محور (مثل TradingView): بدون گپ بصری برای
    # بسته‌بودن بازار/آخر هفته/سشن‌ها — فاصله بین کندل‌ها همیشه یکسان است
    x_idx = list(range(n))
    bar_w = 0.6  # 60% پر، 40% فاصله بین کندل‌ها — مثل TradingView

    # ── اندیکاتورها ──
    ema21              = _ema(closes, 21)
    ema50              = _ema(closes, 50)
    bb_up, bb_mid, bb_lo = _bollinger(closes, 20)
    rsi_v              = _rsi(closes, 14)
    macd_l, sig_l, hist_l = _macd(closes)

    cur   = closes[-1]
    hi    = max(highs)
    lo    = min(lows)
    chg   = cur - closes[0]
    chgp  = chg / closes[0] * 100 if closes[0] else 0
    up    = chg >= 0
    mc    = UP_C if up else DN_C
    sign  = "+" if up else ""

    last_rsi  = next((v for v in reversed(rsi_v)  if v is not None), None)
    last_macd = next((v for v in reversed(macd_l) if v is not None), None)

    # ══ Figure — نسبت‌های شبیه TradingView ══
    plt.rcParams.update({
        "font.family":       "DejaVu Sans",
        "font.size":         8,
        "axes.unicode_minus": False,
    })

    fig = plt.figure(figsize=(16, 9), facecolor=BG)
    # نسبت‌ها: چارت اصلی بزرگ، volume کوچک، RSI و MACD متوسط
    gs = fig.add_gridspec(
        4, 1,
        height_ratios=[5.5, 0.8, 1.5, 1.5],
        hspace=0,
        left=0.02, right=0.88,
        top=0.89, bottom=0.06,
    )
    ax_c = fig.add_subplot(gs[0])
    ax_v = fig.add_subplot(gs[1], sharex=ax_c)
    ax_r = fig.add_subplot(gs[2], sharex=ax_c)
    ax_m = fig.add_subplot(gs[3], sharex=ax_c)

    for ax in (ax_c, ax_v, ax_r, ax_m):
        ax.set_facecolor(BG)
        ax.tick_params(colors=DIM_C, labelsize=7, length=2, width=0.5)
        ax.yaxis.set_label_position("right")
        ax.yaxis.tick_right()
        for sp in ax.spines.values():
            sp.set_color(GRID_C)
            sp.set_linewidth(0.5)
        ax.grid(True, color=GRID_C, linewidth=0.3, alpha=0.8, zorder=0)

    # ── CANDLES ──
    _draw_candles(ax_c, x_idx, opens, highs, lows, closes, bar_w, hi - lo)

    # ── Bollinger Bands ──
    _plot_valid(ax_c, x_idx, bb_up, color=BB_C, lw=0.8, alpha=0.6, ls="--", label="BB(20)")
    _plot_valid(ax_c, x_idx, bb_lo, color=BB_C, lw=0.8, alpha=0.6, ls="--")
    _plot_valid(ax_c, x_idx, bb_mid, color=BB_C, lw=0.5, alpha=0.4, ls=":")

    bb_pts = [(x, u, l) for x, u, l in zip(x_idx, bb_up, bb_lo)
              if u is not None and l is not None]
    if bb_pts:
        bx, bu, bl = zip(*bb_pts)
        ax_c.fill_between(bx, bu, bl, color=BB_C, alpha=0.03, zorder=1)

    # ── EMA ──
    _plot_valid(ax_c, x_idx, ema21, color=EMA21_C, lw=1.0, label="EMA21", zorder=4)
    _plot_valid(ax_c, x_idx, ema50, color=EMA50_C, lw=1.0, label="EMA50", zorder=4)

    # ── قیمت جاری ──
    ax_c.axhline(cur, color=mc, ls="-", lw=0.6, alpha=0.4, zorder=1)
    ax_c.text(
        1.002, cur, f" {cur:.{dec}f}",
        transform=ax_c.get_yaxis_transform(),
        fontsize=8.5, fontweight="bold", color="white", va="center", ha="left",
        bbox=dict(boxstyle="round,pad=0.25", fc=mc, ec="none", alpha=0.95),
    )

    # ── H / L label ──
    hi_i = int(np.argmax(highs))
    lo_i = int(np.argmin(lows))
    ax_c.annotate(
        f"H {hi:.{dec}f}",
        xy=(hi_i, hi), xytext=(0, 8), textcoords="offset points",
        fontsize=7, color=EMA21_C, fontweight="bold", ha="center",
        arrowprops=dict(arrowstyle="-", color=EMA21_C, lw=0.5),
    )
    ax_c.annotate(
        f"L {lo:.{dec}f}",
        xy=(lo_i, lo), xytext=(0, -10), textcoords="offset points",
        fontsize=7, color="#29b6f6", fontweight="bold", ha="center",
        arrowprops=dict(arrowstyle="-", color="#29b6f6", lw=0.5),
    )

    # y range
    pr  = hi - lo
    pad = max(pr * 0.08, cur * 0.001)
    ax_c.set_ylim(lo - pad, hi + pad * 3)
    ax_c.legend(
        loc="upper left", fontsize=7,
        facecolor=BG, edgecolor=GRID_C, labelcolor=TEXT_C, framealpha=0.9,
    )

    # ── VOLUME ──
    vcols = [UP_C if c >= o else DN_C for c, o in zip(closes, opens)]
    ax_v.bar(x_idx, vols, color=vcols, alpha=0.45, width=bar_w * 0.85, zorder=2)
    ax_v.set_yticks([])
    ax_v.text(
        1.002, 0.5, "VOL", transform=ax_v.transAxes,
        fontsize=6.5, color=DIM_C, va="center", ha="left",
    )

    # ── RSI ──
    rsi_pts = [(x, v) for x, v in zip(x_idx, rsi_v) if v is not None]
    if rsi_pts:
        rx, rv = zip(*rsi_pts)
        ax_r.plot(rx, rv, color="#ce93d8", lw=1.0, zorder=3)
        ax_r.axhline(70, color=DN_C, ls="--", lw=0.6, alpha=0.5)
        ax_r.axhline(50, color=DIM_C, ls=":", lw=0.5, alpha=0.4)
        ax_r.axhline(30, color=UP_C, ls="--", lw=0.6, alpha=0.5)
        ax_r.fill_between(rx, rv, 70, where=[v >= 70 for v in rv], color=DN_C, alpha=0.08)
        ax_r.fill_between(rx, rv, 30, where=[v <= 30 for v in rv], color=UP_C, alpha=0.08)
        ax_r.set_ylim(0, 100)
        ax_r.set_yticks([30, 50, 70])
    if last_rsi is not None:
        rc = UP_C if last_rsi >= 50 else DN_C
        ax_r.text(
            1.002, last_rsi, f"RSI {last_rsi:.0f}",
            transform=ax_r.get_yaxis_transform(),
            fontsize=7.5, fontweight="bold", color="white", va="center", ha="left",
            bbox=dict(boxstyle="round,pad=0.22", fc=rc, ec="none"),
        )

    # ── MACD ──
    mpts = [(x, v) for x, v in zip(x_idx, macd_l) if v is not None]
    spts = [(x, v) for x, v in zip(x_idx, sig_l)  if v is not None]
    hpts = [(x, v) for x, v in zip(x_idx, hist_l) if v is not None]
    if mpts:
        mx, mv = zip(*mpts)
        ax_m.plot(mx, mv, color=MACD_C, lw=1.0, label="MACD", zorder=3)
    if spts:
        sx, sv = zip(*spts)
        ax_m.plot(sx, sv, color=SIG_C, lw=0.9, label="Sig", zorder=3)
    if hpts:
        hx, hv = zip(*hpts)
        hc = [UP_C if v >= 0 else DN_C for v in hv]
        ax_m.bar(hx, hv, color=hc, alpha=0.5, width=bar_w * 0.85, zorder=2)
    ax_m.axhline(0, color=DIM_C, lw=0.5, alpha=0.6)
    ax_m.legend(
        loc="upper left", fontsize=6.5,
        facecolor=BG, edgecolor=GRID_C, labelcolor=TEXT_C, framealpha=0.9,
    )
    if last_macd is not None:
        mcc = UP_C if last_macd >= 0 else DN_C
        ax_m.text(
            1.002, last_macd, f"{last_macd:.{max(1, dec-1)}f}",
            transform=ax_m.get_yaxis_transform(),
            fontsize=7, fontweight="bold", color="white", va="center", ha="left",
            bbox=dict(boxstyle="round,pad=0.2", fc=mcc, ec="none"),
        )
    ax_m.text(
        1.002, 0.5, "MACD", transform=ax_m.transAxes,
        fontsize=6.5, color=DIM_C, va="center", ha="left",
    )

    # ── X axis: ایندکس‌محور با لیبل زمان واقعی (بدون گپ بصری) ──
    def _x_time_fmt(val, pos=None):
        i = int(round(val))
        if 0 <= i < len(times):
            return times[i].strftime(x_fmt)
        return ""
    ax_m.xaxis.set_major_locator(MaxNLocator(nbins=8, integer=True, min_n_ticks=4))
    ax_m.xaxis.set_major_formatter(FuncFormatter(_x_time_fmt))
    plt.setp(ax_m.xaxis.get_majorticklabels(), rotation=25, ha="right", fontsize=6.5)
    for ax in (ax_c, ax_v, ax_r):
        plt.setp(ax.xaxis.get_majorticklabels(), visible=False)

    # ── X limits — فقط محدوده داده، بدون فاصله‌ی زمانی واقعی (مثل TradingView) ──
    margin = 1.5
    ax_c.set_xlim(-margin, (n - 1) + margin)

    # ── Title ──
    arrow = "▲" if up else "▼"
    rsi_s  = f"RSI: {last_rsi:.0f}"  if last_rsi  is not None else ""
    macd_s = f"MACD: {last_macd:.{max(1,dec-1)}f}" if last_macd is not None else ""

    fig.text(0.45, 0.936,
             f"{asset.symbol}   {label_en}   ({n} candles)   |   Iran Time (UTC+3:30)",
             ha="center", fontsize=10, fontweight="bold", color=TEXT_C)
    fig.text(0.45, 0.910,
             f"{arrow} {cur:.{dec}f}   {sign}{chg:.{dec}f}  ({sign}{chgp:.2f}%)   "
             f"H: {hi:.{dec}f}   L: {lo:.{dec}f}",
             ha="center", fontsize=10, color=mc)
    fig.text(0.45, 0.888,
             f"EMA21 / EMA50 / BB(20,2)   {rsi_s}   {macd_s}",
             ha="center", fontsize=8, color=DIM_C)
    fig.text(0.88, 0.01, "@ScalpBotPro", ha="right", fontsize=6, color=GRID_C)

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

    def ensure_user(self, uid, username=""):
        key = str(uid)
        with self._lock:
            if key not in self._data["users"]:
                self._data["users"][key] = {
                    "username": username, "joined": now_ir().isoformat(), "requests": 0,
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
            (self._data.setdefault("alerts", {})
                       .setdefault(key, {})
                       .setdefault(sym, [])
                       .append({"dir": direction, "target": target,
                                "at": now_ir().isoformat(), "done": False}))
            self._dirty = True

    def get_alerts(self, uid):
        return dict(self._data.get("alerts", {}).get(str(uid), {}))

    def mark_done(self, uid, sym, idx):
        try:
            with self._lock:
                self._data["alerts"][str(uid)][sym][idx]["done"] = True
                self._dirty = True
        except Exception:
            pass

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
    mode = "public" if PUBLIC_MODE else "private"
    return f"ScalpBot Pro v6 | {mode} | users={u} | reqs={r} | {str_ir()}"

@flask_app.route("/health")
def health():
    return jsonify(status="ok", time=str_ir(), version="v6")

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
        "1m":  "⚡ 1M (scalp)",
        "5m":  "🔥 5M",
        "15m": "⏱ 15M",
        "1h":  "📅 1H",
        "4h":  "📆 4H",
        "1d":  "🗓 Daily",
    }
    rows, row = [], []
    for k, lbl in labels.items():
        row.append(InlineKeyboardButton(lbl, callback_data=f"tf:{sym}:{k}"))
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
        prev  = hist[-2]["p"]
        diff  = price - prev
        pips  = round(diff / a.pip_size)
        sign  = "+" if diff >= 0 else ""
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
            lines.append(
                f"{st} {a.emoji} {a.symbol}  {dr}  "
                f"<code>{al['target']:.{a.decimals}f}</code>"
            )
    lines.append("\n<i>بررسی هر ۵ دقیقه</i>")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════
async def _reg(update):
    u = update.effective_user
    cloud.ensure_user(u.id, u.username or "")
    cloud.inc(u.id)

async def _send_chart(sym, tf_key, chat_id, reply_to_msg_id, bot, fail_fn):
    _, _, label_en, label_fa, _ = TIMEFRAMES[tf_key]
    loading = await bot.send_message(
        chat_id=chat_id,
        text=f"📈 در حال رسم نمودار {ASSETS[sym].symbol} ({label_fa})…",
        reply_to_message_id=reply_to_msg_id,
    )
    live = get_price(sym, force=True)
    if live:
        cloud.push_price(sym, live)

    buf = create_chart(sym, tf_key, live_price=live)
    if not buf:
        await loading.edit_text(fail_fn(), reply_markup=kb_tf(sym))
        return

    a = ASSETS[sym]
    live_str = f"{live:.{a.decimals}f}" if live else "N/A"
    caption = (
        f"{a.emoji} <b>{a.symbol}</b>   {label_fa}\n"
        f"💰 Live: <code>{live_str}</code>   ⏱ {str_ir()}\n"
        f"<i>EMA21/50 | BB(20,2) | RSI(14) | MACD(12,26,9)</i>"
    )
    await bot.send_photo(
        chat_id=chat_id, photo=buf, caption=caption,
        parse_mode=ParseMode.HTML, reply_markup=kb_after_chart(sym),
    )
    try:
        await loading.delete()
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _check_access(update): return
    await _reg(update)
    mode_txt = "🌐 حالت عمومی" if PUBLIC_MODE else "🔒 حالت خصوصی (ادمین)"
    await update.effective_message.reply_text(
        f"👋 <b>ScalpBot Pro v6</b>  |  {mode_txt}\n\n"
        "قیمت realtime فارکس و طلا — ویژه اسکلپرها\n"
        "منبع: <b>Twelve Data</b>\n\n"
        "📊 کندل | EMA21/50 | BB | RSI | MACD | Volume\n"
        "⏱ TF: 1m | 5m | 15m | 1h | 4h | 1d\n\n"
        "👇 منو:",
        parse_mode=ParseMode.HTML, reply_markup=kb_main(),
    )

async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _check_access(update): return
    await _reg(update)
    msg = await update.effective_message.reply_text("⏳")
    await msg.edit_text(build_all_prices(), parse_mode=ParseMode.HTML, reply_markup=kb_main())

async def cmd_chart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _check_access(update): return
    await _reg(update)
    args = ctx.args
    sym = args[0].upper() if args else "XAUUSD"
    if sym not in ASSETS: sym = "XAUUSD"
    tf  = args[1].lower() if len(args) > 1 else "1m"
    if tf not in TIMEFRAMES: tf = "1m"
    msg = update.effective_message
    await _send_chart(
        sym, tf, chat_id=msg.chat_id,
        reply_to_msg_id=msg.message_id, bot=ctx.bot,
        fail_fn=lambda: f"⚠️ دریافت داده ناموفق.\n/chart {sym} {tf}",
    )

async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _check_access(update): return
    await _reg(update)
    await update.effective_message.reply_text(
        build_alerts_text(update.effective_user.id),
        parse_mode=ParseMode.HTML, reply_markup=kb_alerts(),
    )

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _check_access(update): return
    ctx.user_data.pop("pending_alert", None)
    await update.effective_message.reply_text("❌ لغو شد.", reply_markup=kb_main())

# ══════════════════════════════════════════════════════════════════════
# CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════════
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await _check_access(update): return

    d   = q.data
    uid = update.effective_user.id
    cloud.inc(uid)

    async def edit(text, kb=None):
        try:
            kw = {"parse_mode": ParseMode.HTML}
            if kb: kw["reply_markup"] = kb
            await q.edit_message_text(text, **kw)
        except Exception as e:
            log.warning(f"edit: {e}")
            kw2 = {"chat_id": q.message.chat_id, "text": text, "parse_mode": ParseMode.HTML}
            if kb: kw2["reply_markup"] = kb
            await ctx.bot.send_message(**kw2)

    if d == "main":
        await edit("🏠 <b>منوی اصلی</b>\n\nیک گزینه انتخاب کنید:", kb_main()); return

    if d == "all":
        await edit("⏳ در حال دریافت قیمت‌ها…")
        await edit(build_all_prices(), kb_main()); return

    if d == "stats":
        u, r = cloud.stats()
        await edit(
            f"📊 <b>آمار</b>\n\n👥 کاربران: <b>{u}</b>\n"
            f"📈 درخواست‌ها: <b>{r}</b>\n\n⏱ {str_ir()}",
            kb_main()
        ); return

    if d == "help":
        await edit(
            "❓ <b>راهنما — ScalpBot Pro v6</b>\n\n"
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
        body = _price_line(sym, p) if p else f"{a.emoji} <b>{a.symbol}</b>\n⚠️ خطا\n"
        if p: cloud.push_price(sym, p)
        await edit(f"{body}\n⏱ {str_ir()}\n\nانتخاب کنید:", kb_asset(sym)); return

    if d.startswith("px:"):
        sym = d[3:]
        if sym not in ASSETS: return
        a = ASSETS[sym]
        p = get_price(sym, force=True)
        body = _price_line(sym, p) if p else f"{a.emoji} <b>{a.symbol}</b>\n⚠️ خطا\n"
        if p: cloud.push_price(sym, p)
        await edit(f"{body}\n⏱ {str_ir()}\n\nانتخاب کنید:", kb_asset(sym)); return

    if d == "chart_menu":
        await edit("📈 <b>انتخاب نمودار</b>\n\nدارایی را انتخاب کنید:", kb_chart_menu()); return

    if d.startswith("chart:") and d.count(":") == 1:
        sym = d[6:]
        if sym not in ASSETS: return
        a = ASSETS[sym]
        await edit(
            f"📈 <b>تایم‌فریم — {a.symbol}</b>\n\n"
            "⚡ 1M = اسکلپ\n🔥 5M / ⏱ 15M / 📅 1H / 📆 4H / 🗓 Daily",
            kb_tf(sym)
        ); return

    if d.startswith("tf:"):
        parts = d.split(":")
        if len(parts) != 3: return
        sym, tf_key = parts[1], parts[2]
        if sym not in ASSETS or tf_key not in TIMEFRAMES: return

        _, _, label_en, label_fa, _ = TIMEFRAMES[tf_key]
        try:
            await q.edit_message_text(
                f"📈 رسم نمودار {ASSETS[sym].symbol}  {label_en}…\n⏳"
            )
        except Exception:
            pass

        live = get_price(sym, force=True)
        if live: cloud.push_price(sym, live)

        buf = create_chart(sym, tf_key, live_price=live)
        if not buf:
            try:
                await q.edit_message_text(
                    f"⚠️ دریافت داده ناموفق برای {ASSETS[sym].symbol}.",
                    reply_markup=kb_tf(sym)
                )
            except Exception:
                await ctx.bot.send_message(
                    chat_id=q.message.chat_id,
                    text=f"⚠️ خطا: {ASSETS[sym].symbol}",
                    reply_markup=kb_tf(sym)
                )
            return

        a = ASSETS[sym]
        live_str = f"{live:.{a.decimals}f}" if live else "N/A"
        caption = (
            f"{a.emoji} <b>{a.symbol}</b>   {label_fa}\n"
            f"💰 Live: <code>{live_str}</code>   ⏱ {str_ir()}\n"
            f"<i>EMA21/50 | BB(20,2) | RSI(14) | MACD(12,26,9)</i>"
        )
        await ctx.bot.send_photo(
            chat_id=q.message.chat_id, photo=buf, caption=caption,
            parse_mode=ParseMode.HTML, reply_markup=kb_after_chart(sym),
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
        dir_fa  = "صعودی ⬆️" if direction == "up" else "نزولی ⬇️"
        hint    = f"قیمت فعلی: <code>{p:.{a.decimals}f}</code>\n\n" if p else ""
        example = "3350" if sym == "XAUUSD" else ("155.00" if sym == "USDJPY" else "1.0850")
        await edit(
            f"🔔 <b>هشدار {dir_fa} — {a.symbol}</b>\n\n"
            f"{hint}قیمت هدف را ارسال کنید:\n"
            f"مثال: <code>{example}</code>\n\n❌ /cancel برای لغو"
        ); return

    if d == "my_alerts":
        await edit(build_alerts_text(uid), kb_alerts()); return

    if d == "alerts_clean":
        cloud.clear_done(uid)
        await edit(build_alerts_text(uid) + "\n\n✅ پاک شد.", kb_alerts()); return

# ══════════════════════════════════════════════════════════════════════
# MESSAGE HANDLER
# ══════════════════════════════════════════════════════════════════════
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _check_access(update): return
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
                fired = (
                    (al["dir"] == "up"   and price >= al["target"]) or
                    (al["dir"] == "down" and price <= al["target"])
                )
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
                            f"🎯 هدف: <code>{al['target']:.{a.decimals}f}</code>\n\n"
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
        log.critical("⚠️ TWELVEDATA_API_KEY not set! Charts will fail.")
    mode = "PUBLIC" if PUBLIC_MODE else f"PRIVATE (admins: {ADMIN_IDS})"
    log.info(f"Access mode: {mode}")
    asyncio.create_task(bg_loop(app))
    for sym in ASSETS:
        p = get_price(sym)
        if p: cloud.push_price(sym, p)
    log.info("ScalpBot Pro v6 ready ✓")

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

    log.info("ScalpBot Pro v6 starting…")
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
