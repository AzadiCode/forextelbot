#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GramSaz WebApp Edition — پلتفرم ربات‌ساز با رابط وب
════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import os, sys, json, time, hmac, hashlib, asyncio, logging, threading, copy, secrets, re
import random
from datetime import datetime, timedelta, timezone
from typing import Optional
from functools import wraps
import urllib.parse

# ── Flask ──
from flask import Flask, request, jsonify, g

# ── python-telegram-bot ──
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, WebAppInfo, BotCommand
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters,
)

# ── MongoDB ──
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError


# ══════════════════════════════════════════════════════════════════════
# لاگ‌گذاری
# ══════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO, stream=sys.stdout,
)
log = logging.getLogger("GramSazWA")


# ══════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════
PLATFORM_BOT_TOKEN = os.getenv("PLATFORM_BOT_TOKEN", "8710240249:AAH3e0apc50IvtkfH1F98D6DWahu-TRUWFw").strip()
MONGODB_URI        = os.getenv("MONGODB_URI", "").strip()
WEBAPP_URL         = os.getenv("WEBAPP_URL", "https://botsazgram.onrender.com").strip().rstrip("/")
WEBHOOK_URL        = os.getenv("WEBHOOK_URL", "").strip().rstrip("/")
API_HOST           = os.getenv("API_HOST", "0.0.0.0")
API_PORT           = int(os.getenv("PORT", os.getenv("API_PORT", "8000")))

ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "605611554").replace(" ", "").split(",")
    if x.strip().lstrip("-").isdigit()
}

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))

if not PLATFORM_BOT_TOKEN:
    log.critical("PLATFORM_BOT_TOKEN تنظیم نشده!")
    sys.exit(1)

if not WEBAPP_URL:
    log.warning("WEBAPP_URL تنظیم نشده — دکمه WebApp کار نخواهد کرد")


# ══════════════════════════════════════════════════════════════════════
# توابع کمکی
# ══════════════════════════════════════════════════════════════════════
def now_ir() -> datetime:
    return datetime.now(IRAN_TZ)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def verify_telegram_init_data(init_data: str, bot_token: str) -> Optional[dict]:
    try:
        parsed_pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
        parsed = {k: v for k, v in parsed_pairs}
        
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            log.warning("❌ Hash در initData یافت نشد")
            return None
            
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(expected_hash, received_hash):
            log.warning("❌ هش‌ها مطابقت ندارند!")
            return None
            
        user_str = parsed.get("user", "{}")
        return json.loads(user_str)
        
    except Exception as e:
        log.error(f"❌ خطا در پردازش initData: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
# Storage — MongoDB sync (pymongo) + In-Memory Fallback
# ══════════════════════════════════════════════════════════════════════
class Storage:
    def __init__(self):
        self._is_memory = False
        if MONGODB_URI:
            try:
                self._client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=8000)
                db = self._client.get_default_database()
                self._users = db["users"]
                self._bots  = db["bots"]
                self._users.create_index("uid", unique=True)
                self._bots.create_index("bot_id", unique=True)
                self._bots.create_index("owner_uid")
                log.info("✅ MongoDB متصل شد")
            except Exception as e:
                log.warning(f"⚠️ خطا در اتصال به MongoDB: {e}. از حافظه موقت استفاده می‌شود.")
                self._use_memory()
        else:
            log.warning("⚠️ MONGODB_URI تنظیم نشده است! از حافظه موقت استفاده می‌شود.")
            self._use_memory()

    def _use_memory(self):
        self._is_memory = True
        self._users = {}  # uid -> user_dict
        self._bots = {}   # bot_id -> bot_dict

    def _find_user_by(self, query: dict) -> Optional[dict]:
        """جستجوی کاربر با query در حالت Memory یا MongoDB"""
        if self._is_memory:
            for user in self._users.values():
                match = all(user.get(k) == v for k, v in query.items())
                if match:
                    return user.copy()
            return None
        else:
            doc = self._users.find_one(query)
            if doc:
                doc = dict(doc)
                doc.pop("_id", None)
            return doc

    # ── کاربران ──────────────────────────────────────────────────────
    def ensure_user(self, uid: int, username: str = "", full_name: str = "") -> dict:
        if self._is_memory:
            if uid in self._users:
                doc = self._users[uid]
                if username and doc.get("username") != username:
                    doc["username"] = username
                if full_name and doc.get("full_name") != full_name:
                    doc["full_name"] = full_name
                return copy.deepcopy(doc)
            referral_code = secrets.token_urlsafe(8)
            new_user = {
                "uid": uid,
                "username": username,
                "full_name": full_name,
                "is_admin": uid in ADMIN_IDS,
                "is_banned": False,
                "created_at": now_ir().isoformat(),
                "bots_created": [],
                "coins": 0,
                "referral_code": referral_code,
                "referred_by": None,
                "referral_count": 0,
            }
            self._users[uid] = new_user
            return new_user
        else:
            doc = self._users.find_one({"uid": uid})
            if doc:
                update = {}
                if username and doc.get("username") != username:
                    update["username"] = username
                if full_name and doc.get("full_name") != full_name:
                    update["full_name"] = full_name
                if "coins" not in doc:
                    update["coins"] = 0
                if "referral_code" not in doc:
                    update["referral_code"] = secrets.token_urlsafe(8)
                if "referred_by" not in doc:
                    update["referred_by"] = None
                if "referral_count" not in doc:
                    update["referral_count"] = 0
                if update:
                    self._users.update_one({"uid": uid}, {"$set": update})
                    doc.update(update)
                doc.pop("_id", None)
                return doc
            referral_code = secrets.token_urlsafe(8)
            new_user = {
                "uid": uid,
                "username": username,
                "full_name": full_name,
                "is_admin": uid in ADMIN_IDS,
                "is_banned": False,
                "created_at": now_ir().isoformat(),
                "bots_created": [],
                "coins": 0,
                "referral_code": referral_code,
                "referred_by": None,
                "referral_count": 0,
            }
            try:
                self._users.insert_one(new_user)
            except DuplicateKeyError:
                return self.get_user(uid)
            new_user.pop("_id", None)
            return new_user

    def get_user(self, uid: int) -> Optional[dict]:
        if self._is_memory:
            user = self._users.get(uid)
            return copy.deepcopy(user) if user else None
        doc = self._users.find_one({"uid": uid})
        if doc:
            doc.pop("_id", None)
        return doc

    def get_all_users(self) -> list:
        if self._is_memory:
            return [copy.deepcopy(u) for u in self._users.values()]
        return list(self._users.find({}, {"_id": 0}))

    # ── ربات‌ها ──────────────────────────────────────────────────────
    @staticmethod
    def _bot_defaults(b: dict) -> dict:
        b.setdefault("is_active", True)
        b.setdefault("bot_username", "")
        b.setdefault("commands", {
            "start": {
                "response": "👋 سلام! به ربات ما خوش آمدید.",
                "buttons": None,
                "photo": None,
                "callbacks": {},
            }
        })
        b.setdefault("force_join_channel", None)
        b.setdefault("bot_users", [])
        # ── نسل جدید ──
        b.setdefault("welcome_message", "")   # پیام خوش‌آمد برای عضو جدید
        b.setdefault("unknown_reply", "")      # پاسخ خودکار به ورودی ناشناخته
        b.setdefault("auto_menu", True)        # همگام‌سازی منوی تلگرام
        b.setdefault("rules", [])              # موتور پاسخ هوشمند (کلیدواژه)
        b.setdefault("bot_user_info", {})      # کتابخانهٔ کاربران: uid→{name,username,joined}
        b.setdefault("stats", {
            "messages_received": 0,
            "messages_sent": 0,
            "unique_users": 0,
            "broadcasts_sent": 0,
        })
        b.setdefault("daily_stats", {})
        return b

    def create_bot(self, owner_uid: int, bot_token: str, bot_username: str) -> dict:
        bot_id = hashlib.md5(f"{owner_uid}{bot_token}{time.time()}".encode()).hexdigest()[:12]
        bot_rec = {
            "bot_id": bot_id,
            "owner_uid": owner_uid,
            "bot_token": bot_token,
            "bot_username": bot_username,
            "created_at": now_ir().isoformat(),
            "last_activity": None,
        }
        self._bot_defaults(bot_rec)
        
        if self._is_memory:
            self._bots[bot_id] = copy.deepcopy(bot_rec)
            if owner_uid in self._users:
                if bot_id not in self._users[owner_uid].get("bots_created", []):
                    self._users[owner_uid]["bots_created"].append(bot_id)
        else:
            self._bots.insert_one(bot_rec)
            self._users.update_one(
                {"uid": owner_uid},
                {"$addToSet": {"bots_created": bot_id}}
            )
        bot_rec.pop("_id", None)
        return bot_rec

    def get_bot(self, bot_id: str) -> Optional[dict]:
        if self._is_memory:
            bot = self._bots.get(bot_id)
            return copy.deepcopy(bot) if bot else None
        doc = self._bots.find_one({"bot_id": bot_id})
        if doc:
            doc.pop("_id", None)
        return doc

    def get_user_bots(self, uid: int) -> list:
        if self._is_memory:
            return [copy.deepcopy(b) for b in self._bots.values() if b.get("owner_uid") == uid]
        return list(self._bots.find({"owner_uid": uid}, {"_id": 0}))

    def get_all_bots(self) -> list:
        if self._is_memory:
            return [copy.deepcopy(b) for b in self._bots.values()]
        return list(self._bots.find({}, {"_id": 0}))

    def delete_bot(self, bot_id: str, owner_uid: int) -> bool:
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot and bot.get("owner_uid") == owner_uid:
                del self._bots[bot_id]
                if owner_uid in self._users and bot_id in self._users[owner_uid].get("bots_created", []):
                    self._users[owner_uid]["bots_created"].remove(bot_id)
                return True
            return False
            
        res = self._bots.delete_one({"bot_id": bot_id, "owner_uid": owner_uid})
        if res.deleted_count:
            self._users.update_one(
                {"uid": owner_uid},
                {"$pull": {"bots_created": bot_id}}
            )
            return True
        return False

    def set_bot_active(self, bot_id: str, active: bool):
        if self._is_memory:
            if bot_id in self._bots:
                self._bots[bot_id]["is_active"] = active
        else:
            self._bots.update_one({"bot_id": bot_id}, {"$set": {"is_active": active}})

    def update_bot_username(self, bot_id: str, username: str):
        if self._is_memory:
            if bot_id in self._bots:
                self._bots[bot_id]["bot_username"] = username
        else:
            self._bots.update_one({"bot_id": bot_id}, {"$set": {"bot_username": username}})

    def set_command(self, bot_id: str, cmd_name: str, response: str,
                    buttons=None, photo=None, media=None, description: str = ""):
        """ذخیره دستور با پشتیبانی از media جدید و photo قدیمی + توضیح"""

        # اگر photo قدیمی ارسال شد و media نبود، تبدیل کنیم
        if photo and not media:
            media = {"type": "photo", "url": photo}
        elif media and not photo:
            # media جدید
            pass
        elif media and photo:
            # هر دو ارسال شده، media اولویت دارد
            pass

        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot:
                if "commands" not in bot:
                    bot["commands"] = {}
                existing = bot["commands"].get(cmd_name, {})
                bot["commands"][cmd_name] = {
                    "response": response,
                    "buttons": buttons,
                    "photo": photo if photo is not None else existing.get("photo"),
                    "media": media if media is not None else existing.get("media"),
                    "callbacks": existing.get("callbacks", {}),
                    "description": description if description != "" else existing.get("description", ""),
                }
        else:
            doc = self._bots.find_one({"bot_id": bot_id}, {f"commands.{cmd_name}": 1})
            existing = {}
            if doc and "commands" in doc:
                existing = doc["commands"].get(cmd_name, {})
            new_cmd = {
                "response": response,
                "buttons": buttons,
                "photo": photo if photo is not None else existing.get("photo"),
                "media": media if media is not None else existing.get("media"),
                "callbacks": existing.get("callbacks", {}),
                "description": description if description != "" else existing.get("description", ""),
            }
            self._bots.update_one({"bot_id": bot_id}, {"$set": {f"commands.{cmd_name}": new_cmd}})

    def set_bot_config(self, bot_id: str, cfg: dict):
        """تنظیمات عمومی ربات: welcome_message / unknown_reply / auto_menu"""
        allowed = ("welcome_message", "unknown_reply", "auto_menu")
        patch = {k: cfg[k] for k in allowed if k in cfg}
        if not patch:
            return
        if self._is_memory:
            if bot_id in self._bots:
                self._bots[bot_id].update(patch)
        else:
            self._bots.update_one({"bot_id": bot_id}, {"$set": patch})

    def record_daily(self, bot_id: str, key: str, amount: int = 1):
        """ثبت آمار روزانه: new_users / messages_in / messages_out"""
        day = now_ir().strftime("%Y-%m-%d")
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if not bot: return
            ds = bot.setdefault("daily_stats", {})
            day_data = ds.setdefault(day, {})
            day_data[key] = day_data.get(key, 0) + amount
        else:
            self._bots.update_one(
                {"bot_id": bot_id},
                {"$inc": {f"daily_stats.{day}.{key}": amount}}
            )

    def delete_command(self, bot_id: str, cmd_name: str):
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot and cmd_name in bot.get("commands", {}):
                del bot["commands"][cmd_name]
        else:
            self._bots.update_one({"bot_id": bot_id}, {"$unset": {f"commands.{cmd_name}": ""}})

    def set_command_callback(self, bot_id: str, cmd_name: str, cb_id: str, response: str):
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot:
                if cmd_name in bot.get("commands", {}):
                    if "callbacks" not in bot["commands"][cmd_name]:
                        bot["commands"][cmd_name]["callbacks"] = {}
                    bot["commands"][cmd_name]["callbacks"][cb_id] = response
        else:
            field = f"commands.{cmd_name}.callbacks.{cb_id}"
            self._bots.update_one({"bot_id": bot_id}, {"$set": {field: response}})

    def delete_command_callback(self, bot_id: str, cmd_name: str, cb_id: str):
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot and cmd_name in bot.get("commands", {}):
                if cb_id in bot["commands"][cmd_name].get("callbacks", {}):
                    del bot["commands"][cmd_name]["callbacks"][cb_id]
        else:
            field = f"commands.{cmd_name}.callbacks.{cb_id}"
            self._bots.update_one({"bot_id": bot_id}, {"$unset": {field: ""}})

    # ── آمار ──────────────────────────────────────────────────────────
    def add_bot_user(self, bot_id: str, uid: int) -> bool:
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if not bot: return False
            if uid in bot.get("bot_users", []): return False
            bot.setdefault("bot_users", []).append(uid)
            bot.setdefault("stats", {})["unique_users"] = bot["stats"].get("unique_users", 0) + 1
            day = now_ir().strftime("%Y-%m-%d")
            dd = bot.setdefault("daily_stats", {}).setdefault(day, {})
            dd["new_users"] = dd.get("new_users", 0) + 1
            bot["last_activity"] = now_ir().isoformat()
            return True
            
        doc = self._bots.find_one({"bot_id": bot_id}, {"bot_users": 1})
        if not doc:
            return False
        if uid in doc.get("bot_users", []):
            return False
        day = now_ir().strftime("%Y-%m-%d")
        self._bots.update_one(
            {"bot_id": bot_id},
            {
                "$addToSet": {"bot_users": uid},
                "$inc": {"stats.unique_users": 1, f"daily_stats.{day}.new_users": 1},
                "$set": {"last_activity": now_ir().isoformat()},
            }
        )
        return True

    def update_bot_stats(self, bot_id: str, received: int = 0, sent: int = 0):
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot:
                if received:
                    bot.setdefault("stats", {})["messages_received"] = bot["stats"].get("messages_received", 0) + received
                if sent:
                    bot.setdefault("stats", {})["messages_sent"] = bot["stats"].get("messages_sent", 0) + sent
                bot["last_activity"] = now_ir().isoformat()
        else:
            inc = {}
            if received: inc["stats.messages_received"] = received
            if sent:     inc["stats.messages_sent"] = sent
            if inc:
                self._bots.update_one({"bot_id": bot_id}, {
                    "$inc": inc,
                    "$set": {"last_activity": now_ir().isoformat()},
                })

    def record_broadcast(self, bot_id: str, sent: int):
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot:
                bot.setdefault("stats", {})["broadcasts_sent"] = bot["stats"].get("broadcasts_sent", 0) + 1
                bot["stats"]["messages_sent"] = bot["stats"].get("messages_sent", 0) + sent
        else:
            self._bots.update_one({"bot_id": bot_id}, {
                "$inc": {"stats.broadcasts_sent": 1, "stats.messages_sent": sent}
            })

    # ══════════════════════════════════════════════════════════════════
    # سیستم کوین و رفرال
    # ══════════════════════════════════════════════════════════════════
    def add_coins(self, uid: int, amount: int, reason: str = "") -> bool:
        if self._is_memory:
            if uid in self._users:
                self._users[uid]["coins"] = self._users[uid].get("coins", 0) + amount
                return True
            return False
        else:
            result = self._users.update_one(
                {"uid": uid},
                {"$inc": {"coins": amount}}
            )
            return result.modified_count > 0

    def get_user_referral_info(self, uid: int) -> dict:
        user = self.get_user(uid)
        if not user:
            return {}
        return {
            "referral_code": user.get("referral_code", ""),
            "referred_by": user.get("referred_by"),
            "referral_count": user.get("referral_count", 0),
            "coins": user.get("coins", 0),
        }

    def find_user_by_referral_code(self, code: str) -> Optional[dict]:
        return self._find_user_by({"referral_code": code})

    # ══════════════════════════════════════════════════════════════════
    # سیستم محدودیت ثبت‌نام
    # ══════════════════════════════════════════════════════════════════
    def __init_registration_tracker(self):
        if not hasattr(self, '_registration_tracker'):
            self._registration_tracker = {
                'ips': {},
                'devices': {},
            }
    
    def check_registration_limit(self, ip: str, device_id: str) -> tuple:
        self.__init_registration_tracker()
        
        now = time.time()
        day_ago = now - 86400
        
        for ip_key in list(self._registration_tracker['ips'].keys()):
            self._registration_tracker['ips'][ip_key] = [
                t for t in self._registration_tracker['ips'][ip_key] if t > day_ago
            ]
        
        for dev_key in list(self._registration_tracker['devices'].keys()):
            self._registration_tracker['devices'][dev_key] = [
                t for t in self._registration_tracker['devices'][dev_key] if t > day_ago
            ]
        
        ip_records = self._registration_tracker['ips'].get(ip, [])
        if len(ip_records) >= 2:
            return False, "حداکثر ۲ حساب از هر IP مجاز است"
        
        if ip_records:
            last_time = max(ip_records)
            if now - last_time < 600:
                remaining = int((600 - (now - last_time)) / 60)
                return False, f"لطفاً {remaining} دقیقه دیگر تلاش کنید"
        
        device_records = self._registration_tracker['devices'].get(device_id, [])
        if len(device_records) >= 1:
            return False, "این دستگاه قبلاً ثبت‌نام کرده است"
        
        return True, "OK"
    
    def record_registration(self, ip: str, device_id: str):
        self.__init_registration_tracker()
        now = time.time()
        
        if ip not in self._registration_tracker['ips']:
            self._registration_tracker['ips'][ip] = []
        self._registration_tracker['ips'][ip].append(now)
        
        if device_id not in self._registration_tracker['devices']:
            self._registration_tracker['devices'][device_id] = []
        self._registration_tracker['devices'][device_id].append(now)

    def set_force_join(self, bot_id: str, channel: Optional[str]):
        if self._is_memory:
            if bot_id in self._bots:
                self._bots[bot_id]["force_join_channel"] = channel
        else:
            self._bots.update_one({"bot_id": bot_id}, {"$set": {"force_join_channel": channel}})

    # ══════════════════════════════════════════════════════════════════
    # موتور پاسخ هوشمند — قوانین کلیدواژه‌ای
    # rule = {id, keywords:[...], mode:(exact|contains|regex), response, media}
    # ══════════════════════════════════════════════════════════════════
    def add_rule(self, bot_id: str, rule: dict) -> list:
        if not rule.get("id"):
            rule["id"] = "r_" + secrets.token_hex(4)
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if not bot: return []
            rules = bot.setdefault("rules", [])
            for i, r in enumerate(rules):
                if r.get("id") == rule["id"]:
                    rules[i] = rule; return copy.deepcopy(rules)
            rules.append(rule); return copy.deepcopy(rules)
        bot = self._bots.find_one({"bot_id": bot_id}, {"rules": 1}) or {}
        rules = bot.get("rules", [])
        for i, r in enumerate(rules):
            if r.get("id") == rule["id"]:
                rules[i] = rule; break
        else:
            rules.append(rule)
        self._bots.update_one({"bot_id": bot_id}, {"$set": {"rules": rules}})
        return rules

    def delete_rule(self, bot_id: str, rule_id: str) -> list:
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if not bot: return []
            bot["rules"] = [r for r in bot.get("rules", []) if r.get("id") != rule_id]
            return copy.deepcopy(bot["rules"])
        bot = self._bots.find_one({"bot_id": bot_id}, {"rules": 1}) or {}
        rules = [r for r in bot.get("rules", []) if r.get("id") != rule_id]
        self._bots.update_one({"bot_id": bot_id}, {"$set": {"rules": rules}})
        return rules

    @staticmethod
    def match_rule(rules: list, text: str):
        """اولین قانونِ منطبق با متن را برمی‌گرداند"""
        t = (text or "").strip().lower()
        if not t:
            return None
        for r in rules or []:
            mode = r.get("mode", "contains")
            for kw in r.get("keywords", []):
                k = (kw or "").strip().lower()
                if not k:
                    continue
                if mode == "exact" and t == k:
                    return r
                if mode == "contains" and k in t:
                    return r
                if mode == "regex":
                    try:
                        if re.search(kw, text or "", re.IGNORECASE):
                            return r
                    except re.error:
                        pass
        return None

    # ══════════════════════════════════════════════════════════════════
    # کتابخانهٔ کاربرانِ هر ربات
    # ══════════════════════════════════════════════════════════════════
    def set_bot_user_info(self, bot_id: str, uid: int, name: str = "", username: str = ""):
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if not bot: return
            info = bot.setdefault("bot_user_info", {})
            if str(uid) not in info:
                info[str(uid)] = {"name": name, "username": username, "joined": now_ir().isoformat()}
            else:
                info[str(uid)].update({"name": name or info[str(uid)].get("name", ""),
                                       "username": username or info[str(uid)].get("username", "")})
        else:
            self._bots.update_one(
                {"bot_id": bot_id, "bot_user_info": {"$exists": True}},
                {"$set": {f"bot_user_info.{uid}.name": name, f"bot_user_info.{uid}.username": username}},
            )
            self._bots.update_one(
                {"bot_id": bot_id, f"bot_user_info.{uid}": {"$exists": False}},
                {"$set": {f"bot_user_info.{uid}": {"name": name, "username": username, "joined": now_ir().isoformat()}}},
            )

    def get_bot_users(self, bot_id: str) -> list:
        b = self.get_bot(bot_id)
        if not b: return []
        users = b.get("bot_users", [])
        info = b.get("bot_user_info", {})
        out = []
        for u in users:
            meta = info.get(str(u), {})
            out.append({"uid": u, "name": meta.get("name", ""), "username": meta.get("username", ""), "joined": meta.get("joined", "")})
        return out

    # ══════════════════════════════════════════════════════════════════
    # سیستم Auth
    # ══════════════════════════════════════════════════════════════════
    def register_user(self, username: str, password: str, full_name: str = "", 
                      referral_code: str = None, 
                      ip: str = "", device_id: str = "") -> tuple:
        """ثبت‌نام کاربر جدید"""
        
        existing = self._find_user_by({"username": username})
        if existing:
            raise ValueError("این نام کاربری قبلاً ثبت شده است")
        
        referrer_uid = None
        if referral_code:
            referrer = self.find_user_by_referral_code(referral_code)
            if referrer:
                referrer_uid = referrer["uid"]
        
        if self._is_memory:
            all_uids = [u["uid"] for u in self._users.values()]
            uid = max(all_uids) + 1 if all_uids else 1000000
        else:
            uid = self._users.count_documents({}) + 1000000
        
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        token = secrets.token_urlsafe(32)
        user_referral_code = secrets.token_urlsafe(8)
        initial_coins = 50 if referrer_uid else 0
        
        new_user = {
            "uid": uid,
            "username": username,
            "password_hash": password_hash,
            "full_name": full_name or username,
            "is_admin": False,
            "is_banned": False,
            "created_at": now_ir().isoformat(),
            "bots_created": [],
            "auth_token": token,
            "coins": initial_coins,
            "referral_code": user_referral_code,
            "referred_by": referrer_uid,
            "referral_count": 0,
            "registration_ip": ip,
            "device_id": device_id,
        }
        
        if self._is_memory:
            self._users[uid] = new_user
        else:
            self._users.insert_one(new_user)
        
        if referrer_uid:
            self.add_coins(referrer_uid, 100, "referral_reward")
            if self._is_memory:
                self._users[referrer_uid]["referral_count"] = \
                    self._users[referrer_uid].get("referral_count", 0) + 1
            else:
                self._users.update_one(
                    {"uid": referrer_uid},
                    {"$inc": {"referral_count": 1}}
                )
            log.info(f"🎉 کاربر {uid} با username {username} و رفرال {referral_code} ثبت‌نام کرد")
        
        self.record_registration(ip, device_id)
        
        user_copy = copy.deepcopy(new_user)
        user_copy.pop("password_hash", None)
        user_copy.pop("auth_token", None)
        user_copy.pop("_id", None)
        
        return user_copy, token
    
    def login_user(self, username: str, password: str) -> tuple:
        user = self._find_user_by({"username": username})
        
        if not user:
            raise ValueError("نام کاربری یا رمز عبور اشتباه است")
        
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        if user.get("password_hash") != password_hash:
            raise ValueError("نام کاربری یا رمز عبور اشتباه است")
        
        if user.get("is_banned"):
            raise ValueError("حساب شما مسدود شده است")
        
        token = secrets.token_urlsafe(32)
        
        if self._is_memory:
            self._users[user["uid"]]["auth_token"] = token
        else:
            self._users.update_one(
                {"uid": user["uid"]},
                {"$set": {"auth_token": token}}
            )
        
        user_copy = copy.deepcopy(user)
        user_copy["auth_token"] = token
        user_copy.pop("password_hash", None)
        user_copy.pop("_id", None)
        
        return user_copy, token
    
    def get_user_by_token(self, token: str) -> Optional[dict]:
        user = self._find_user_by({"auth_token": token})
        if user:
            user_copy = copy.deepcopy(user)
            user_copy.pop("password_hash", None)
            user_copy.pop("auth_token", None)
            user_copy.pop("_id", None)
            return user_copy
        return None
    
    def logout_user(self, token: str) -> bool:
        if self._is_memory:
            for uid, user in self._users.items():
                if user.get("auth_token") == token:
                    self._users[uid].pop("auth_token", None)
                    return True
            return False
        else:
            result = self._users.update_one(
                {"auth_token": token},
                {"$unset": {"auth_token": ""}}
            )
            return result.modified_count > 0


storage = Storage()


# ══════════════════════════════════════════════════════════════════════
# BotFactory
# ══════════════════════════════════════════════════════════════════════
class BotRunner:
    def __init__(self, bot_id: str, bot_token: str):
        self.bot_id = bot_id
        self.bot_token = bot_token
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._app: Optional[Application] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stopped = threading.Event()

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        
        self._ready.clear()
        self._stopped.clear()
        
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=15)
        return not self._stopped.is_set()

    def stop(self):
        if self._loop and self._app and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        self._stopped.wait(timeout=10)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._start())
        except Exception as e:
            log.error(f"ساب‌بات {self.bot_id} خطا: {e}")
        finally:
            self._stopped.set()

    async def _start(self):
        self._app = Application.builder().token(self.bot_token).build()
        self._app.add_handler(CommandHandler(
            "start", lambda u, c: _subbot_start(u, c, self.bot_id)))
        self._app.add_handler(CallbackQueryHandler(
            lambda u, c: _subbot_callback(u, c, self.bot_id)))
        self._app.add_handler(MessageHandler(
            filters.TEXT & filters.COMMAND,
            lambda u, c: _subbot_command(u, c, self.bot_id)))
        self._app.add_handler(MessageHandler(
            filters.TEXT & (~filters.COMMAND),
            lambda u, c: _subbot_text(u, c, self.bot_id)))

        await self._app.initialize()
        me = await self._app.bot.get_me()
        storage.update_bot_username(self.bot_id, me.username or "")

        self._ready.set()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        
        log.info(f"🟢 ساب‌بات @{me.username} ({self.bot_id}) روشن شد")
        
        while not self._stopped.is_set():
            await asyncio.sleep(1)

    async def _shutdown(self):
        try:
            if self._app.updater.running:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        except Exception as e:
            log.warning(f"خطا در shutdown {self.bot_id}: {e}")
        finally:
            self._stopped.set()

    def get_bot(self) -> Optional[Bot]:
        return self._app.bot if self._app else None


class BotFactory:
    def __init__(self):
        self._runners: dict = {}
        self._lock = threading.Lock()

    def start_bot(self, bot_id: str, bot_token: str) -> bool:
        with self._lock:
            runner = self._runners.get(bot_id)
            if runner:
                runner.stop()
                del self._runners[bot_id]
            
            time.sleep(1)
            
            runner = BotRunner(bot_id, bot_token)
            ok = runner.start()
            if ok:
                self._runners[bot_id] = runner
            return ok

    def stop_bot(self, bot_id: str):
        with self._lock:
            runner = self._runners.pop(bot_id, None)
        if runner:
            runner.stop()
        log.info(f"🔴 ساب‌بات {bot_id} خاموش شد")

    def is_running(self, bot_id: str) -> bool:
        runner = self._runners.get(bot_id)
        return runner is not None and runner._thread is not None and runner._thread.is_alive()

    def get_bot_instance(self, bot_id: str) -> Optional[Bot]:
        runner = self._runners.get(bot_id)
        return runner.get_bot() if runner else None

    def start_all_active(self):
        for b in storage.get_all_bots():
            if b.get("is_active"):
                self.start_bot(b["bot_id"], b["bot_token"])


factory = BotFactory()

# ══════════════════════════════════════════════════════════════════════
# متغیرهای global برای ربات مادر
# ══════════════════════════════════════════════════════════════════════
mother_bot_instance = None
mother_bot_loop = None


# ══════════════════════════════════════════════════════════════════════
# هندلرهای ساب‌بات
# ══════════════════════════════════════════════════════════════════════
def _build_inline(buttons) -> Optional[InlineKeyboardMarkup]:
    if not buttons:
        return None
    rows = []
    for row in buttons:
        rrow = []
        for btn in row:
            url = btn.get("url")
            if url:
                # دکمهٔ لینک — بدون callback
                rrow.append(InlineKeyboardButton(btn["text"], url=url))
            else:
                rrow.append(InlineKeyboardButton(
                    btn["text"], callback_data=btn.get("id") or btn["text"]))
        rows.append(rrow)
    return InlineKeyboardMarkup(rows)


async def _send_media(bot, chat_id, media, caption, kb):
    """ارسال رسانه بر اساس نوع"""
    if not media or not media.get("url"):
        return False
    
    media_type = media.get("type", "photo")
    url = media["url"]
    
    try:
        if media_type == "photo":
            await bot.send_photo(
                chat_id, url, 
                caption=caption, 
                reply_markup=kb, 
                parse_mode=ParseMode.HTML
            )
        elif media_type == "video":
            await bot.send_video(
                chat_id, url,
                caption=caption,
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
                supports_streaming=True
            )
        elif media_type == "audio":
            await bot.send_audio(
                chat_id, url,
                caption=caption,
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
        elif media_type == "animation":
            await bot.send_animation(
                chat_id, url,
                caption=caption,
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
        elif media_type == "document":
            # برای document، caption را جدا بفرست
            await bot.send_document(
                chat_id, url,
                caption=caption,
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
        else:
            # fallback به photo
            await bot.send_photo(
                chat_id, url,
                caption=caption,
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
        return True
    except Exception as e:
        log.error(f"❌ خطا در ارسال {media_type}: {e}")
        return False


async def _subbot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE, bot_id: str):
    uid = update.effective_user.id
    eu = update.effective_user
    b = storage.get_bot(bot_id)
    if not b:
        return
    is_new = storage.add_bot_user(bot_id, uid)
    storage.set_bot_user_info(bot_id, uid, eu.full_name or "", eu.username or "")
    storage.update_bot_stats(bot_id, received=1)
    storage.record_daily(bot_id, "messages_in", 1)

    cmd      = b.get("commands", {}).get("start", {})
    response = cmd.get("response", "سلام 👋")
    buttons  = cmd.get("buttons")
    photo    = cmd.get("photo")  # backward compat
    media    = cmd.get("media")  # سیستم جدید

    log.info(f"📩 /start ساب‌بات {bot_id}: media={media}, photo={photo}, buttons={bool(buttons)}")

    kb = _build_inline(buttons)

    try:
        # پیام خوش‌آمد مخصوص عضو جدید (قبل از پاسخ /start)
        if is_new and b.get("welcome_message"):
            await ctx.bot.send_message(
                update.effective_chat.id, b["welcome_message"],
                parse_mode=ParseMode.HTML)
            storage.update_bot_stats(bot_id, sent=1)

        # اولویت: media جدید، سپس photo قدیمی
        sent = False
        if media and media.get("url"):
            sent = await _send_media(ctx.bot, update.effective_chat.id, media, response, kb)
        elif photo:
            # backward compat
            legacy_media = {"type": "photo", "url": photo}
            sent = await _send_media(ctx.bot, update.effective_chat.id, legacy_media, response, kb)

        if not sent:
            # fallback به متن ساده
            await update.effective_message.reply_text(
                response, reply_markup=kb, parse_mode=ParseMode.HTML)

        storage.update_bot_stats(bot_id, sent=1)
    except Exception as e:
        log.error(f"❌ خطا در /start ساب‌بات {bot_id}: {e}")
        try:
            await update.effective_message.reply_text(response, reply_markup=kb)
            storage.update_bot_stats(bot_id, sent=1)
        except Exception:
            pass


async def _subbot_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE, bot_id: str):
    uid  = update.effective_user.id
    text = (update.effective_message.text or "").strip()
    b    = storage.get_bot(bot_id)
    if not b:
        return
    storage.add_bot_user(bot_id, uid)
    storage.update_bot_stats(bot_id, received=1)
    storage.record_daily(bot_id, "messages_in", 1)

    cmd_name = text.lstrip("/").split()[0].lower() if text.startswith("/") else ""
    cmd = b.get("commands", {}).get(cmd_name)
    if not cmd:
        # پاسخ خودکار به دستور ناشناخته
        if cmd_name:
            unknown = (b.get("unknown_reply") or "").strip()
            if unknown:
                try:
                    await update.effective_message.reply_text(
                        unknown, parse_mode=ParseMode.HTML)
                    storage.update_bot_stats(bot_id, sent=1)
                    storage.record_daily(bot_id, "messages_out", 1)
                except Exception as e:
                    log.error(f"❌ خطا در unknown_reply {bot_id}: {e}")
        return

    kb       = _build_inline(cmd.get("buttons"))
    photo    = cmd.get("photo")
    media    = cmd.get("media")
    response = cmd.get("response", "")

    log.info(f"📩 /{cmd_name} ساب‌بات {bot_id}: media={media}, photo={photo}")

    try:
        sent = False
        if media and media.get("url"):
            sent = await _send_media(ctx.bot, update.effective_chat.id, media, response, kb)
        elif photo:
            legacy_media = {"type": "photo", "url": photo}
            sent = await _send_media(ctx.bot, update.effective_chat.id, legacy_media, response, kb)

        if not sent:
            await update.effective_message.reply_text(
                response, reply_markup=kb, parse_mode=ParseMode.HTML)

        storage.update_bot_stats(bot_id, sent=1)
        storage.record_daily(bot_id, "messages_out", 1)
    except Exception as e:
        log.error(f"❌ خطا در /{cmd_name} ساب‌بات {bot_id}: {e}")
        try:
            await update.effective_message.reply_text(response, reply_markup=kb)
            storage.update_bot_stats(bot_id, sent=1)
        except Exception:
            pass


async def _subbot_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE, bot_id: str):
    """پاسخ هوشمند به متن معمولی کاربر بر اساس قوانین کلیدواژه‌ای"""
    uid = update.effective_user.id
    eu = update.effective_user
    text = (update.effective_message.text or "").strip()
    b = storage.get_bot(bot_id)
    if not b:
        return
    storage.add_bot_user(bot_id, uid)
    storage.set_bot_user_info(bot_id, uid, eu.full_name or "", eu.username or "")
    storage.update_bot_stats(bot_id, received=1)
    storage.record_daily(bot_id, "messages_in", 1)

    rule = storage.match_rule(b.get("rules", []), text)
    if not rule:
        unknown = (b.get("unknown_reply") or "").strip()
        if unknown:
            try:
                await update.effective_message.reply_text(unknown, parse_mode=ParseMode.HTML)
                storage.update_bot_stats(bot_id, sent=1)
                storage.record_daily(bot_id, "messages_out", 1)
            except Exception as e:
                log.error(f"❌ خطا در unknown_reply {bot_id}: {e}")
        return

    resp = rule.get("response", "")
    kb = _build_inline(rule.get("buttons"))
    media = rule.get("media")
    try:
        sent = False
        if media and media.get("url"):
            sent = await _send_media(ctx.bot, update.effective_chat.id, media, resp, kb)
        if not sent and resp:
            await update.effective_message.reply_text(resp, reply_markup=kb, parse_mode=ParseMode.HTML)
        storage.update_bot_stats(bot_id, sent=1)
        storage.record_daily(bot_id, "messages_out", 1)
    except Exception as e:
        log.error(f"❌ خطا در rule-reply {bot_id}: {e}")
        try:
            await update.effective_message.reply_text(resp, reply_markup=kb)
            storage.update_bot_stats(bot_id, sent=1)
        except Exception:
            pass


async def _subbot_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE, bot_id: str):
    q    = update.callback_query
    data = q.data or ""
    await q.answer()

    b = storage.get_bot(bot_id)
    if not b:
        return

    for cmd_name, cmd in b.get("commands", {}).items():
        if data in cmd.get("callbacks", {}):
            resp = cmd["callbacks"][data]
            await q.message.reply_text(resp)
            storage.update_bot_stats(bot_id, sent=1)
            return


# ══════════════════════════════════════════════════════════════════════
# ربات مادر
# ══════════════════════════════════════════════════════════════════════
async def mother_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global mother_bot_instance, mother_bot_loop
    
    mother_bot_instance = ctx.bot
    mother_bot_loop = asyncio.get_event_loop()
    
    user = update.effective_user
    
    u = storage.ensure_user(
        user.id, 
        (user.username or "").lower(),
        (user.full_name or "").strip()
    )
    
    if not storage._is_memory:
        storage._users.update_one(
            {"uid": user.id},
            {"$set": {
                "telegram_username": (user.username or "").lower(),
                "telegram_user_id": user.id,
                "telegram_first_name": user.first_name or "",
            }}
        )
    else:
        if user.id in storage._users:
            storage._users[user.id]["telegram_username"] = (user.username or "").lower()
            storage._users[user.id]["telegram_user_id"] = user.id
            storage._users[user.id]["telegram_first_name"] = user.first_name or ""
    
    if u.get("is_banned"):
        await update.effective_message.reply_text("🚫 حساب شما مسدود شده است.")
        return

    text = (
        f"🤖 <b>سلام {user.first_name or 'دوست عزیز'}!</b>\n\n"
        f"✅ اکانت شما در GramSaz آماده شد.\n\n"
        f"📌 <b>Username تلگرام شما:</b> @{user.username or 'ندارید'}\n\n"
        f"👇 برای ثبت‌نام در پنل، به سایت برگردید و username خود را وارد کنید."
    )

    rows = []
    if WEBAPP_URL:
        rows.append([InlineKeyboardButton("🚀 باز کردن پنل مدیریت", web_app=WebAppInfo(url=WEBAPP_URL))])
    
    # دکمه آمار ادمین (فقط برای ادمین‌ها)
    if is_admin(user.id):
        rows.append([InlineKeyboardButton("📊 آمار پلتفرم", callback_data="admin_stats")])

    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows)
    )


async def mother_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user = q.from_user
    data = q.data
    
    if data == "no_webapp":
        await q.message.reply_text("⚙️ WEBAPP_URL در سرور تنظیم نشده است.")
        return
    
    if data == "admin_stats":
        # فقط ادمین دسترسی دارد
        if not is_admin(user.id):
            await q.message.reply_text("🚫 شما دسترسی ادمین ندارید.")
            return
        
        # دریافت آمار
        try:
            all_users = storage.get_all_users()
            all_bots = storage.get_all_bots()
            
            total_users = len(all_users)
            total_registered = sum(1 for u in all_users if u.get("password_hash"))
            total_active_bots = sum(1 for b in all_bots if b.get("is_active"))
            total_messages = sum(b.get("stats", {}).get("messages_received", 0) for b in all_bots)
            total_bot_users = sum(len(b.get("bot_users", [])) for b in all_bots)
            total_coins = sum(u.get("coins", 0) for u in all_users)
            
            text = (
                f"📊 <b>آمار پلتفرم GramSaz</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👥 <b>کاربران:</b>\n"
                f"   • کل کاربران: <code>{total_users}</code>\n"
                f"   • ثبت‌نام شده: <code>{total_registered}</code>\n\n"
                f"🤖 <b>ربات‌ها:</b>\n"
                f"   • کل ربات‌ها: <code>{len(all_bots)}</code>\n"
                f"   • فعال: <code>{total_active_bots}</code>\n"
                f"   • کاربران ربات‌ها: <code>{total_bot_users}</code>\n\n"
                f"💬 <b>پیام‌ها:</b>\n"
                f"   • کل پیام‌های دریافتی: <code>{total_messages}</code>\n\n"
                f"🪙 <b>اقتصاد:</b>\n"
                f"   • کوین‌های توزیع شده: <code>{total_coins}</code>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💡 برای آمار دقیق‌تر و نمودارها، از پنل وب استفاده کنید."
            )
            
            rows = []
            if WEBAPP_URL:
                rows.append([InlineKeyboardButton(
                    "🌐 مشاهده در پنل وب (با نمودار)",
                    web_app=WebAppInfo(url=WEBAPP_URL)
                )])
            
            await q.message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(rows) if rows else None
            )
            
        except Exception as e:
            log.error(f"❌ خطا در دریافت آمار تلگرام: {e}")
            await q.message.reply_text(f"❌ خطا در دریافت آمار: {e}")


def run_mother_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _run():
        app = Application.builder().token(PLATFORM_BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", mother_start))
        app.add_handler(CallbackQueryHandler(mother_callback))
        log.info("🤖 ربات مادر شروع به polling کرد")
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()

    loop.run_until_complete(_run())


# ══════════════════════════════════════════════════════════════════════
# Flask App
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)


def get_current_user():
    init_data = request.headers.get("X-Init-Data", "")
    if init_data:
        user = verify_telegram_init_data(init_data, PLATFORM_BOT_TOKEN)
        if user:
            uid = user.get("id")
            if uid:
                db_user = storage.get_user(int(uid))
                if not db_user:
                    db_user = storage.ensure_user(
                        int(uid), user.get("username", ""), user.get("first_name", "")
                    )
                if not db_user.get("is_banned"):
                    return db_user, None
        return None, (jsonify({"error": "initData نامعتبر است"}), 401)
    
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if token:
        user = storage.get_user_by_token(token)
        if user:
            if not user.get("is_banned"):
                return user, None
        return None, (jsonify({"error": "توکن نامعتبر یا منقضی شده"}), 401)
    
    return None, (jsonify({"error": "احراز هویت نشده"}), 401)


def auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user, err = get_current_user()
        if err:
            return err
        g.current_user = user
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user, err = get_current_user()
        if err:
            return err
        if not user.get("is_admin") and user["uid"] not in ADMIN_IDS:
            return jsonify({"error": "دسترسی فقط برای ادمین‌ها"}), 403
        g.current_user = user
        return f(*args, **kwargs)
    return decorated


def get_owned_bot(bot_id: str):
    b = storage.get_bot(bot_id)
    if not b:
        return None, (jsonify({"error": "ربات یافت نشد"}), 404)
    user = g.current_user
    if b["owner_uid"] != user["uid"] and not user.get("is_admin"):
        return None, (jsonify({"error": "دسترسی ندارید"}), 403)
    return b, None


@flask_app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,X-Init-Data,Authorization,Accept"
    response.headers["Access-Control-Max-Age"] = "3600"
    return response

@flask_app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@flask_app.route("/<path:path>", methods=["OPTIONS"])
def options_handler(path):
    return "", 204


# ──────────────────────────────────────────────────────────────────────
# /api/me
# ──────────────────────────────────────────────────────────────────────
@flask_app.get("/api/me")
@auth_required
def api_me():
    return jsonify(g.current_user)


# ──────────────────────────────────────────────────────────────────────
# /api/bots
# ──────────────────────────────────────────────────────────────────────
@flask_app.get("/api/bots")
@auth_required
def api_list_bots():
    bots = storage.get_user_bots(g.current_user["uid"])
    for b in bots:
        b["is_running"] = factory.is_running(b["bot_id"])
        b.pop("bot_token", None)
    return jsonify({"bots": bots})


@flask_app.post("/api/bots")
@auth_required
def api_add_bot():
    data  = request.get_json(force=True) or {}
    token = (data.get("token") or "").strip()
    if ":" not in token or len(token) < 30:
        return jsonify({"error": "توکن نامعتبر است"}), 400

    existing   = storage.get_all_bots()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    for b in existing:
        if hashlib.sha256(b.get("bot_token", "").encode()).hexdigest() == token_hash:
            return jsonify({"error": "این ربات قبلاً اضافه شده"}), 409

    import requests as req_lib
    try:
        resp = req_lib.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        if not resp.ok or not resp.json().get("ok"):
            return jsonify({"error": "توکن معتبر نیست — از @BotFather بگیر"}), 400
        me_data = resp.json()["result"]
    except Exception as e:
        return jsonify({"error": f"خطا در ارتباط با تلگرام: {e}"}), 500

    bot_rec = storage.create_bot(g.current_user["uid"], token, me_data.get("username", ""))
    ok      = factory.start_bot(bot_rec["bot_id"], token)
    bot_rec.pop("bot_token", None)
    bot_rec["is_running"] = ok
    return jsonify({"bot": bot_rec, "message": f"✅ ربات @{me_data.get('username')} اضافه شد"}), 201


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>
# ──────────────────────────────────────────────────────────────────────
@flask_app.get("/api/bots/<bot_id>")
@auth_required
def api_get_bot(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    b.pop("bot_token", None)
    b["is_running"] = factory.is_running(bot_id)
    return jsonify({"bot": b})


@flask_app.delete("/api/bots/<bot_id>")
@auth_required
def api_delete_bot(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    factory.stop_bot(bot_id)
    ok = storage.delete_bot(bot_id, g.current_user["uid"])
    if not ok:
        return jsonify({"error": "حذف ناموفق بود"}), 500
    return jsonify({"message": "ربات حذف شد"})


@flask_app.route("/api/bots/<bot_id>/toggle", methods=["PATCH"])
@auth_required
def api_toggle_bot(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    data   = request.get_json(force=True) or {}
    active = bool(data.get("active", True))
    storage.set_bot_active(bot_id, active)
    if active:
        ok = factory.start_bot(bot_id, b["bot_token"])
    else:
        factory.stop_bot(bot_id)
        ok = True
    return jsonify({"active": active, "is_running": factory.is_running(bot_id)})


@flask_app.route("/api/bots/<bot_id>/force-join", methods=["PATCH"])
@auth_required
def api_force_join(bot_id):
    _, err = get_owned_bot(bot_id)
    if err: return err
    data    = request.get_json(force=True) or {}
    channel = data.get("channel") or None
    storage.set_force_join(bot_id, channel)
    return jsonify({"force_join_channel": channel})


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>/commands
# ──────────────────────────────────────────────────────────────────────
@flask_app.get("/api/bots/<bot_id>/commands")
@auth_required
def api_list_commands(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    cmds    = b.get("commands", {})
    ordered = {}
    if "start" in cmds:
        ordered["start"] = cmds["start"]
    for k, v in sorted(cmds.items()):
        if k != "start":
            ordered[k] = v
    return jsonify({"commands": ordered})


@flask_app.route("/api/bots/<bot_id>/commands/<cmd_name>", methods=["PUT"])
@auth_required
def api_set_command(bot_id, cmd_name):
    _, err = get_owned_bot(bot_id)
    if err: return err
    data     = request.get_json(force=True) or {}
    response = data.get("response", "")
    buttons  = data.get("buttons")
    photo    = data.get("photo")
    media    = data.get("media")  # ✅ سیستم جدید
    description = data.get("description", "")  # توضیح برای منوی تلگرام

    log.info(f"💾 ذخیره دستور /{cmd_name} برای {bot_id}: media={media}, photo={bool(photo)}, buttons={bool(buttons)}")

    storage.set_command(bot_id, cmd_name, response, buttons, photo, media, description)
    return jsonify({"message": f"دستور /{cmd_name} ذخیره شد"})

@flask_app.route("/api/bots/<bot_id>/commands/<cmd_name>", methods=["DELETE"])
@auth_required
def api_delete_command(bot_id, cmd_name):
    if cmd_name == "start":
        return jsonify({"error": "دستور /start قابل حذف نیست"}), 400
    _, err = get_owned_bot(bot_id)
    if err: return err
    storage.delete_command(bot_id, cmd_name)
    return jsonify({"message": f"دستور /{cmd_name} حذف شد"})


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>/commands/<cmd_name>/callbacks/<cb_id>
# ──────────────────────────────────────────────────────────────────────
@flask_app.route("/api/bots/<bot_id>/commands/<cmd_name>/callbacks/<cb_id>", methods=["PUT"])
@auth_required
def api_set_callback(bot_id, cmd_name, cb_id):
    _, err = get_owned_bot(bot_id)
    if err: return err
    data     = request.get_json(force=True) or {}
    response = data.get("response", "")
    storage.set_command_callback(bot_id, cmd_name, cb_id, response)
    return jsonify({"message": f"پاسخ دکمه {cb_id} ذخیره شد"})


@flask_app.route("/api/bots/<bot_id>/commands/<cmd_name>/callbacks/<cb_id>", methods=["DELETE"])
@auth_required
def api_delete_callback(bot_id, cmd_name, cb_id):
    _, err = get_owned_bot(bot_id)
    if err: return err
    storage.delete_command_callback(bot_id, cmd_name, cb_id)
    return jsonify({"message": "پاسخ دکمه حذف شد"})


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>/broadcast
# ──────────────────────────────────────────────────────────────────────
@flask_app.post("/api/bots/<bot_id>/broadcast")
@auth_required
def api_broadcast(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    data  = request.get_json(force=True) or {}
    text  = data.get("text", "")
    users = b.get("bot_users", [])
    if not users:
        return jsonify({"sent": 0, "failed": 0, "message": "هنوز کاربری ندارد"})

    sub_bot = factory.get_bot_instance(bot_id)
    if not sub_bot:
        return jsonify({"error": "ربات خاموش است — اول روشنش کن"}), 400

    runner = factory._runners.get(bot_id)

    def do_broadcast():
        loop = runner._loop if runner else None
        if not loop:
            return
        sent = failed = 0
        for uid in users:
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    sub_bot.send_message(uid, text, parse_mode=ParseMode.HTML), loop)
                fut.result(timeout=10)
                sent += 1
            except Exception:
                # در صورت خطای فرمت، بدون HTML امتحان کن
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        sub_bot.send_message(uid, text), loop)
                    fut.result(timeout=10)
                    sent += 1
                except Exception:
                    failed += 1
            time.sleep(0.05)
        storage.record_broadcast(bot_id, sent)
        log.info(f"📢 broadcast {bot_id}: ✅{sent} ❌{failed}")

    threading.Thread(target=do_broadcast, daemon=True).start()
    return jsonify({"message": f"پیام برای {len(users)} کاربر در صف ارسال قرار گرفت"})


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>/config  — خوش‌آمد، پاسخ ناشناخته، منوی خودکار
# ──────────────────────────────────────────────────────────────────────
@flask_app.route("/api/bots/<bot_id>/config", methods=["PATCH"])
@auth_required
def api_bot_config(bot_id):
    _, err = get_owned_bot(bot_id)
    if err: return err
    data = request.get_json(force=True) or {}
    patch = {}
    for k in ("welcome_message", "unknown_reply", "auto_menu"):
        if k in data:
            patch[k] = data[k]
    storage.set_bot_config(bot_id, patch)
    b, _ = get_owned_bot(bot_id)
    return jsonify({
        "welcome_message": b.get("welcome_message", ""),
        "unknown_reply": b.get("unknown_reply", ""),
        "auto_menu": b.get("auto_menu", True),
        "message": "تنظیمات ذخیره شد",
    })


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>/sync-menu — همگام‌سازی دستورها با منوی تلگرام
# ──────────────────────────────────────────────────────────────────────
@flask_app.post("/api/bots/<bot_id>/sync-menu")
@auth_required
def api_sync_menu(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    sub_bot = factory.get_bot_instance(bot_id)
    if not sub_bot:
        return jsonify({"error": "ربات خاموش است — اول روشنش کن"}), 400
    cmds = b.get("commands", {})
    menu = []
    for name, cmd in cmds.items():
        desc = (cmd.get("description") or "").strip() or name
        menu.append(BotCommand(command=name[:32], description=desc[:48]))
    runner = factory._runners.get(bot_id)
    try:
        loop = runner._loop if runner else None
        if loop:
            fut = asyncio.run_coroutine_threadsafe(sub_bot.set_my_commands(menu), loop)
            fut.result(timeout=15)
        else:
            sub_bot.set_my_commands(menu)
        return jsonify({"message": f"منوی تلگرام با {len(menu)} دستور همگام شد", "count": len(menu)})
    except Exception as e:
        log.error(f"❌ خطا در sync-menu {bot_id}: {e}")
        return jsonify({"error": f"خطا در همگام‌سازی منو: {e}"}), 500


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>/rules — موتور پاسخ هوشمند (کلیدواژه)
# ──────────────────────────────────────────────────────────────────────
@flask_app.get("/api/bots/<bot_id>/rules")
@auth_required
def api_list_rules(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    return jsonify({"rules": b.get("rules", [])})


@flask_app.route("/api/bots/<bot_id>/rules", methods=["PUT"])
@auth_required
def api_set_rule(bot_id):
    _, err = get_owned_bot(bot_id)
    if err: return err
    data = request.get_json(force=True) or {}
    rule = {
        "id": (data.get("id") or "").strip(),
        "keywords": [str(k).strip() for k in (data.get("keywords") or []) if str(k).strip()],
        "mode": data.get("mode") if data.get("mode") in ("exact", "contains", "regex") else "contains",
        "response": data.get("response", ""),
        "media": data.get("media") or None,
        "buttons": data.get("buttons"),
    }
    if not rule["keywords"]:
        return jsonify({"error": "حداقل یک کلیدواژه لازم است"}), 400
    if not rule["response"] and not rule["media"]:
        return jsonify({"error": "متن پاسخ یا رسانه لازم است"}), 400
    rules = storage.add_rule(bot_id, rule)
    return jsonify({"rules": rules, "message": "قانون ذخیره شد"})


@flask_app.route("/api/bots/<bot_id>/rules/<rule_id>", methods=["DELETE"])
@auth_required
def api_delete_rule(bot_id, rule_id):
    _, err = get_owned_bot(bot_id)
    if err: return err
    rules = storage.delete_rule(bot_id, rule_id)
    return jsonify({"rules": rules, "message": "قانون حذف شد"})


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>/users — کتابخانهٔ کاربرانِ ربات
# ──────────────────────────────────────────────────────────────────────
@flask_app.get("/api/bots/<bot_id>/users")
@auth_required
def api_list_bot_users(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    q = (request.args.get("q") or "").strip().lower()
    users = storage.get_bot_users(bot_id)
    if q:
        users = [u for u in users if q in str(u.get("uid", "")).lower()
                 or q in (u.get("name", "") or "").lower()
                 or q in (u.get("username", "") or "").lower()]
    users.sort(key=lambda u: u.get("joined", ""), reverse=True)
    return jsonify({"users": users, "total": len(b.get("bot_users", []))})


# ══════════════════════════════════════════════════════════════════════
# Endpoint ارسال کد تایید (تلگرام)
# ══════════════════════════════════════════════════════════════════════
@flask_app.post("/api/auth/send-code")
def api_send_code():
    """ارسال کد تایید به تلگرام کاربر"""
    try:
        data = request.get_json(force=True) or {}
        telegram_username = (data.get("telegram_username") or "").strip().lower().replace("@", "")
        device_id = (data.get("device_id") or "").strip()
        
        log.info(f"📱 درخواست کد تایید برای: @{telegram_username}")
        
        if not telegram_username or len(telegram_username) < 3:
            return jsonify({"error": "نام کاربری تلگرام نامعتبر است"}), 400
        
        # ✅ چک کردن اینکه آیا کاربر قبلاً ثبت‌نام کامل کرده
        # (یعنی password_hash دارد = ثبت‌نام کامل شده)
        existing = storage._find_user_by({"username": telegram_username})
        if existing and existing.get("password_hash"):
            return jsonify({"error": "این کاربر قبلاً ثبت‌نام کرده است. از بخش ورود استفاده کنید."}), 400
        
        # پیدا کردن user از telegram_username (که mother_start ساخته)
        user_record = storage._find_user_by({"telegram_username": telegram_username})
        
        if not user_record:
            return jsonify({
                "error": "این username در ربات ما یافت نشد. لطفاً ابتدا ربات را در تلگرام استارت کنید."
            }), 404
        
        user_id = user_record.get("telegram_user_id") or user_record.get("uid")
        
        if not user_id:
            return jsonify({"error": "شناسه کاربر یافت نشد"}), 400
        
        ip = request.headers.get("X-Forwarded-For", request.remote_addr) or ""
        if "," in ip:
            ip = ip.split(",")[0].strip()
        
        can_register, reason = storage.check_registration_limit(ip, device_id)
        if not can_register:
            return jsonify({"error": reason}), 429
        
        code = f"{random.randint(100000, 999999)}"
        
        if not hasattr(storage, '_verification_codes'):
            storage._verification_codes = {}
        
        storage._verification_codes[telegram_username] = {
            "code": code,
            "user_id": user_id,
            "expires": time.time() + 600,
            "attempts": 0,
        }
        
        code_sent = False
        
        if mother_bot_instance and mother_bot_loop:
            try:
                message_text = (
                    f"🔐 <b>کد تایید GramSaz</b>\n\n"
                    f"سلام {user_record.get('telegram_first_name', 'دوست عزیز')}!\n\n"
                    f"کد ۶ رقمی شما:\n"
                    f"<code>{code}</code>\n\n"
                    f"⏰ این کد تا ۱۰ دقیقه معتبر است.\n"
                    f"🔒 این کد را با کسی به اشتراک نگذارید."
                )
                
                future = asyncio.run_coroutine_threadsafe(
                    mother_bot_instance.send_message(
                        chat_id=user_id,
                        text=message_text,
                        parse_mode=ParseMode.HTML
                    ),
                    mother_bot_loop
                )
                future.result(timeout=10)
                
                log.info(f"✅ کد تایید به @{telegram_username} (ID: {user_id}) در تلگرام ارسال شد")
                code_sent = True
                
            except Exception as e:
                log.error(f"❌ خطا در ارسال به تلگرام: {e}")
        else:
            log.warning("⚠️ ربات مادر آماده نیست")
        
        log.info(f"📱 کد تایید برای @{telegram_username}: {code}")
        
        return jsonify({
            "message": "کد تایید به تلگرام شما ارسال شد" + ("" if code_sent else " (کد در لاگ سرور نیز موجود است)"),
            "telegram_username": telegram_username,
            "code_sent": code_sent
        })
        
    except Exception as e:
        log.error(f"❌ خطا در send-code: {e}")
        return jsonify({"error": "خطای سرور"}), 500


# ══════════════════════════════════════════════════════════════════════
# /api/auth/* - سیستم احراز هویت
# ══════════════════════════════════════════════════════════════════════
@flask_app.post("/api/auth/register")
def api_register():
    """ثبت‌نام کاربر جدید"""
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = (data.get("password") or "").strip()
    full_name = (data.get("full_name") or "").strip()
    referral_code = (data.get("referral_code") or "").strip()
    device_id = (data.get("device_id") or "").strip()
    telegram_username = (data.get("telegram_username") or "").strip().lower().replace("@", "")
    verification_code = (data.get("verification_code") or "").strip()
    
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or ""
    if "," in ip:
        ip = ip.split(",")[0].strip()
    
    # اعتبارسنجی‌ها
    if not username or len(username) < 3:
        return jsonify({"error": "نام کاربری باید حداقل ۳ کاراکتر باشد"}), 400
    if not password or len(password) < 6:
        return jsonify({"error": "رمز عبور باید حداقل ۶ کاراکتر باشد"}), 400
    if not device_id:
        return jsonify({"error": "شناسه دستگاه الزامی است"}), 400
    if not telegram_username:
        return jsonify({"error": "username تلگرام الزامی است"}), 400
    if not verification_code:
        return jsonify({"error": "کد تایید الزامی است"}), 400
    
    # بررسی کد تایید
    if not hasattr(storage, '_verification_codes'):
        return jsonify({"error": "کد تایید منقضی شده"}), 400
    
    code_data = storage._verification_codes.get(telegram_username)
    if not code_data:
        return jsonify({"error": "کد تایید یافت نشد یا منقضی شده"}), 400
    
    if time.time() > code_data["expires"]:
        del storage._verification_codes[telegram_username]
        return jsonify({"error": "کد تایید منقضی شده است"}), 400
    
    code_data["attempts"] += 1
    if code_data["attempts"] > 5:
        del storage._verification_codes[telegram_username]
        return jsonify({"error": "تعداد تلاش‌ها بیش از حد مجاز"}), 400
    
    if code_data["code"] != verification_code:
        return jsonify({"error": f"کد تایید نادرست است ({code_data['attempts']}/5 تلاش)"}), 400
    
    del storage._verification_codes[telegram_username]
    log.info(f"✅ کد تایید برای @{telegram_username} تایید شد")

    try:
        user, token = storage.register_user(
            username, password, full_name, 
            referral_code=referral_code if referral_code else None,
            ip=ip,
            device_id=device_id
        )
        
        message = "ثبت‌نام موفق"
        if referral_code:
            message += " - ۵۰ کوین هدیه دریافت کردید!"
        
        return jsonify({
            "message": message,
            "user": user,
            "token": token
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.error(f"❌ خطا در ثبت‌نام: {e}")
        return jsonify({"error": "خطای سرور در ثبت‌نام"}), 500


@flask_app.post("/api/auth/login")
def api_login():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    
    if not username or not password:
        return jsonify({"error": "نام کاربری و رمز عبور الزامی است"}), 400
    
    try:
        user, token = storage.login_user(username, password)
        return jsonify({
            "message": "ورود موفق",
            "user": user,
            "token": token
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        log.error(f"❌ خطا در ورود: {e}")
        return jsonify({"error": "خطای سرور در ورود"}), 500


@flask_app.post("/api/auth/logout")
def api_logout():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if token:
        storage.logout_user(token)
    return jsonify({"message": "خروج موفق"})


@flask_app.get("/api/auth/me")
def api_auth_me():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return jsonify({"error": "توکن ارسال نشده"}), 401
    
    user = storage.get_user_by_token(token)
    if not user:
        return jsonify({"error": "توکن نامعتبر"}), 401
    
    return jsonify(user)


# ══════════════════════════════════════════════════════════════════════
# /api/admin/full-stats - آمار کامل پلتفرم (فقط ادمین)
# ══════════════════════════════════════════════════════════════════════
@flask_app.get("/api/admin/full-stats")
@admin_required
def api_admin_full_stats():
    """آمار کامل پلتفرم برای ادمین"""
    try:
        # ── آمار کاربران ──
        all_users = storage.get_all_users()
        total_users = len(all_users)
        
        # کاربران ثبت‌نام شده (با password_hash = ثبت‌نام کامل)
        registered_users = [u for u in all_users if u.get("password_hash")]
        total_registered = len(registered_users)
        
        # کاربران فعال (با auth_token یا از تلگرام)
        active_users = [u for u in all_users if u.get("auth_token") or u.get("telegram_user_id")]
        total_active = len(active_users)
        
        # کاربران ban شده
        banned_users = [u for u in all_users if u.get("is_banned")]
        total_banned = len(banned_users)
        
        # مجموع کوین‌های توزیع شده
        total_coins = sum(u.get("coins", 0) for u in all_users)
        
        # کاربران با رفرال
        users_with_referral = [u for u in all_users if u.get("referred_by")]
        total_referrals = len(users_with_referral)
        
        # ── آمار ربات‌ها ──
        all_bots = storage.get_all_bots()
        total_bots = len(all_bots)
        active_bots = [b for b in all_bots if b.get("is_active")]
        total_active_bots = len(active_bots)
        
        # مجموع پیام‌ها
        total_messages_received = sum(b.get("stats", {}).get("messages_received", 0) for b in all_bots)
        total_messages_sent = sum(b.get("stats", {}).get("messages_sent", 0) for b in all_bots)
        total_broadcasts = sum(b.get("stats", {}).get("broadcasts_sent", 0) for b in all_bots)
        
        # مجموع کاربران ربات‌ها
        total_bot_users = sum(len(b.get("bot_users", [])) for b in all_bots)
        
        # ── Top 10 ربات‌های برتر (بر اساس تعداد پیام) ──
        sorted_bots = sorted(
            all_bots, 
            key=lambda b: b.get("stats", {}).get("messages_received", 0),
            reverse=True
        )[:10]
        
        top_bots = []
        for b in sorted_bots:
            owner = storage.get_user(b.get("owner_uid"))
            top_bots.append({
                "bot_id": b.get("bot_id"),
                "username": b.get("bot_username", "—"),
                "owner_name": owner.get("full_name") if owner else "—",
                "owner_username": owner.get("username") if owner else "—",
                "messages": b.get("stats", {}).get("messages_received", 0),
                "users": len(b.get("bot_users", [])),
                "is_active": b.get("is_active", False),
            })
        
        # ── Top 10 کاربران برتر (بر اساس تعداد ربات) ──
        sorted_users = sorted(
            all_users,
            key=lambda u: len(u.get("bots_created", [])),
            reverse=True
        )[:10]
        
        top_users = []
        for u in sorted_users:
            if not u.get("bots_created"):
                continue
            top_users.append({
                "uid": u.get("uid"),
                "name": u.get("full_name", "—"),
                "username": u.get("username", "—"),
                "telegram_username": u.get("telegram_username", "—"),
                "bots_count": len(u.get("bots_created", [])),
                "coins": u.get("coins", 0),
                "referrals": u.get("referral_count", 0),
                "is_admin": u.get("is_admin", False),
                "is_banned": u.get("is_banned", False),
            })
        
        # ── Top 5 معرف‌ها (بر اساس تعداد زیرمجموعه) ──
        top_referrers = sorted(
            [u for u in all_users if u.get("referral_count", 0) > 0],
            key=lambda u: u.get("referral_count", 0),
            reverse=True
        )[:5]
        
        referrers_data = []
        for u in top_referrers:
            referrers_data.append({
                "name": u.get("full_name", "—"),
                "username": u.get("username", "—"),
                "referral_count": u.get("referral_count", 0),
                "coins": u.get("coins", 0),
            })
        
        # ── آمار رشد روزانه (از daily_stats ربات‌ها) ──
        daily_growth = {}
        for b in all_bots:
            for day, stats in b.get("daily_stats", {}).items():
                if day not in daily_growth:
                    daily_growth[day] = {"new_users": 0, "bots": 0}
                daily_growth[day]["new_users"] += stats.get("new_users", 0)
        
        # مرتب‌سازی بر اساس تاریخ و گرفتن ۷ روز اخیر
        sorted_days = sorted(daily_growth.keys(), reverse=True)[:7]
        recent_growth = [{"date": d, **daily_growth[d]} for d in sorted_days]
        
        return jsonify({
            "overview": {
                "total_users": total_users,
                "total_registered": total_registered,
                "total_active": total_active,
                "total_banned": total_banned,
                "total_bots": total_bots,
                "total_active_bots": total_active_bots,
                "total_messages_received": total_messages_received,
                "total_messages_sent": total_messages_sent,
                "total_broadcasts": total_broadcasts,
                "total_bot_users": total_bot_users,
                "total_coins": total_coins,
                "total_referrals": total_referrals,
            },
            "top_bots": top_bots,
            "top_users": top_users,
            "top_referrers": referrers_data,
            "daily_growth": recent_growth,
        })
        
    except Exception as e:
        log.error(f"❌ خطا در دریافت آمار ادمین: {e}")
        import traceback
        log.error(traceback.format_exc())
        return jsonify({"error": "خطا در دریافت آمار"}), 500

# ══════════════════════════════════════════════════════════════════════
# /api/coins/* - سیستم کوین
# ══════════════════════════════════════════════════════════════════════
@flask_app.get("/api/coins/referral")
@auth_required
def api_coins_referral():
    info = storage.get_user_referral_info(g.current_user["uid"])
    return jsonify(info)

# ══════════════════════════════════════════════════════════════════════
# /api/upload-media - آپلود انواع رسانه به Catbox.moe
# ══════════════════════════════════════════════════════════════════════
@flask_app.post("/api/upload-media")
@auth_required
def api_upload_media():
    """آپلود رسانه (عکس/ویدیو/صوت/فایل) به Catbox.moe"""
    try:
        log.info(f"📤 درخواست آپلود رسانه دریافت شد")
        
        if 'file' not in request.files:
            return jsonify({"error": "فایلی ارسال نشده"}), 400
        
        file = request.files['file']
        
        if not file or not file.filename:
            return jsonify({"error": "نام فایل خالی است"}), 400
        
        original_name = file.filename
        ext = ''
        if '.' in original_name:
            ext = original_name.rsplit('.', 1)[1].lower()
        
        # تشخیص نوع media از روی extension و MIME
        media_type = None
        
        # تعریف نگاشت extension به media type
        type_map = {
            # Photo
            'jpg': 'photo', 'jpeg': 'photo', 'png': 'photo', 'webp': 'photo',
            # Video
            'mp4': 'video', 'webm': 'video', 'mov': 'video', 'avi': 'video', 'mkv': 'video',
            # Audio
            'mp3': 'audio', 'ogg': 'audio', 'm4a': 'audio', 'wav': 'audio', 'flac': 'audio',
            # Animation
            'gif': 'animation',
            # Document
            'pdf': 'document', 'zip': 'document', 'rar': 'document', '7z': 'document',
            'doc': 'document', 'docx': 'document', 'xls': 'document', 'xlsx': 'document',
            'ppt': 'document', 'pptx': 'document', 'txt': 'document', 'csv': 'document',
        }
        
        if ext in type_map:
            media_type = type_map[ext]
        else:
            # اگر extension ناشناخته بود، به عنوان document در نظر بگیر
            media_type = 'document'
        
        # محدودیت حجم بر اساس نوع
        max_size_mb = 10 if media_type == 'photo' else 50
        max_size_bytes = max_size_mb * 1024 * 1024
        
        # خواندن فایل
        file_data = file.read()
        size = len(file_data)
        
        if size > max_size_bytes:
            return jsonify({
                "error": f"حجم فایل باید کمتر از {max_size_mb} مگابایت باشد"
            }), 400
        
        if size < 100:
            return jsonify({"error": "فایل خیلی کوچک است"}), 400
        
        # MIME type
        mime_type = file.content_type or 'application/octet-stream'
        
        # نام فایل ساده (بدون کاراکتر عجیب)
        safe_filename = f"gramsaz_{int(time.time())}_{secrets.token_hex(4)}.{ext}"
        
        log.info(f"📤 آپلود {media_type} به Catbox.moe ({size} bytes)...")
        
        import requests as req_lib
        
        response = req_lib.post(
            'https://catbox.moe/user/api.php',
            data={'reqtype': 'fileupload'},
            files={'fileToUpload': (safe_filename, file_data, mime_type)},
            timeout=60  # timeout بیشتر برای فایل‌های بزرگ
        )
        
        log.info(f"   Catbox Status: {response.status_code}")
        
        if response.status_code != 200:
            return jsonify({"error": f"خطا در آپلود (کد {response.status_code})"}), 500
        
        file_url = response.text.strip()
        
        if not file_url.startswith('https://'):
            log.error(f"❌ پاسخ نامعتبر از Catbox: {file_url}")
            return jsonify({"error": "پاسخ نامعتبر از سرور"}), 500
        
        log.info(f"✅ رسانه آپلود شد: {file_url}")
        
        # اطلاعات اضافی برای نمایش در UI
        type_icons = {
            'photo': '🖼', 'video': '🎬', 'audio': '🎵',
            'animation': '🎞', 'document': '📎'
        }
        type_names = {
            'photo': 'تصویر', 'video': 'ویدیو', 'audio': 'صوت',
            'animation': 'انیمیشن', 'document': 'فایل'
        }
        
        return jsonify({
            "url": file_url,
            "filename": original_name,
            "size": size,
            "type": media_type,
            "icon": type_icons.get(media_type, '📎'),
            "type_name": type_names.get(media_type, 'فایل')
        })
        
    except Exception as e:
        log.error(f"❌ خطا در آپلود: {type(e).__name__}: {e}")
        import traceback
        log.error(traceback.format_exc())
        return jsonify({"error": f"خطای سرور: {str(e)}"}), 500

# ══════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════
def main():
    factory.start_all_active()

    t = threading.Thread(target=run_mother_bot, daemon=True)
    t.start()

    log.info(f"🌐 Flask روی http://{API_HOST}:{API_PORT}")
    flask_app.run(host=API_HOST, port=API_PORT, threaded=True)


if __name__ == "__main__":
    main()
