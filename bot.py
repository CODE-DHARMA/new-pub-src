#!/usr/bin/env python3
"""
Multi-Bot Restricted Content Saver — Pyrogram
Render 24/7 Free Tier Ready (with built-in health server)
"""

import asyncio
import json
import os
import re
import logging
import secrets
import string
import io
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import web

import qrcode
from PIL import Image
from pyzbar.pyzbar import decode as decode_qr

from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
)
from pyrogram.errors import (
    FloodWait, ChatAdminRequired, UserNotParticipant,
    ChannelPrivate, PeerIdInvalid, UsernameNotOccupied, UsernameInvalid,
    MessageNotModified, RPCError,
)
from pyrogram.enums import ChatType, ChatMemberStatus

# ─── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger(__name__)

# ─── Bot Configurations (env-var friendly) ──────────────────────
BOT_CONFIGS = [
    {
        "token": os.environ.get(
            "BOT1_TOKEN",
            "8165913045:AAEgBlnj2mDd6mt1gkOtHaZW93YuCflX2Wc"
        ),
        "api_id": int(os.environ.get("BOT1_API_ID", "27094603")),
        "api_hash": os.environ.get(
            "BOT1_API_HASH", "42948630a2893f4002baef885407f326"
        ),
        "owner_id": int(os.environ.get("BOT1_OWNER_ID", "8116685806")),
        "data_dir": "./data/bot1",
        "session_name": "./data/bot1/session",
    },
    {
        "token": os.environ.get(
            "BOT2_TOKEN",
            "8758745998:AAG984VHnqanzRx2a5vOrU0xOT7Eyt_O9OI"
        ),
        "api_id": int(os.environ.get("BOT2_API_ID", "27094603")),
        "api_hash": os.environ.get(
            "BOT2_API_HASH", "42948630a2893f4002baef885407f326"
        ),
        "owner_id": int(os.environ.get("BOT2_OWNER_ID", "987260932")),
        "data_dir": "./data/bot2",
        "session_name": "./data/bot2/session",
    },
]

# ─── Constants ─────────────────────────────────────────────────
ALLOWED_USERS: List[int] = []
BULK_DELAY = 2.5
FREE_DAILY_LIMIT = 50
SRC_TRACKER_CHANNEL = int(os.environ.get("SRC_TRACKER_CHANNEL", "-1003820634243"))
ENABLE_SRC_TRACKING = os.environ.get("ENABLE_SRC_TRACKING", "true").lower() == "true"

PREMIUM_PRICES = {
    "day":   "₹3",
    "week":  "₹20",
    "month": "₹80",
    "year":  "₹900",
}

PLAN_PRICES_INT = {"day": 3, "week": 20, "month": 80, "year": 900}
PLAN_HOURS = {"6h": 6, "day": 24, "week": 168, "month": 720, "year": 8760}

IST = ZoneInfo("Asia/Kolkata")


# ═══════════════════════════════════════════════════════════════
# BOT APP
# ═══════════════════════════════════════════════════════════════
class BotApp:
    def __init__(self, token: str, api_id: int, api_hash: str, owner_id: int,
                 data_dir: str, session_name: str):
        self.token = token
        self.api_id = api_id
        self.api_hash = api_hash
        self.owner_id = owner_id
        self.data_dir = Path(data_dir)
        self.session_name = session_name

        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.app = Client(
            self.session_name,
            api_id=self.api_id,
            api_hash=self.api_hash,
            bot_token=self.token,
            sleep_threshold=60,
            workers=4,
        )
        self._bot_username: Optional[str] = None

        self.pending_bulk: Dict[int, Dict] = {}
        self.cancel_flags: Dict[int, bool] = {}
        self.pause_flags: Dict[int, bool] = {}
        self.active_tasks: Dict[int, asyncio.Task] = {}
        self.inline_state: Dict[int, Dict] = {}
        self.user_force_links: Dict[int, str] = {}

        self._json_locks: Dict[Path, asyncio.Lock] = {}

        self.SETTINGS_FILE    = self.data_dir / "settings.json"
        self.TRACK_FILE       = self.data_dir / "track_log.json"
        self.PREMIUM_FILE     = self.data_dir / "premium.json"
        self.USAGE_FILE       = self.data_dir / "usage.json"
        self.ADMIN_FILE       = self.data_dir / "admin_cfg.json"
        self.PROMO_FILE       = self.data_dir / "promos.json"
        self.SRC_LOG_FILE     = self.data_dir / "src_log.json"
        self.BAN_FILE         = self.data_dir / "banned.json"
        self.CHAT_LOG_FILE    = self.data_dir / "chat_log.json"
        self.BROADCAST_FILE   = self.data_dir / "broadcast_chats.json"
        self.CLAIMS_FILE      = self.data_dir / "claims.json"
        self.FREE_CLAIMS_FILE = self.data_dir / "free_claims.json"

        self._register_handlers()

    # ─── JSON helpers with locks ──────────────────────────────
    def _get_lock(self, path: Path) -> asyncio.Lock:
        if path not in self._json_locks:
            self._json_locks[path] = asyncio.Lock()
        return self._json_locks[path]

    async def _load_json(self, path: Path) -> Any:
        async with self._get_lock(path):
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except (json.JSONDecodeError, OSError):
                    log.error(f"Corrupt JSON file {path}, using empty")
                    return {}
            return {}

    async def _save_json(self, path: Path, data: Any) -> None:
        async with self._get_lock(path):
            try:
                tmp = path.with_suffix(path.suffix + ".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                tmp.replace(path)
            except OSError as e:
                log.error(f"Failed to write {path}: {e}")

    # ─── Free Claims ──────────────────────────────────────────
    async def _load_free_claims(self) -> Dict:
        return await self._load_json(self.FREE_CLAIMS_FILE)

    async def _save_free_claims(self, data: Dict) -> None:
        await self._save_json(self.FREE_CLAIMS_FILE, data)

    async def get_free_claim_count(self, uid: int) -> int:
        data = await self._load_free_claims()
        key = str(uid)
        if key not in data:
            return 0
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=24)
        valid = [ts for ts in data[key] if datetime.fromisoformat(ts) > cutoff]
        return len(valid)

    async def can_claim_free(self, uid: int) -> bool:
        if uid == self.owner_id:
            return True
        limit = await self.get_free_claims_limit()
        count = await self.get_free_claim_count(uid)
        return count < limit

    async def record_free_claim(self, uid: int) -> None:
        data = await self._load_free_claims()
        key = str(uid)
        now_iso = datetime.now(timezone.utc).isoformat()
        if key not in data:
            data[key] = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        data[key] = [ts for ts in data[key] if datetime.fromisoformat(ts) > cutoff]
        data[key].append(now_iso)
        await self._save_free_claims(data)

    async def get_free_claims_limit(self) -> int:
        cfg = await self.get_admin_cfg()
        return cfg.get("free_claims_limit", 3)

    # ─── Claims ───────────────────────────────────────────────
    async def _load_claims(self) -> Dict:
        return await self._load_json(self.CLAIMS_FILE)

    async def _save_claims(self, data: Dict) -> None:
        await self._save_json(self.CLAIMS_FILE, data)

    async def _purge_expired_claims(self, claims: Dict) -> Dict:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=24)
        out = {}
        for code, rec in claims.items():
            try:
                if datetime.fromisoformat(rec["created_at"]) > cutoff or rec.get("used"):
                    out[code] = rec
            except Exception:
                continue
        return out

    async def generate_claim(self, uid: int) -> str:
        claims = await self._load_claims()
        claims = await self._purge_expired_claims(claims)
        for code, rec in list(claims.items()):
            if rec.get("user_id") == uid and not rec.get("used"):
                rec["invalidated"] = True
        while True:
            code = secrets.token_hex(5).upper()
            if code not in claims:
                break
        claims[code] = {
            "user_id": uid,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "used": False,
            "used_by": None,
            "granted_at": None,
            "invalidated": False,
        }
        await self._save_claims(claims)
        return code

    async def verify_and_use_claim(self, code: str, uid: int) -> Tuple[bool, str]:
        claims = await self._load_claims()
        rec = claims.get(code)
        if not rec:
            return False, "❌ Invalid or expired claim code."
        if rec.get("invalidated"):
            return False, "❌ This claim link has been superseded. Please generate a new one."
        if rec.get("used"):
            return False, "⚠️ This claim has already been used."
        created = datetime.fromisoformat(rec["created_at"])
        if datetime.now(timezone.utc) - created > timedelta(hours=1):
            rec["invalidated"] = True
            claims[code] = rec
            await self._save_claims(claims)
            return False, "❌ This claim link has expired (valid 1 hour). Please get a new one."
        rec["used"] = True
        rec["used_by"] = uid
        rec["granted_at"] = datetime.now(timezone.utc).isoformat()
        claims[code] = rec
        await self._save_claims(claims)
        expiry = await self.grant_premium(uid, 6, "6 Hours (Free)")
        expiry_str = expiry.strftime("%Y-%m-%d %H:%M UTC")
        return True, (
            f"🎉 **Free 6-Hour Premium Activated!**\n\n"
            f"✅ Your premium has been extended by **6 hours**.\n"
            f"⏰ New expiry: `{expiry_str}`\n\n"
            f"📌 You can claim this again **anytime**.\n"
            f"Enjoy! 🚀"
        )

    # ─── Shortener ────────────────────────────────────────────
    async def shorten_url(self, long_url: str) -> Optional[str]:
        cfg = await self.get_admin_cfg()
        api_url = cfg.get("shortener_api_url", "").strip()
        api_key = cfg.get("shortener_api_key", "").strip()

        if not api_url or not api_key:
            return await self._shorten_vgd(long_url)

        method = cfg.get("shortener_method", "auto").upper()
        param_name = cfg.get("shortener_param_name", "url")
        response_key = cfg.get("shortener_response_key", "")
        auth_type = cfg.get("shortener_auth_type", "bearer").lower()
        header_name = cfg.get("shortener_header_name", "Authorization")

        if method == "AUTO" or method not in ("GET", "POST"):
            method = "GET" if ("format=json" in api_url or "shrtslug" in api_url) else "POST"

        for attempt in range(1, 4):
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    headers = {}
                    if auth_type == "bearer":
                        headers["Authorization"] = f"Bearer {api_key}"
                    elif auth_type == "apikey":
                        headers[header_name] = api_key

                    if method == "GET":
                        params = {"api": api_key, param_name: long_url}
                        if "format=json" not in api_url:
                            params["format"] = "json"
                        async with session.get(api_url, params=params, headers=headers) as resp:
                            raw = await resp.text()
                            if resp.status != 200:
                                log.error(f"Shortener returned {resp.status}: {raw[:200]}")
                                if resp.status == 429:
                                    await asyncio.sleep(int(resp.headers.get("Retry-After", 2)))
                                continue
                            try:
                                data = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            short = self._extract_short_url(data, response_key)
                            if short and short.startswith(("http://", "https://")):
                                return short
                    else:
                        headers["Content-Type"] = "application/json"
                        payload = {param_name: long_url}
                        async with session.post(api_url, json=payload, headers=headers) as resp:
                            raw = await resp.text()
                            if resp.status != 200:
                                log.error(f"Shortener returned {resp.status}: {raw[:200]}")
                                if resp.status == 429:
                                    await asyncio.sleep(int(resp.headers.get("Retry-After", 2)))
                                continue
                            try:
                                data = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            short = self._extract_short_url(data, response_key)
                            if short and short.startswith(("http://", "https://")):
                                return short
            except (asyncio.TimeoutError, aiohttp.ClientError, json.JSONDecodeError) as e:
                log.error(f"Shortener attempt {attempt} failed: {e}")
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
        log.error(f"Shortener failed after 3 attempts: {long_url}")
        return None

    async def _shorten_vgd(self, long_url: str) -> Optional[str]:
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                params = {"url": long_url, "format": "json"}
                async with session.get("https://v.gd/create.php", params=params) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return data.get("shorturl")
        except Exception as e:
            log.error(f"v.gd error: {e}")
            return None

    def _extract_short_url(self, data: Dict, response_key: str = "") -> Optional[str]:
        if response_key and response_key in data:
            val = data[response_key]
            if isinstance(val, str):
                return val
            if isinstance(val, dict) and "shorten_url" in val:
                return val["shorten_url"]
        if isinstance(data.get("result"), dict):
            return data["result"].get("shorten_url")
        for key in ("shortenedUrl", "short", "short_url", "url", "shorten_url", "result"):
            val = data.get(key)
            if val and isinstance(val, str):
                return val
        return None

    # ─── Bot username ─────────────────────────────────────────
    async def get_bot_username(self) -> str:
        if not self._bot_username:
            me = await self.app.get_me()
            self._bot_username = me.username
        return self._bot_username

    # ─── User settings ────────────────────────────────────────
    async def load_settings(self) -> Dict:
        return await self._load_json(self.SETTINGS_FILE)

    async def save_settings(self, data: Dict) -> None:
        await self._save_json(self.SETTINGS_FILE, data)

    async def get_user_cfg(self, uid: int) -> Dict:
        data = await self.load_settings()
        default = {
            "channel": None, "topic": None, "replacements": {},
            "deletions": [], "prefix": "", "suffix": "", "delay": None,
        }
        stored = data.get(str(uid), {})
        default.update(stored)
        return default

    async def set_user_cfg(self, uid: int, cfg: Dict) -> None:
        data = await self.load_settings()
        data[str(uid)] = cfg
        await self.save_settings(data)

    # ─── Premium ──────────────────────────────────────────────
    async def _load_premium(self) -> Dict:
        return await self._load_json(self.PREMIUM_FILE)

    async def _save_premium(self, data: Dict) -> None:
        await self._save_json(self.PREMIUM_FILE, data)

    async def is_premium(self, uid: int) -> bool:
        if uid == self.owner_id:
            return True
        data = await self._load_premium()
        rec = data.get(str(uid))
        if not rec:
            return False
        try:
            expiry = datetime.fromisoformat(rec["expiry"])
            return datetime.now(timezone.utc) < expiry
        except Exception:
            return False

    async def get_premium_expiry(self, uid: int) -> Optional[str]:
        if uid == self.owner_id:
            return "♾️ Admin (lifetime)"
        data = await self._load_premium()
        rec = data.get(str(uid))
        if not rec:
            return None
        try:
            expiry = datetime.fromisoformat(rec["expiry"])
            if datetime.now(timezone.utc) >= expiry:
                return None
            return expiry.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            return None

    async def grant_premium(self, uid: int, duration_hours: int, plan_label: str = "") -> datetime:
        data = await self._load_premium()
        now = datetime.now(timezone.utc)
        rec = data.get(str(uid))
        if rec:
            try:
                current_expiry = datetime.fromisoformat(rec["expiry"])
                base = max(now, current_expiry)
            except Exception:
                base = now
        else:
            base = now
        new_expiry = base + timedelta(hours=duration_hours)
        data[str(uid)] = {
            "expiry": new_expiry.isoformat(),
            "plan": plan_label,
            "granted_at": now.isoformat(),
        }
        await self._save_premium(data)
        return new_expiry

    async def revoke_premium(self, uid: int) -> None:
        data = await self._load_premium()
        data.pop(str(uid), None)
        await self._save_premium(data)

    # ─── Daily usage ──────────────────────────────────────────
    async def _load_usage(self) -> Dict:
        return await self._load_json(self.USAGE_FILE)

    async def _save_usage(self, data: Dict) -> None:
        await self._save_json(self.USAGE_FILE, data)

    def _today_str(self) -> str:
        return datetime.now(IST).strftime("%Y-%m-%d")

    async def get_daily_usage(self, uid: int) -> int:
        data = await self._load_usage()
        rec = data.get(str(uid), {})
        if rec.get("date") != self._today_str():
            return 0
        return rec.get("count", 0)

    async def increment_usage(self, uid: int, amount: int = 1) -> int:
        data = await self._load_usage()
        today = self._today_str()
        rec = data.get(str(uid), {})
        if rec.get("date") != today:
            rec = {"date": today, "count": 0}
        rec["count"] = rec.get("count", 0) + amount
        data[str(uid)] = rec
        await self._save_usage(data)
        return rec["count"]

    async def check_limit(self, uid: int, needed: int = 1) -> Tuple[bool, int]:
        if await self.is_premium(uid):
            return True, FREE_DAILY_LIMIT
        used = await self.get_daily_usage(uid)
        remaining = max(0, FREE_DAILY_LIMIT - used)
        return remaining >= needed, remaining

    # ─── Admin config ─────────────────────────────────────────
    async def _load_admin_cfg(self) -> Dict:
        return await self._load_json(self.ADMIN_FILE)

    async def _save_admin_cfg(self, data: Dict) -> None:
        await self._save_json(self.ADMIN_FILE, data)

    def _admin_defaults(self) -> Dict:
        return {
            "upi_id": "", "phone": "", "qr_file_id": "", "qr_local_path": "",
            "extracted_upi": "", "url_shortener": "", "razorpay_key": "",
            "razorpay_secret": "", "welcome_text": "", "welcome_image": "",
            "welcome_video": "", "backup_channel_id": None, "global_delay": None,
            "shortener_api_url": "", "shortener_api_key": "", "shortener_domain": "",
            "free_6h_enabled": False, "free_claims_limit": 3,
            "shortener_method": "auto", "shortener_param_name": "url",
            "shortener_response_key": "", "shortener_auth_type": "bearer",
            "shortener_header_name": "Authorization",
        }

    async def get_admin_cfg(self) -> Dict:
        default = self._admin_defaults()
        stored = await self._load_admin_cfg()
        default.update(stored)
        return default

    async def update_admin_cfg(self, updates: Dict) -> None:
        cfg = await self.get_admin_cfg()
        cfg.update(updates)
        await self._save_admin_cfg(cfg)

    def get_admin_cfg_sync(self) -> Dict:
        default = self._admin_defaults()
        if self.ADMIN_FILE.exists():
            try:
                with open(self.ADMIN_FILE, "r", encoding="utf-8") as f:
                    default.update(json.load(f))
            except Exception:
                pass
        return default

    # ─── QR helpers ───────────────────────────────────────────
    def extract_upi_from_qr_image(self, image_path: str) -> Optional[str]:
        try:
            img = Image.open(image_path)
            decoded = decode_qr(img)
            for obj in decoded:
                data = obj.data.decode('utf-8')
                if 'upi://' in data.lower() or 'pa=' in data.lower():
                    m = re.search(r'pa=([^&\s]+)', data, re.IGNORECASE)
                    if m:
                        return m.group(1)
                if '@' in data and 'upi://' not in data.lower():
                    return data.strip()
        except Exception as e:
            log.error(f"Error extracting UPI from QR: {e}")
        return None

    def generate_payment_qr(self, plan_key: str, upi_id: str) -> Optional[bytes]:
        amount = PLAN_PRICES_INT.get(plan_key)
        if not amount or not upi_id:
            return None
        plan_labels = {"day": "1 Day", "week": "1 Week", "month": "1 Month", "year": "1 Year"}
        label = plan_labels.get(plan_key, plan_key)
        cfg = self.get_admin_cfg_sync()
        payee = cfg.get("phone") or "Bot Premium"
        upi_url = (
            f"upi://pay?pa={upi_id}"
            f"&pn={payee.replace(' ', '%20')}"
            f"&am={amount}&cu=INR&tn=BotPremium-{plan_key}"
        )
        try:
            qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M,
                               box_size=10, border=4)
            qr.add_data(upi_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf.read()
        except Exception as e:
            log.error(f"Error generating QR: {e}")
            return None

    # ─── Promos ───────────────────────────────────────────────
    async def _load_promos(self) -> Dict:
        return await self._load_json(self.PROMO_FILE)

    async def _save_promos(self, data: Dict) -> None:
        await self._save_json(self.PROMO_FILE, data)

    async def create_promo(self, code: str, duration_hours: int, plan_label: str, max_uses: int = 1) -> Dict:
        data = await self._load_promos()
        code = code.upper()
        data[code] = {
            "duration_hours": duration_hours,
            "plan_label": plan_label,
            "max_uses": max_uses,
            "used_by": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._save_promos(data)
        return data[code]

    async def redeem_promo(self, code: str, uid: int) -> Tuple[bool, str]:
        data = await self._load_promos()
        rec = data.get(code.upper())
        if not rec:
            return False, "❌ Invalid promo code."
        if uid in rec.get("used_by", []):
            return False, "❌ You have already used this promo code."
        if len(rec["used_by"]) >= rec.get("max_uses", 1):
            return False, "❌ This promo code has reached its usage limit."
        rec["used_by"].append(uid)
        await self._save_promos(data)
        expiry = await self.grant_premium(uid, rec["duration_hours"], rec["plan_label"])
        return True, (
            f"🎉 Promo code redeemed!\n\n"
            f"✅ Plan: {rec['plan_label']}\n"
            f"⏰ Valid until: {expiry.strftime('%Y-%m-%d %H:%M UTC')}"
        )

    async def delete_promo(self, code: str) -> bool:
        data = await self._load_promos()
        if code.upper() in data:
            del data[code.upper()]
            await self._save_promos(data)
            return True
        return False

    def generate_promo_code(self, length: int = 8) -> str:
        chars = string.ascii_uppercase + string.digits
        return "".join(secrets.choice(chars) for _ in range(length))

    # ─── Tracking ─────────────────────────────────────────────
    async def _load_track(self) -> List:
        data = await self._load_json(self.TRACK_FILE)
        return data if isinstance(data, list) else []

    async def _save_track(self, entries: List) -> None:
        await self._save_json(self.TRACK_FILE, entries)

    async def track(self, user, action: str, source=None, dest=None, msg_id=None,
                    quantity=None, copied=None, skipped=None, failed=None, detail: str = ""):
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "user_id": user.id if user else None,
            "username": f"@{user.username}" if (user and user.username) else None,
            "full_name": " ".join(filter(None, [user.first_name, user.last_name])) if user else None,
            "action": action,
            "source": str(source) if source is not None else None,
            "dest": str(dest) if dest is not None else None,
            "msg_id": msg_id, "quantity": quantity, "copied": copied,
            "skipped": skipped, "failed": failed, "detail": detail or None,
        }
        entry = {k: v for k, v in entry.items() if v is not None}
        entries = await self._load_track()
        entries.append(entry)
        await self._save_track(entries)

    # ─── Source logs ──────────────────────────────────────────
    async def _load_src_log(self) -> List:
        data = await self._load_json(self.SRC_LOG_FILE)
        return data if isinstance(data, list) else []

    async def _save_src_log(self, entries: List) -> None:
        await self._save_json(self.SRC_LOG_FILE, entries)

    async def log_source_link(self, user, source_chat, source_link: str, msg_id: int, quantity: int):
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "user_id": user.id if user else None,
            "username": f"@{user.username}" if (user and user.username) else None,
            "full_name": " ".join(filter(None, [user.first_name, user.last_name])) if user else None,
            "source_chat": str(source_chat),
            "source_link": source_link,
            "start_msg_id": msg_id,
            "quantity": quantity,
        }
        entries = await self._load_src_log()
        entries.append(entry)
        await self._save_src_log(entries)

    # ─── Bans ─────────────────────────────────────────────────
    async def _load_bans(self) -> Dict:
        return await self._load_json(self.BAN_FILE)

    async def _save_bans(self, data: Dict) -> None:
        await self._save_json(self.BAN_FILE, data)

    async def is_banned(self, uid: int) -> bool:
        if uid == self.owner_id:
            return False
        data = await self._load_bans()
        return str(uid) in data

    async def ban_user(self, uid: int, reason: str = "", banned_by: int = None) -> bool:
        if uid == self.owner_id:
            return False
        data = await self._load_bans()
        data[str(uid)] = {
            "reason": reason or "No reason provided",
            "banned_at": datetime.now(timezone.utc).isoformat(),
            "banned_by": banned_by,
        }
        await self._save_bans(data)
        return True

    async def unban_user(self, uid: int) -> bool:
        data = await self._load_bans()
        if str(uid) in data:
            del data[str(uid)]
            await self._save_bans(data)
            return True
        return False

    async def get_ban_info(self, uid: int) -> Optional[Dict]:
        data = await self._load_bans()
        return data.get(str(uid))

    # ─── Chat log ─────────────────────────────────────────────
    async def _load_chat_log(self) -> Dict:
        return await self._load_json(self.CHAT_LOG_FILE)

    async def _save_chat_log(self, data: Dict) -> None:
        await self._save_json(self.CHAT_LOG_FILE, data)

    async def save_chat_message(self, uid: int, msg_data: Dict) -> None:
        data = await self._load_chat_log()
        key = str(uid)
        if key not in data:
            data[key] = []
        data[key].append(msg_data)
        if len(data[key]) > 200:
            data[key] = data[key][-200:]
        await self._save_chat_log(data)

    async def get_chat_log(self, uid: int) -> List:
        data = await self._load_chat_log()
        return data.get(str(uid), [])

    async def clear_chat_log(self, uid: int) -> None:
        data = await self._load_chat_log()
        data.pop(str(uid), None)
        await self._save_chat_log(data)

    # ─── Broadcast ────────────────────────────────────────────
    async def _load_broadcast_chats(self) -> List:
        data = await self._load_json(self.BROADCAST_FILE)
        if isinstance(data, dict):
            return data.get("chats", [])
        return []

    async def _save_broadcast_chats(self, chat_ids: List) -> None:
        await self._save_json(self.BROADCAST_FILE, {"chats": list(set(chat_ids))})

    async def add_broadcast_chat(self, chat_id: int) -> None:
        chats = await self._load_broadcast_chats()
        if chat_id not in chats:
            chats.append(chat_id)
            await self._save_broadcast_chats(chats)

    async def remove_broadcast_chat(self, chat_id: int) -> None:
        chats = await self._load_broadcast_chats()
        if chat_id in chats:
            chats.remove(chat_id)
            await self._save_broadcast_chats(chats)

    # ─── Text utils ───────────────────────────────────────────
    def apply_rules(self, text: Optional[str], cfg: Dict) -> str:
        if not text:
            return ""
        text = str(text)
        for word in cfg.get("deletions", []):
            try:
                text = re.sub(re.escape(word), "", text, flags=re.IGNORECASE)
            except Exception:
                pass
        for old, new in cfg.get("replacements", {}).items():
            try:
                text = re.sub(re.escape(old), lambda _: new, text, flags=re.IGNORECASE)
            except Exception:
                pass
        return cfg.get("prefix", "") + text.strip() + cfg.get("suffix", "")

    def parse_tme_link(self, text: str):
        m = re.search(r"https?://t\.me/(?:c/)?([^/\s]+)/(\d+)", text)
        if not m:
            return None
        chat_ref = m.group(1)
        msg_id = int(m.group(2))
        if chat_ref.isdigit():
            chat_ref = int(f"-100{chat_ref}")
        return chat_ref, msg_id

    def extract_full_link(self, text: str) -> Optional[str]:
        m = re.search(r"(https?://t\.me/(?:c/)?[^/\s]+/\d+)", text)
        return m.group(1) if m else None

    # ─── Force-join ───────────────────────────────────────────
    async def is_member(self, uid: int, channel_id: int) -> bool:
        try:
            await self.app.get_chat_member(int(channel_id), uid)
            return True
        except (UserNotParticipant, ChatAdminRequired, ChannelPrivate):
            return False
        except Exception as e:
            log.error(f"Force-join check failed: {e}")
            return False

    async def revoke_and_create_link(self, uid: int, channel_id: int) -> Optional[str]:
        if uid in self.user_force_links:
            old = self.user_force_links.pop(uid)
            try:
                await self.app.revoke_chat_invite_link(channel_id, old)
            except Exception:
                pass
        try:
            invite = await self.app.create_chat_invite_link(channel_id, member_limit=0)
            link = invite.invite_link
            self.user_force_links[uid] = link
            return link
        except Exception as e:
            log.error(f"Failed to create invite link for {channel_id}: {e}")
            return None

    async def enforce_force_join(self, msg_or_cb, uid: int) -> bool:
        cfg = await self.get_admin_cfg()
        channel_id = cfg.get("backup_channel_id")
        if not channel_id:
            return True
        if await self.is_member(uid, channel_id):
            if uid in self.user_force_links:
                old = self.user_force_links.pop(uid)
                try:
                    await self.app.revoke_chat_invite_link(channel_id, old)
                except Exception:
                    pass
            return True
        link = await self.revoke_and_create_link(uid, channel_id)
        if not link:
            await msg_or_cb.reply_text("⚠️ Could not generate an invite link. Please contact the admin.")
            return False
        await msg_or_cb.reply_text(
            f"🚫 **You must join our backup channel to use this bot.**\n\n"
            f"👉 [Join Channel]({link})\n\n"
            "After joining, press the button below or send /start again.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ I have joined", callback_data="check_force_join")
            ]])
        )
        return False

    async def validate_and_set_backup_channel(self, input_text: str) -> Tuple[bool, str]:
        if input_text.lower() == "clear":
            await self.update_admin_cfg({"backup_channel_id": None})
            return True, "✅ Force join disabled."
        if input_text.lstrip("-").isdigit():
            chat_id = int(input_text)
        else:
            try:
                chat = await self.app.get_chat(input_text)
                chat_id = chat.id
            except (UsernameInvalid, UsernameNotOccupied, PeerIdInvalid) as e:
                return False, f"❌ Invalid channel/group: `{e}`"
            except Exception as e:
                return False, f"❌ Could not resolve chat: {e}"
        try:
            chat = await self.app.get_chat(chat_id)
            if chat.type not in (ChatType.CHANNEL, ChatType.SUPERGROUP, ChatType.GROUP):
                return False, "❌ Backup channel must be a channel/supergroup/group."
        except Exception as e:
            return False, f"❌ Could not fetch chat info: {e}"
        try:
            me = await self.app.get_me()
            member = await self.app.get_chat_member(chat_id, me.id)
            if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                return False, "❌ Bot must be admin in this chat."
            if member.privileges and not member.privileges.can_invite_users:
                return False, "❌ Bot needs Invite Users permission."
        except Exception as e:
            return False, f"❌ Bot admin check failed: {e}"
        await self.update_admin_cfg({"backup_channel_id": chat_id})
        return True, f"✅ Force join set to `{chat.title}` (`{chat_id}`)."

    # ─── SRC Tracker ──────────────────────────────────────────
    async def send_src_tracker(self, user, source_chat, msg_id, quantity,
                               source_link="", action_type="BULK COPY"):
        if not ENABLE_SRC_TRACKING:
            return
        try:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            uname = f"@{user.username}" if user.username else f"ID:{user.id}"
            fname = " ".join(filter(None, [user.first_name, user.last_name])) or "Unknown"
            plan = "⭐ Premium" if await self.is_premium(user.id) else "🆓 Free"

            channel_title = "Unknown"
            channel_username = ""
            try:
                chat = await self.app.get_chat(source_chat)
                channel_title = chat.title or "Unknown"
                channel_username = f"@{chat.username}" if chat.username else ""
            except Exception as e:
                log.warning(f"Could not get chat info: {e}")
                channel_title = str(source_chat)

            if not source_link and source_chat:
                if isinstance(source_chat, int):
                    chat_id_str = str(source_chat).replace("-100", "")
                    source_link = f"https://t.me/c/{chat_id_str}/{msg_id}"
                else:
                    source_link = f"https://t.me/{source_chat}/{msg_id}"

            text = (
                f"📡 **SRC TRACKER LOG**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔔 **Action:** {action_type}\n"
                f"👤 **User:** {fname} ({uname})\n"
                f"🆔 **ID:** `{user.id}`\n"
                f"💎 **Plan:** {plan}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📺 **Source:** {channel_title}\n"
                f"📎 **Channel:** {channel_username or f'`{source_chat}`'}\n"
                f"🔗 **Link:** {source_link}\n"
                f"📌 **Start ID:** `{msg_id}`\n"
                f"📦 **Quantity:** `{quantity}`\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🕐 **Time:** `{now}`"
            )
            await self.log_source_link(user, source_chat, source_link, msg_id, quantity)
            try:
                await self.app.send_message(SRC_TRACKER_CHANNEL, text)
            except Exception as send_err:
                log.error(f"SRC tracker send failed: {send_err}")
        except Exception as e:
            log.error(f"SRC tracker error: {e}")

    # ─── Resolve / admin check ────────────────────────────────
    async def resolve_chat_id(self, chat_ref):
        if isinstance(chat_ref, int):
            return chat_ref
        try:
            chat = await self.app.get_chat(chat_ref)
            return chat.id
        except Exception:
            return chat_ref

    async def check_bot_is_admin(self, chat_id) -> Tuple[bool, str]:
        try:
            me = await self.app.get_me()
            chat = await self.app.get_chat(chat_id)
            member = await self.app.get_chat_member(chat_id, me.id)
            status = member.status.value
            if status not in ("administrator", "owner", "creator"):
                return False, "❌ Bot is not admin in destination chat."
            if chat.type.value == "channel":
                if hasattr(member, "privileges") and member.privileges is not None:
                    if not member.privileges.can_post_messages:
                        return False, "❌ Bot lacks Post Messages permission."
            return True, ""
        except (ChatAdminRequired, UserNotParticipant, ChannelPrivate) as e:
            return False, f"❌ Bot is not in destination chat or lacks access.\n{e}"
        except Exception as e:
            return False, f"❌ Could not check admin status: {e}"

    # ─── Copy helpers ─────────────────────────────────────────
    async def download_and_resend(self, msg: Message, dest, topic,
                                   caption=None, reply_markup=None) -> bool:
        extra = {"reply_to_message_id": topic} if topic else {}
        if reply_markup:
            extra["reply_markup"] = reply_markup
        path = None
        try:
            cap = caption or msg.caption or None
            if msg.photo:
                path = await self.app.download_media(msg)
                await self.app.send_photo(dest, path, caption=cap, **extra)
            elif msg.video:
                path = await self.app.download_media(msg)
                await self.app.send_video(dest, path, caption=cap,
                    duration=msg.video.duration, width=msg.video.width,
                    height=msg.video.height, **extra)
            elif msg.document:
                path = await self.app.download_media(msg)
                await self.app.send_document(dest, path, caption=cap, **extra)
            elif msg.audio:
                path = await self.app.download_media(msg)
                await self.app.send_audio(dest, path, caption=cap,
                    duration=msg.audio.duration, **extra)
            elif msg.voice:
                path = await self.app.download_media(msg)
                await self.app.send_voice(dest, path, caption=cap, **extra)
            elif msg.video_note:
                path = await self.app.download_media(msg)
                await self.app.send_video_note(dest, path, **extra)
            elif msg.sticker:
                await self.app.send_sticker(dest, msg.sticker.file_id, **extra)
            elif msg.animation:
                path = await self.app.download_media(msg)
                await self.app.send_animation(dest, path, caption=cap, **extra)
            elif msg.text or cap:
                await self.app.send_message(dest, cap or msg.text, **extra)
            else:
                return False
            return True
        except Exception as e:
            log.error(f"download_and_resend error: {e}")
            return False
        finally:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    async def copy_one(self, src_msg: Message, cfg: Dict, uid: int,
                       pin_in_dest: bool = False) -> bool:
        dest = cfg.get("channel")
        topic = cfg.get("topic")
        kwargs = {"reply_to_message_id": topic} if topic else {}
        original_text = src_msg.caption if src_msg.media else src_msg.text
        modified_text = self.apply_rules(original_text, cfg) or None
        text_changed = modified_text != original_text
        has_markup = bool(src_msg.reply_markup) and isinstance(src_msg.reply_markup, InlineKeyboardMarkup)

        if has_markup or text_changed:
            return await self.download_and_resend(
                src_msg, dest, topic,
                caption=modified_text if text_changed else None,
                reply_markup=src_msg.reply_markup if has_markup else None
            )
        try:
            sent = await self.app.copy_message(
                chat_id=dest, from_chat_id=src_msg.chat.id,
                message_id=src_msg.id, **kwargs
            )
        except FloodWait as e:
            log.warning(f"FloodWait {e.value}s")
            await asyncio.sleep(e.value)
            return await self.copy_one(src_msg, cfg, uid, pin_in_dest)
        except Exception as e:
            log.warning(f"copy_message failed ({type(e).__name__}), fallback")
            return await self.download_and_resend(src_msg, dest, topic, modified_text)
        if sent and pin_in_dest:
            try:
                await sent.pin()
            except Exception as e:
                log.warning(f"Pin failed: {e}")
        return True

    # ─── Progress ─────────────────────────────────────────────
    def make_progress_bar(self, current: int, total: int, bar_length: int = 20) -> str:
        if total <= 0:
            return "[░░░░░░░░░░░░░░░░░░░░] 0%"
        filled = int(bar_length * current / total)
        bar = "█" * filled + "░" * (bar_length - filled)
        percent = round(current / total * 100, 1)
        return f"[{bar}] {percent}% ({current}/{total})"

    async def update_progress_message(self, uid: int, msg: Message, status: str = "active",
                                      done: int = None, skipped: int = None, failed: int = None,
                                      current: int = None, total: int = None):
        if done is None or total is None:
            return
        bar = self.make_progress_bar(done, total)
        status_icon = {"active": "⏳", "paused": "⏸", "stopped": "🛑", "completed": "✅"}.get(status, "⏳")
        text = (
            f"**{status_icon} Bulk Copy {'Paused' if status=='paused' else 'Running'}**\n\n"
            f"{bar}\n\n"
            f"✅ Copied: `{done}`\n"
            f"⏭️ Skipped: `{skipped}`\n"
            f"❌ Failed: `{failed}`\n"
            f"📊 Progress: `{current}/{total}`\n\n"
            f"_Send /cancel or use buttons below._"
        )
        if status == "active":
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("⏸ Pause", callback_data="bulk_pause"),
                InlineKeyboardButton("🛑 Stop", callback_data="bulk_stop")]])
        elif status == "paused":
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("▶ Resume", callback_data="bulk_resume"),
                InlineKeyboardButton("🛑 Stop", callback_data="bulk_stop")]])
        else:
            markup = None
        try:
            await msg.edit_text(text, reply_markup=markup)
        except MessageNotModified:
            pass
        except Exception as e:
            log.error(f"Failed to edit progress msg: {e}")

    # ─── Effective delay ──────────────────────────────────────
    async def get_effective_delay(self, uid: int) -> float:
        user_cfg = await self.get_user_cfg(uid)
        admin_cfg = await self.get_admin_cfg()
        user_delay = user_cfg.get("delay")
        if user_delay is not None and user_delay > 0:
            return float(user_delay)
        admin_delay = admin_cfg.get("global_delay")
        if admin_delay is not None and admin_delay > 0:
            return float(admin_delay)
        return BULK_DELAY

    # ─── Bulk runner ──────────────────────────────────────────
    async def run_bulk_copy(self, msg: Message, uid: int, chat_id, start_id: int,
                            quantity: int, cfg: Dict, source_link: str = ""):
        dest = cfg.get("channel")
        self.cancel_flags[uid] = False
        self.pause_flags[uid] = False

        try:
            chat_id = await self.resolve_chat_id(chat_id)
        except Exception as e:
            await msg.reply_text(f"❌ Could not resolve source chat: `{e}`")
            return

        if not await self.is_premium(uid):
            allowed_copy, remaining = await self.check_limit(uid, 1)
            if not allowed_copy:
                await msg.reply_text(
                    "⛔ **Daily free limit reached!**\n\n"
                    f"You have used all **{FREE_DAILY_LIMIT}** free copies today.\n\n"
                    "💎 Upgrade to Premium for unlimited copies.",
                    reply_markup=await self.build_premium_keyboard()
                )
                return
            if quantity > remaining:
                quantity = remaining
                await msg.reply_text(f"⚠️ Free limit: capping to **{quantity}** copies.")

        asyncio.create_task(self.send_src_tracker(msg.from_user, chat_id, start_id, quantity, source_link))
        await self.track(msg.from_user, "bulk_start", source=chat_id, dest=dest, msg_id=start_id, quantity=quantity)

        pinned_msg_id = None
        try:
            source_chat = await self.app.get_chat(chat_id)
            if source_chat.pinned_message:
                pinned_msg_id = source_chat.pinned_message.id
        except Exception as e:
            log.warning(f"Could not fetch pinned msg: {e}")

        status_msg = await msg.reply_text(
            "⏳ **Bulk copy initialising...**",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏸ Pause", callback_data="bulk_pause"),
                InlineKeyboardButton("🛑 Stop", callback_data="bulk_stop")]])
        )
        try:
            await status_msg.pin()
        except Exception:
            pass

        done = skipped = failed = 0
        delay_seconds = await self.get_effective_delay(uid)
        i = 0

        try:
            for i in range(quantity):
                if self.cancel_flags.get(uid, False):
                    await self.track(msg.from_user, "bulk_cancelled", source=chat_id, dest=dest,
                              msg_id=start_id, quantity=quantity, copied=done, skipped=skipped, failed=failed)
                    await self.update_progress_message(uid, status_msg, status="stopped",
                        done=done, skipped=skipped, failed=failed, current=i, total=quantity)
                    try: await status_msg.unpin()
                    except: pass
                    return

                while self.pause_flags.get(uid, False):
                    await asyncio.sleep(1)
                    if self.cancel_flags.get(uid, False):
                        break
                if self.cancel_flags.get(uid, False):
                    continue

                mid = start_id + i
                try:
                    src_msg = await self.app.get_messages(chat_id, mid)
                    if not src_msg or src_msg.empty:
                        skipped += 1
                    else:
                        pin_this = (pinned_msg_id is not None and mid == pinned_msg_id)
                        ok = await self.copy_one(src_msg, cfg, uid, pin_in_dest=pin_this)
                        if ok:
                            done += 1
                            if not await self.is_premium(uid):
                                new_count = await self.increment_usage(uid, 1)
                                if new_count >= FREE_DAILY_LIMIT:
                                    await status_msg.edit_text(
                                        f"⛔ **Free daily limit reached!**\n\n"
                                        f"✅ Copied: `{done}`  ⏭️ Skipped: `{skipped}`  ❌ Failed: `{failed}`\n\n"
                                        "Upgrade to Premium. /getpremium"
                                    )
                                    return
                        else:
                            failed += 1

                    if (i + 1) % 5 == 0 or (i + 1) == quantity:
                        await self.update_progress_message(uid, status_msg, status="active",
                            done=done, skipped=skipped, failed=failed, current=i+1, total=quantity)
                    await asyncio.sleep(delay_seconds)

                except asyncio.CancelledError:
                    raise
                except FloodWait as e:
                    log.warning(f"FloodWait {e.value}s at msg {mid}")
                    await asyncio.sleep(e.value)
                except Exception as e:
                    log.error(f"Bulk error at msg {mid}: {e}")
                    failed += 1

        except asyncio.CancelledError:
            await self.track(msg.from_user, "bulk_cancelled", source=chat_id, dest=dest,
                      msg_id=start_id, quantity=quantity, copied=done, skipped=skipped, failed=failed)
            await self.update_progress_message(uid, status_msg, status="stopped",
                done=done, skipped=skipped, failed=failed, current=i, total=quantity)
            try: await status_msg.unpin()
            except: pass
            return
        finally:
            self.cancel_flags.pop(uid, None)
            self.pause_flags.pop(uid, None)
            self.active_tasks.pop(uid, None)

        await self.track(msg.from_user, "bulk_done", source=chat_id, dest=dest,
                  msg_id=start_id, quantity=quantity, copied=done, skipped=skipped, failed=failed)
        await self.update_progress_message(uid, status_msg, status="completed",
            done=done, skipped=skipped, failed=failed, current=quantity, total=quantity)
        try: await status_msg.unpin()
        except: pass

    # ─── Broadcast ────────────────────────────────────────────
    async def execute_broadcast(self, origin_msg: Message, content):
        chats = await self._load_broadcast_chats()
        if not chats:
            await origin_msg.reply_text("📭 Broadcast list is empty.")
            return
        progress = await origin_msg.reply_text(f"📡 Broadcasting to {len(chats)} chats…")
        success, fail = 0, 0
        for chat_id in chats:
            try:
                if isinstance(content, str):
                    await self.app.send_message(chat_id, content)
                else:
                    await content.copy(chat_id)
                success += 1
            except FloodWait as e:
                await asyncio.sleep(e.value)
                try:
                    if isinstance(content, str):
                        await self.app.send_message(chat_id, content)
                    else:
                        await content.copy(chat_id)
                    success += 1
                except Exception:
                    fail += 1
            except Exception as e:
                log.warning(f"Broadcast to {chat_id} failed: {e}")
                fail += 1
            await asyncio.sleep(0.2)
        try:
            await progress.edit_text(
                f"✅ Broadcast finished!\n\n• Sent: {success}\n• Failed: {fail}"
            )
        except Exception:
            pass

    # ─── Settings helpers ─────────────────────────────────────
    def format_settings_text(self, cfg: Dict) -> str:
        reps = cfg.get("replacements", {})
        dels = cfg.get("deletions", [])
        prefix = cfg.get("prefix", "")
        suffix = cfg.get("suffix", "")
        delay = cfg.get("delay")
        rep_lines = "\n".join(f" • {k} → {v or '(delete)'}" for k, v in reps.items()) or " None"
        del_lines = "\n".join(f" • {w}" for w in dels) or " None"
        delay_str = f"{delay}s" if delay else "Not set (default)"
        return (
            f"⚙️ Your Settings\n\n"
            f"📤 Destination: {cfg.get('channel') or 'Not set'}\n"
            f"📌 Topic: {cfg.get('topic') or 'General'}\n"
            f"🔹 Prefix: {prefix or 'None'}\n"
            f"🔸 Suffix: {suffix or 'None'}\n"
            f"⏱ Batch Delay: {delay_str}\n\n"
            f"Replacements (max 2):\n{rep_lines}\n\n"
            f"Deletions:\n{del_lines}"
        )

    async def send_settings_message(self, uid: int, source_msg: Message):
        cfg = await self.get_user_cfg(uid)
        text = self.format_settings_text(cfg)
        buttons = []
        reps = list(cfg.get("replacements", {}).items())
        dels = cfg.get("deletions", [])
        prefix = cfg.get("prefix", "")
        suffix = cfg.get("suffix", "")
        self.inline_state[uid] = {
            "settings_reps": reps, "settings_dels": dels,
            "settings_prefix": prefix, "settings_suffix": suffix,
        }
        for idx, (old, new) in enumerate(reps):
            buttons.append([InlineKeyboardButton(f"❌ replace: {old}", callback_data=f"delrep_{idx}")])
        for idx, word in enumerate(dels):
            buttons.append([InlineKeyboardButton(f"❌ delete: {word}", callback_data=f"deldelete_{idx}")])
        if prefix:
            buttons.append([InlineKeyboardButton("❌ clear prefix", callback_data="delprefix")])
        if suffix:
            buttons.append([InlineKeyboardButton("❌ clear suffix", callback_data="delsuffix")])
        buttons.append([InlineKeyboardButton("🔄 Refresh", callback_data="settings")])
        markup = InlineKeyboardMarkup(buttons) if buttons else None
        await source_msg.reply_text(text, reply_markup=markup)

    async def edit_settings_message(self, uid: int, msg_to_edit: Message):
        cfg = await self.get_user_cfg(uid)
        text = self.format_settings_text(cfg)
        buttons = []
        reps = list(cfg.get("replacements", {}).items())
        dels = cfg.get("deletions", [])
        prefix = cfg.get("prefix", "")
        suffix = cfg.get("suffix", "")
        self.inline_state[uid] = {
            "settings_reps": reps, "settings_dels": dels,
            "settings_prefix": prefix, "settings_suffix": suffix,
        }
        for idx, (old, new) in enumerate(reps):
            buttons.append([InlineKeyboardButton(f"❌ replace: {old}", callback_data=f"delrep_{idx}")])
        for idx, word in enumerate(dels):
            buttons.append([InlineKeyboardButton(f"❌ delete: {word}", callback_data=f"deldelete_{idx}")])
        if prefix:
            buttons.append([InlineKeyboardButton("❌ clear prefix", callback_data="delprefix")])
        if suffix:
            buttons.append([InlineKeyboardButton("❌ clear suffix", callback_data="delsuffix")])
        buttons.append([InlineKeyboardButton("🔄 Refresh", callback_data="settings")])
        markup = InlineKeyboardMarkup(buttons) if buttons else None
        try:
            await msg_to_edit.edit_text(text, reply_markup=markup)
        except MessageNotModified:
            pass

    # ─── Admin panel ──────────────────────────────────────────
    async def send_admin_panel(self, source_msg: Message):
        cfg = await self.get_admin_cfg()
        upi_display = cfg.get('upi_id') or cfg.get('extracted_upi') or 'not set'
        force_join = cfg.get('backup_channel_id')
        force_str = f"`{force_join}` ✅" if force_join else "❌ disabled"
        welcome_status = []
        if cfg.get("welcome_text"):  welcome_status.append("Text ✅")
        if cfg.get("welcome_image"): welcome_status.append("Image ✅")
        if cfg.get("welcome_video"): welcome_status.append("Video ✅")
        welcome_str = " | ".join(welcome_status) if welcome_status else "Default"
        delay_str = f"`{cfg.get('global_delay')}`s" if cfg.get('global_delay') else "default (2.5s)"
        shortener_status = "not configured"
        if cfg.get("shortener_api_url") and cfg.get("shortener_api_key"):
            shortener_status = f"✅ {cfg.get('shortener_method','auto').upper()}"
        elif cfg.get("shortener_api_url") or cfg.get("shortener_api_key"):
            shortener_status = "⚠️ incomplete"
        else:
            shortener_status = "❌ fallback to v.gd"

        text = (
            "🛠 **Admin Panel**\n\n"
            f"📱 UPI ID: `{upi_display}`\n"
            f"📞 Phone: `{cfg['phone'] or 'not set'}`\n"
            f"🖼 QR Code: {'✅ uploaded' if cfg['qr_file_id'] else '❌ not set'}\n"
            f"🔗 URL Shortener: `{cfg['url_shortener'] or 'not set'}`\n"
            f"🔗 Shortener API: {shortener_status}\n"
            f"💳 Razorpay Key: `{'✅ set' if cfg['razorpay_key'] else '❌ not set'}`\n"
            f"🔐 Force Join: {force_str}\n"
            f"⏱ Global Delay: {delay_str}\n"
            f"👋 **Welcome:** {welcome_str}\n"
            f"⚡ Free 6h: {'✅ enabled' if cfg.get('free_6h_enabled') else '❌ disabled'}\n"
            f"📊 Free claims limit: {cfg.get('free_claims_limit', 3)} per 24h\n\n"
            "**Premium Pricing:**\n"
            f"• Day: {PREMIUM_PRICES['day']} | Week: {PREMIUM_PRICES['week']}\n"
            f"• Month: {PREMIUM_PRICES['month']} | Year: {PREMIUM_PRICES['year']}"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 Set UPI ID",        callback_data="adm_set_upi"),
             InlineKeyboardButton("📞 Set Phone",          callback_data="adm_set_phone")],
            [InlineKeyboardButton("🖼 Set QR Code",        callback_data="adm_set_qr"),
             InlineKeyboardButton("🔗 Set URL Shortener",  callback_data="adm_set_url")],
            [InlineKeyboardButton("💳 Set Razorpay",       callback_data="adm_set_rzp")],
            [InlineKeyboardButton("✏️ Set Welcome Text",   callback_data="adm_set_welcome_text"),
             InlineKeyboardButton("🖼 Set Welcome Image",  callback_data="adm_set_welcome_image")],
            [InlineKeyboardButton("🎬 Set Welcome Video",  callback_data="adm_set_welcome_video"),
             InlineKeyboardButton("🧹 Clear Welcome",      callback_data="adm_clear_welcome")],
            [InlineKeyboardButton("🔐 Set Force Join",     callback_data="adm_set_forcejoin")],
            [InlineKeyboardButton("⏱ Set Global Delay",    callback_data="adm_set_global_delay")],
            [InlineKeyboardButton("🔗 Shortener API",      callback_data="adm_set_shortener_api"),
             InlineKeyboardButton("🔑 Shortener Key",      callback_data="adm_set_shortener_key")],
            [InlineKeyboardButton("⚡ Toggle Free 6h",     callback_data="adm_toggle_free6h"),
             InlineKeyboardButton("📊 Set Claim Limit",    callback_data="adm_set_claim_limit")],
            [InlineKeyboardButton("⚙️ Shortener Config",   callback_data="adm_shortener_config")],
            [InlineKeyboardButton("📡 Broadcast",          callback_data="adm_broadcast")],
            [InlineKeyboardButton("🎟 Create Promo",       callback_data="adm_create_promo"),
             InlineKeyboardButton("📋 List Promos",        callback_data="adm_list_promos")],
            [InlineKeyboardButton("❌ Delete Promo",       callback_data="adm_del_promo")],
            [InlineKeyboardButton("💎 Give Premium",       callback_data="adm_give_premium"),
             InlineKeyboardButton("🚫 Revoke Premium",     callback_data="adm_revoke_premium")],
            [InlineKeyboardButton("📋 List Premium",       callback_data="adm_list_premium"),
             InlineKeyboardButton("📊 View Usage",         callback_data="adm_view_usage")],
            [InlineKeyboardButton("🔨 Ban User",           callback_data="adm_ban_user"),
             InlineKeyboardButton("✅ Unban User",         callback_data="adm_unban_user")],
            [InlineKeyboardButton("📋 Banned List",        callback_data="adm_list_banned"),
             InlineKeyboardButton("♻️ Restore Chat",       callback_data="adm_restore_chat")],
            [InlineKeyboardButton("📡 View Source Logs",   callback_data="adm_view_sources")],
        ])
        await source_msg.reply_text(text, reply_markup=keyboard)

    # ─── Premium keyboard ─────────────────────────────────────
    async def build_premium_keyboard(self) -> InlineKeyboardMarkup:
        cfg = await self.get_admin_cfg()
        buttons = [
            [InlineKeyboardButton("☀️ QR – 1 Day (₹3)",    callback_data="payqr_day"),
             InlineKeyboardButton("📅 QR – 1 Week (₹20)",   callback_data="payqr_week")],
            [InlineKeyboardButton("🗓 QR – 1 Month (₹80)", callback_data="payqr_month"),
             InlineKeyboardButton("🏆 QR – 1 Year (₹900)", callback_data="payqr_year")],
            [InlineKeyboardButton("🎟 Redeem Promo Code",   callback_data="redeem_promo")],
        ]
        if (cfg.get("free_6h_enabled") and cfg.get("shortener_api_url")
                and cfg.get("shortener_api_key")):
            buttons.append([InlineKeyboardButton("⚡ Get FREE 6 Hours Premium", callback_data="free_6h")])
        return InlineKeyboardMarkup(buttons)

    # ══════════════════════════════════════════════════════════
    # HANDLERS
    # ══════════════════════════════════════════════════════════
    def _register_handlers(self):
        app = self.app

        @app.on_message(filters.command("start"), group=-1)
        async def cmd_start_banned(_, msg: Message):
            if not msg.from_user:
                return
            if await self.is_banned(msg.from_user.id):
                info = await self.get_ban_info(msg.from_user.id)
                reason = info.get("reason", "No reason provided") if info else "No reason provided"
                await msg.reply_text(
                    "🚫 **You are banned from using this bot.**\n\n"
                    f"📝 Reason: {reason}\n\n"
                    "If you believe this is an error, please contact the admin."
                )

        def auth_filter(_, __, msg: Message) -> bool:
            if not msg.from_user:
                return False
            uid = msg.from_user.id
            if not ALLOWED_USERS:
                return True
            return uid in ALLOWED_USERS
        allowed = filters.create(auth_filter)

        def owner_filter(_, __, msg: Message) -> bool:
            return bool(self.owner_id and msg.from_user and msg.from_user.id == self.owner_id)
        owner_only = filters.create(owner_filter)

        # ── /start ─────────────────────────────────────────────
        @app.on_message(filters.command("start") & allowed)
        async def cmd_start(client: Client, msg: Message):
            uid = msg.from_user.id
            if not await self.enforce_force_join(msg, uid):
                return
            first_name = msg.from_user.first_name or "there"

            if msg.command and len(msg.command) > 1:
                param = msg.command[1]
                if param.startswith("claim_"):
                    code = param[6:]
                    ok, reply = await self.verify_and_use_claim(code, uid)
                    await self.track(msg.from_user, "claim_attempt", detail=f"code={code} success={ok}")
                    await msg.reply_text(reply)
                    if ok:
                        expiry = await self.get_premium_expiry(uid)
                        await msg.reply_text(
                            f"💎 **Your premium is now active!**\n\n"
                            f"⏰ Expires: `{expiry}`\n\n"
                            f"Use /start to access the main menu."
                        )
                        return

            await self.add_broadcast_chat(msg.chat.id)
            await self.track(msg.from_user, "command", detail="/start")
            admin_cfg = await self.get_admin_cfg()

            if await self.is_premium(uid):
                expiry = await self.get_premium_expiry(uid)
                status_line = f"💎 **Premium** | Expires: `{expiry}`"
            else:
                used = await self.get_daily_usage(uid)
                remaining = max(0, FREE_DAILY_LIMIT - used)
                status_line = (f"🆓 **Free** | Used today: `{used}/{FREE_DAILY_LIMIT}` "
                               f"| Remaining: `{remaining}`")
                if remaining == 0:
                    await msg.reply_text(
                        f"Hey {first_name}! 👋\n\n"
                        "⚠️ **Your free daily limit is over!**\n\n"
                        "💎 Get Premium to continue.",
                        reply_markup=await self.build_premium_keyboard()
                    )
                    return

            custom_text = admin_cfg.get("welcome_text", "").strip()
            if custom_text:
                welcome = (custom_text
                           .replace("{name}", first_name)
                           .replace("{first_name}", first_name)
                           .replace("{status}", status_line))
                if "{status}" not in custom_text:
                    welcome += f"\n\n{status_line}"
            else:
                welcome = (
                    f"Hey {first_name}! 👋\n\n"
                    f"{status_line}\n\n"
                    "This bot is made by @akashh955956.\n\n"
                    "📋 **Main Menu:**"
                )

            keyboard_rows = [
                [InlineKeyboardButton("📤 Set Destination", callback_data="setchannel"),
                 InlineKeyboardButton("📌 Set Topic",       callback_data="settopic")],
                [InlineKeyboardButton("📱 My Settings",     callback_data="settings"),
                 InlineKeyboardButton("🗑 Clear Destination", callback_data="clearchannel")],
                [InlineKeyboardButton("🔄 Replace Word",    callback_data="replace_ask"),
                 InlineKeyboardButton("❌ Delete Word",     callback_data="delete_ask")],
                [InlineKeyboardButton("🔹 Add Prefix",      callback_data="prefix_ask"),
                 InlineKeyboardButton("🔸 Add Suffix",      callback_data="suffix_ask")],
                [InlineKeyboardButton("🧹 Clear All Rules", callback_data="clearrules"),
                 InlineKeyboardButton("🛑 Cancel Bulk",     callback_data="cancelb")],
                [InlineKeyboardButton("📚 Batch Help",      callback_data="batch_help"),
                 InlineKeyboardButton("💎 Get Premium",     callback_data="getpremium")],
                [InlineKeyboardButton("🎟 Redeem Promo Code", callback_data="redeem_promo")],
            ]
            if uid == self.owner_id:
                keyboard_rows.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="admin_panel")])

            kb = InlineKeyboardMarkup(keyboard_rows)
            welcome_video = admin_cfg.get("welcome_video", "")
            welcome_image = admin_cfg.get("welcome_image", "")
            try:
                if welcome_video:
                    await msg.reply_video(welcome_video, caption=welcome, reply_markup=kb)
                elif welcome_image:
                    await msg.reply_photo(welcome_image, caption=welcome, reply_markup=kb)
                else:
                    await msg.reply_text(welcome, reply_markup=kb)
            except Exception as e:
                log.error(f"Welcome media send failed: {e}")
                try:
                    await msg.reply_text(welcome, reply_markup=kb)
                except Exception:
                    pass

        # ── /help ──────────────────────────────────────────────
        @app.on_message(filters.command("help") & allowed)
        async def cmd_help(_, msg: Message):
            uid = msg.from_user.id
            if not await self.enforce_force_join(msg, uid):
                return
            await self.track(msg.from_user, "command", detail="/help")
            await msg.reply_text(
                "📋 Commands\n\n"
                "Setup:\n"
                "Use buttons in /start to set destination & topic.\n"
                "/clearchannel – clear destination\n\n"
                "Text Rules:\n"
                "/addreplace old → new\n"
                "/adddelete word\n"
                "/delreplace old\n"
                "/deldelete word\n"
                "/setprefix text\n"
                "/setsuffix text\n\n"
                "Bulk Copy:\n"
                "Send t.me link → bot asks quantity\n"
                "/batch <link> <quantity>\n"
                "/cancel\n\n"
                "Delay:\n"
                "/setdelay <seconds>\n"
                "/mydelay\n\n"
                "Info:\n"
                "/settings\n"
                "/clearrules\n"
                "/mystatus\n"
                "/getpremium"
            )

        # ─── Text rules ────────────────────────────────────────
        @app.on_message(filters.command("addreplace") & allowed)
        async def cmd_addreplace(_, msg: Message):
            uid = msg.from_user.id
            if not await self.enforce_force_join(msg, uid):
                return
            args = msg.text.split(maxsplit=1)
            if len(args) < 2:
                return await msg.reply_text("Usage: /addreplace old → new")
            cfg = await self.get_user_cfg(uid)
            reps = cfg.get("replacements", {})
            if "→" in args[1]:
                old, new = [p.strip() for p in args[1].split("→", 1)]
            else:
                old, new = args[1].strip(), ""
            if old in reps:
                reps[old] = new
                await self.set_user_cfg(uid, cfg)
                return await msg.reply_text(f"✅ Updated `{old}`.")
            if len(reps) >= 2:
                return await msg.reply_text("⚠️ Max 2 replacement rules.")
            reps[old] = new
            await self.set_user_cfg(uid, cfg)
            await msg.reply_text(f"✅ Rule added: `{old}` → `{new}`")

        @app.on_message(filters.command("delreplace") & allowed)
        async def cmd_delreplace(_, msg: Message):
            uid = msg.from_user.id
            if not await self.enforce_force_join(msg, uid):
                return
            args = msg.text.split(maxsplit=1)
            if len(args) < 2:
                return await msg.reply_text("Usage: /delreplace word")
            cfg = await self.get_user_cfg(uid)
            word = args[1].strip()
            if word in cfg.get("replacements", {}):
                del cfg["replacements"][word]
                await self.set_user_cfg(uid, cfg)
                await msg.reply_text(f"🗑️ Removed {word}.")
            else:
                await msg.reply_text(f"⚠️ No rule for {word}.")

        @app.on_message(filters.command("adddelete") & allowed)
        async def cmd_adddelete(_, msg: Message):
            uid = msg.from_user.id
            if not await self.enforce_force_join(msg, uid):
                return
            args = msg.text.split(maxsplit=1)
            if len(args) < 2:
                return await msg.reply_text("Usage: /adddelete word")
            cfg = await self.get_user_cfg(uid)
            word = args[1].strip()
            if word not in cfg.get("deletions", []):
                cfg.setdefault("deletions", []).append(word)
                await self.set_user_cfg(uid, cfg)
            await msg.reply_text(f"✅ {word} will be deleted.")

        @app.on_message(filters.command("deldelete") & allowed)
        async def cmd_deldelete(_, msg: Message):
            uid = msg.from_user.id
            if not await self.enforce_force_join(msg, uid):
                return
            args = msg.text.split(maxsplit=1)
            if len(args) < 2:
                return await msg.reply_text("Usage: /deldelete word")
            cfg = await self.get_user_cfg(uid)
            word = args[1].strip()
            try:
                cfg["deletions"].remove(word)
                await self.set_user_cfg(uid, cfg)
                await msg.reply_text(f"🗑️ Removed {word}.")
            except (ValueError, KeyError):
                await msg.reply_text(f"⚠️ No deletion rule for {word}.")

        @app.on_message(filters.command("setprefix") & allowed)
        async def cmd_setprefix(_, msg: Message):
            uid = msg.from_user.id
            if not await self.enforce_force_join(msg, uid):
                return
            args = msg.text.split(maxsplit=1)
            cfg = await self.get_user_cfg(uid)
            cfg["prefix"] = args[1] if len(args) > 1 else ""
            await self.set_user_cfg(uid, cfg)
            await msg.reply_text(f"✅ Prefix: {cfg['prefix']}")

        @app.on_message(filters.command("setsuffix") & allowed)
        async def cmd_setsuffix(_, msg: Message):
            uid = msg.from_user.id
            if not await self.enforce_force_join(msg, uid):
                return
            args = msg.text.split(maxsplit=1)
            cfg = await self.get_user_cfg(uid)
            cfg["suffix"] = args[1] if len(args) > 1 else ""
            await self.set_user_cfg(uid, cfg)
            await msg.reply_text(f"✅ Suffix: {cfg['suffix']}")

        @app.on_message(filters.command("settings") & allowed)
        async def cmd_settings(_, msg: Message):
            uid = msg.from_user.id
            if not await self.enforce_force_join(msg, uid):
                return
            await self.send_settings_message(uid, msg)

        @app.on_message(filters.command("clearrules") & allowed)
        async def cmd_clearrules(_, msg: Message):
            uid = msg.from_user.id
            if not await self.enforce_force_join(msg, uid):
                return
            cfg = await self.get_user_cfg(uid)
            cfg["replacements"] = {}
            cfg["deletions"] = []
            cfg["prefix"] = ""
            cfg["suffix"] = ""
            await self.set_user_cfg(uid, cfg)
            await msg.reply_text("🗑️ All text rules cleared.")

        @app.on_message(filters.command("cancel") & allowed)
        async def cmd_cancel(_, msg: Message):
            uid = msg.from_user.id
            did = False
            if uid in self.active_tasks and not self.active_tasks[uid].done():
                self.cancel_flags[uid] = True
                self.active_tasks[uid].cancel()
                did = True
            if uid in self.pending_bulk:
                del self.pending_bulk[uid]
                did = True
            if uid in self.inline_state:
                del self.inline_state[uid]
                did = True
            await self.track(msg.from_user, "command", detail="/cancel")
            await msg.reply_text("🛑 Cancelled!" if did else "⚠️ Nothing to cancel.")

        # ─── Premium ───────────────────────────────────────────
        @app.on_message(filters.command("mystatus") & allowed)
        async def cmd_mystatus(_, msg: Message):
            uid = msg.from_user.id
            if not await self.enforce_force_join(msg, uid):
                return
            if await self.is_premium(uid):
                expiry = await self.get_premium_expiry(uid)
                await msg.reply_text(f"💎 Premium user!\n\n⏰ Expires: {expiry}")
            else:
                used = await self.get_daily_usage(uid)
                remaining = max(0, FREE_DAILY_LIMIT - used)
                await msg.reply_text(
                    f"🆓 Free Plan\n\n📊 Used: {used}/{FREE_DAILY_LIMIT}\n📦 Remaining: {remaining}\n\n"
                    "💎 /getpremium",
                    reply_markup=await self.build_premium_keyboard()
                )

        @app.on_message(filters.command("getpremium") & allowed)
        async def cmd_getpremium(_, msg: Message):
            cfg = await self.get_admin_cfg()
            upi_to_show = cfg.get("upi_id") or cfg.get("extracted_upi") or ""
            lines = [
                "💎 Get Premium\n",
                "• ⚡ 6 Hours → Free via URL shortener (unlimited claims!)",
                f"• ☀️ 1 Day → {PREMIUM_PRICES['day']}",
                f"• 📅 1 Week → {PREMIUM_PRICES['week']}",
                f"• 🗓 1 Month → {PREMIUM_PRICES['month']}",
                f"• 🏆 1 Year → {PREMIUM_PRICES['year']}\n",
            ]
            if upi_to_show: lines.append(f"📱 UPI: {upi_to_show}")
            if cfg["phone"]: lines.append(f"📞 Phone/Paytm: {cfg['phone']}")
            if cfg.get("url_shortener"): lines.append(f"🔗 Free 6h: {cfg['url_shortener']}")
            lines.append("\n📲 Tap a plan below for QR code.")
            lines.append("After payment, contact @akashh955956.")
            await msg.reply_text("\n".join(lines), reply_markup=await self.build_premium_keyboard())

        @app.on_message(filters.command("redeem") & allowed)
        async def cmd_redeem(_, msg: Message):
            uid = msg.from_user.id
            args = msg.text.split(maxsplit=1)
            if len(args) < 2:
                self.inline_state[uid] = {"action": "redeem_promo"}
                return await msg.reply_text("🎟 Send your promo code:")
            ok, reply = await self.redeem_promo(args[1].strip(), uid)
            await msg.reply_text(reply)

        # ─── Batch / delay ────────────────────────────────────
        @app.on_message(filters.command("batch") & allowed)
        async def cmd_batch(_, msg: Message):
            uid = msg.from_user.id
            if not await self.enforce_force_join(msg, uid):
                return
            cfg = await self.get_user_cfg(uid)
            args = msg.text.split(maxsplit=2)
            if len(args) < 2:
                return await msg.reply_text("Usage: /batch <t.me link> [qty]")
            link = args[1]
            quantity = None
            if len(args) >= 3 and args[2].strip().isdigit():
                quantity = int(args[2].strip())
                if quantity > 10_000_000:
                    return await msg.reply_text("⚠️ Max 10,000,000.")
            parsed = self.parse_tme_link(link)
            if not parsed:
                return await msg.reply_text("Invalid t.me link.")
            chat_ref, start_id = parsed
            source_link = self.extract_full_link(link) or link
            dest = cfg.get("channel")
            topic = cfg.get("topic") if dest else None
            if not dest:
                dest, topic = msg.chat.id, None
            if dest != msg.chat.id:
                ok, reason = await self.check_bot_is_admin(dest)
                if not ok:
                    return await msg.reply_text(reason)
            if quantity is None:
                self.pending_bulk[uid] = {"chat_id": chat_ref, "start_msg_id": start_id, "source_link": source_link}
                await self.track(msg.from_user, "link_detected", source=chat_ref, dest=dest, msg_id=start_id)
                await msg.reply_text(
                    f"🔗 **Link detected!**\n\n"
                    f"📌 Start ID: `{start_id}`\n"
                    f"📤 Destination: `{dest}`\n\n"
                    f"**How many messages to copy?**\n_(Reply with a number)_\n\n`/cancel` to abort."
                )
                return
            if uid in self.active_tasks and not self.active_tasks[uid].done():
                self.active_tasks[uid].cancel()
            bulk_cfg = cfg.copy()
            bulk_cfg["channel"] = dest
            bulk_cfg["topic"] = topic
            task = asyncio.create_task(
                self.run_bulk_copy(msg, uid, chat_ref, start_id, quantity, bulk_cfg, source_link)
            )
            self.active_tasks[uid] = task

        @app.on_message(filters.command("setdelay") & allowed)
        async def cmd_setdelay(_, msg: Message):
            uid = msg.from_user.id
            if not await self.enforce_force_join(msg, uid):
                return
            args = msg.text.split(maxsplit=1)
            if len(args) < 2:
                return await msg.reply_text("Usage: /setdelay <seconds>")
            try:
                seconds = float(args[1].strip())
                if seconds <= 0:
                    return await msg.reply_text("⚠️ Positive only.")
            except ValueError:
                return await msg.reply_text("⚠️ Invalid number.")
            cfg = await self.get_user_cfg(uid)
            cfg["delay"] = seconds
            await self.set_user_cfg(uid, cfg)
            await msg.reply_text(f"✅ Delay set to **{seconds}s**.")

        @app.on_message(filters.command("mydelay") & allowed)
        async def cmd_mydelay(_, msg: Message):
            uid = msg.from_user.id
            eff = await self.get_effective_delay(uid)
            await msg.reply_text(f"⏱ Effective batch delay: **{eff}s**")

        @app.on_message(filters.command("setglobaldelay") & owner_only)
        async def cmd_setglobaldelay(_, msg: Message):
            args = msg.text.split(maxsplit=1)
            if len(args) < 2:
                return await msg.reply_text("Usage: /setglobaldelay <seconds> | clear")
            arg = args[1].strip()
            if arg.lower() == "clear":
                await self.update_admin_cfg({"global_delay": None})
                return await msg.reply_text("✅ Global delay cleared.")
            try:
                seconds = float(arg)
                if seconds <= 0:
                    return await msg.reply_text("⚠️ Positive only.")
            except ValueError:
                return await msg.reply_text("⚠️ Invalid number.")
            await self.update_admin_cfg({"global_delay": seconds})
            await msg.reply_text(f"✅ Global delay: **{seconds}s**.")

        # ─── Owner ─────────────────────────────────────────────
        @app.on_message(filters.command("adminpanel") & owner_only)
        async def cmd_adminpanel(_, msg: Message):
            await self.send_admin_panel(msg)

        @app.on_message(filters.command("ban") & owner_only)
        async def cmd_ban(_, msg: Message):
            args = msg.text.split(maxsplit=2)
            if len(args) < 2:
                return await msg.reply_text("Usage: /ban <uid> [reason]")
            try:
                target = int(args[1])
            except ValueError:
                return await msg.reply_text("⚠️ Invalid UID.")
            if target == self.owner_id:
                return await msg.reply_text("❌ Cannot ban owner.")
            reason = args[2].strip() if len(args) > 2 else "Banned by admin"
            await self.ban_user(target, reason, banned_by=msg.from_user.id)
            await msg.reply_text(f"🔨 Banned `{target}`. Reason: {reason}")
            try:
                await self.app.send_message(target,
                    f"🚫 **You have been banned.**\n\n📝 Reason: {reason}")
            except Exception:
                pass

        @app.on_message(filters.command("unban") & owner_only)
        async def cmd_unban(_, msg: Message):
            args = msg.text.split()
            if len(args) < 2:
                return await msg.reply_text("Usage: /unban <uid>")
            try:
                target = int(args[1])
            except ValueError:
                return await msg.reply_text("⚠️ Invalid UID.")
            if await self.unban_user(target):
                await msg.reply_text(f"✅ Unbanned `{target}`.")
                try:
                    await self.app.send_message(target, "✅ You have been unbanned!")
                except Exception:
                    pass
            else:
                await msg.reply_text(f"⚠️ `{target}` not banned.")

        @app.on_message(filters.command("banned") & owner_only)
        async def cmd_banned_list(_, msg: Message):
            bans = await self._load_bans()
            if not bans:
                return await msg.reply_text("✅ No banned users.")
            lines = [f"🔨 **Banned ({len(bans)}):**\n"]
            for b_uid, rec in bans.items():
                lines.append(f"• `{b_uid}` — {rec.get('reason','No reason')} | {rec.get('banned_at','')[:10]}")
            await msg.reply_text("\n".join(lines))

        @app.on_message(filters.command("restore") & owner_only)
        async def cmd_restore(_, msg: Message):
            args = msg.text.split()
            if len(args) < 2:
                return await msg.reply_text("Usage: /restore <uid>")
            try:
                target = int(args[1])
            except ValueError:
                return await msg.reply_text("⚠️ Invalid UID.")
            history = await self.get_chat_log(target)
            if not history:
                return await msg.reply_text(f"📭 No history for `{target}`.")
            await msg.reply_text(f"♻️ Restoring `{len(history)}` messages…")
            restored = 0
            for entry in history:
                try:
                    ts = entry.get("ts", "?")
                    u_name = entry.get("username") or entry.get("full_name") or f"ID:{target}"
                    content = entry.get("text") or entry.get("caption") or f"[{entry.get('type', 'media')}]"
                    await self.app.send_message(msg.chat.id, f"━━━━━━━━━━━━━━━\n👤 {u_name} | 🕐 {ts}\n{content}")
                    restored += 1
                    await asyncio.sleep(0.3)
                except Exception as e:
                    log.error(f"Restore error: {e}")
            await msg.reply_text(f"✅ Restored `{restored}` / `{len(history)}`.")

        @app.on_message(filters.command("givepremium") & owner_only)
        async def cmd_givepremium(_, msg: Message):
            args = msg.text.split()
            if len(args) < 3:
                return await msg.reply_text("Usage: /givepremium <uid> <plan>\nPlans: 6h, day, week, month, year")
            try:
                target = int(args[1])
            except ValueError:
                return await msg.reply_text("⚠️ Invalid UID.")
            plan = args[2].lower()
            plan_map = {"6h": (6, "6 Hours"), "day": (24, "1 Day"), "week": (168, "1 Week"),
                        "month": (720, "1 Month"), "year": (8760, "1 Year")}
            if plan not in plan_map:
                return await msg.reply_text(f"⚠️ Use: {', '.join(plan_map)}")
            hours, label = plan_map[plan]
            expiry = await self.grant_premium(target, hours, label)
            await msg.reply_text(
                f"✅ Premium granted!\n👤 {target}\n📋 {label}\n⏰ {expiry.strftime('%Y-%m-%d %H:%M UTC')}"
            )

        @app.on_message(filters.command("revokepremium") & owner_only)
        async def cmd_revokepremium(_, msg: Message):
            args = msg.text.split()
            if len(args) < 2:
                return await msg.reply_text("Usage: /revokepremium <uid>")
            try:
                target = int(args[1])
            except ValueError:
                return await msg.reply_text("⚠️ Invalid UID.")
            await self.revoke_premium(target)
            await msg.reply_text(f"🚫 Premium revoked for {target}.")

        @app.on_message(filters.command("broadcast") & owner_only)
        async def cmd_broadcast(_, msg: Message):
            if msg.reply_to_message:
                to_send = msg.reply_to_message
            elif len(msg.text.split(maxsplit=1)) > 1:
                to_send = msg.text.split(maxsplit=1)[1]
            else:
                return await msg.reply_text("Usage: /broadcast <text> or reply")
            await self.execute_broadcast(msg, to_send)

        @app.on_message(filters.command("setforcechannel") & owner_only)
        async def cmd_setforcechannel(_, msg: Message):
            args = msg.text.split(maxsplit=1)
            if len(args) < 2:
                return await msg.reply_text("Usage: /setforcechannel <username/ID> | clear")
            ok, message = await self.validate_and_set_backup_channel(args[1].strip())
            await msg.reply_text(message)

        @app.on_message(filters.command("tracklog") & owner_only)
        async def cmd_tracklog(_, msg: Message):
            args = msg.text.split()[1:]
            entries = await self._load_track()
            if not entries:
                return await msg.reply_text("📭 No tracking data.")
            if args and args[0].lower() == "full":
                if self.TRACK_FILE.exists():
                    await msg.reply_document(str(self.TRACK_FILE), caption=f"📊 Full log ({len(entries)})")
                return
            try:
                limit = int(args[0]) if args else 10
            except Exception:
                limit = 10
            recent = entries[-limit:]
            lines = [f"📊 Last {len(recent)} tracking entries:\n"]
            for e in reversed(recent):
                lines.append(
                    f"━━━━━━━━━━━━━━━\n"
                    f"🕐 {e.get('ts','?')}\n"
                    f"👤 {e.get('user_id','?')} {e.get('username') or e.get('full_name') or ''}\n"
                    f"📌 {e.get('action','?')}\n"
                )
            text = "\n".join(lines)
            if len(text) > 4000:
                tmp = self.data_dir / "tracklog_summary.txt"
                tmp.write_text(text, encoding="utf-8")
                await msg.reply_document(str(tmp), caption="📊 Tracking summary")
            else:
                await msg.reply_text(text)

        @app.on_message(filters.command("trackstats") & owner_only)
        async def cmd_trackstats(_, msg: Message):
            entries = await self._load_track()
            if not entries:
                return await msg.reply_text("📭 No tracking data.")
            users, sources, dests = {}, {}, {}
            total_copied = 0
            for e in entries:
                uid_val = e.get("user_id")
                if uid_val:
                    users.setdefault(uid_val, e.get("username") or e.get("full_name") or str(uid_val))
                if e.get("source"): sources[e["source"]] = sources.get(e["source"], 0) + 1
                if e.get("dest"): dests[e["dest"]] = dests.get(e["dest"], 0) + 1
                total_copied += e.get("copied", 0)
            await msg.reply_text(
                f"📊 **Bot Stats**\n\n"
                f"Entries: {len(entries)}\n"
                f"Copied: {total_copied}\n"
                f"Users: {len(users)}"
            )

        @app.on_message(filters.command("trackwipe") & owner_only)
        async def cmd_trackwipe(_, msg: Message):
            await self._save_track([])
            await msg.reply_text("🗑️ Wiped.")

        @app.on_message(filters.command("srclog") & owner_only)
        async def cmd_srclog(_, msg: Message):
            src_logs = await self._load_src_log()
            if not src_logs:
                return await msg.reply_text("📭 No logs.")
            args = msg.text.split()[1:]
            limit = int(args[0]) if (args and args[0].isdigit()) else 20
            recent = src_logs[-limit:]
            lines = [f"📡 **Last {len(recent)} Source Links:**\n"]
            for entry in reversed(recent):
                lines.append(
                    f"━━━━━━━━━━━━━━━\n"
                    f"👤 `{entry.get('user_id')}`\n"
                    f"🔗 {entry.get('source_link','N/A')}\n"
                    f"📦 Qty: `{entry.get('quantity')}` | 🕐 `{entry.get('ts','')[:16]}`"
                )
            text = "\n".join(lines)
            if len(text) > 4000:
                tmp = self.data_dir / "srclog_summary.txt"
                tmp.write_text(text, encoding="utf-8")
                await msg.reply_document(str(tmp))
            else:
                await msg.reply_text(text)

        @app.on_message(filters.command("testshortener") & owner_only)
        async def cmd_testshortener(_, msg: Message):
            args = msg.text.split(maxsplit=1)
            if len(args) < 2:
                return await msg.reply_text("Usage: /testshortener <url>")
            url = args[1].strip()
            if not url.startswith(("http://", "https://")):
                return await msg.reply_text("⚠️ Include http:// or https://")
            status_msg = await msg.reply_text("⏳ Shortening…")
            short = await self.shorten_url(url)
            if short:
                await status_msg.edit_text(f"✅ `{short}`")
            else:
                await status_msg.edit_text("❌ Failed. Check logs.")

        # ─── CALLBACKS ─────────────────────────────────────────
        @app.on_callback_query()
        async def handle_callback(client: Client, callback_query: CallbackQuery):
            data = callback_query.data
            uid = callback_query.from_user.id

            if data == "check_force_join":
                cfg = await self.get_admin_cfg()
                channel_id = cfg.get("backup_channel_id")
                if not channel_id:
                    await callback_query.answer("No backup channel.", show_alert=True)
                    return
                if await self.is_member(uid, channel_id):
                    if uid in self.user_force_links:
                        old = self.user_force_links.pop(uid)
                        try:
                            await self.app.revoke_chat_invite_link(channel_id, old)
                        except Exception:
                            pass
                    try:
                        await callback_query.message.edit_text("✅ You have joined! Use /start.")
                    except Exception:
                        pass
                    await callback_query.answer("Verified!")
                else:
                    await callback_query.answer("Join first!", show_alert=True)
                return

            if data in ("bulk_pause", "bulk_resume", "bulk_stop"):
                task = self.active_tasks.get(uid)
                if not task or task.done():
                    await callback_query.answer("No active bulk copy.", show_alert=True)
                    return
                if data == "bulk_pause":
                    self.pause_flags[uid] = True
                    await callback_query.answer("Paused!")
                elif data == "bulk_resume":
                    self.pause_flags[uid] = False
                    await callback_query.answer("Resumed!")
                elif data == "bulk_stop":
                    self.cancel_flags[uid] = True
                    self.pause_flags.pop(uid, None)
                    await callback_query.answer("Stopping...")
                return

            if not await self.enforce_force_join(callback_query.message, uid):
                await callback_query.answer("Join the channel first!", show_alert=True)
                return

            if data == "setchannel":
                await callback_query.answer()
                self.inline_state[uid] = {"action": "set_channel"}
                await callback_query.message.reply_text(
                    "📤 Send the **channel username** or **numeric ID**.\n"
                    "Send `clear` to remove."
                )
                return

            if data == "settopic":
                await callback_query.answer()
                self.inline_state[uid] = {"action": "set_topic"}
                await callback_query.message.reply_text(
                    "📌 Send the **topic/thread ID** (integer).\nSend `0` for General."
                )
                return

            if data == "adm_broadcast":
                if uid != self.owner_id:
                    await callback_query.answer("❌ Unauthorized", show_alert=True)
                    return
                await callback_query.answer()
                self.inline_state[uid] = {"action": "adm_broadcast_msg"}
                await callback_query.message.reply_text(
                    "📡 Send the message to broadcast.\n`/cancel` to abort."
                )
                return

            if data.startswith("delrep_"):
                idx = int(data[7:])
                state = self.inline_state.get(uid, {})
                reps = state.get("settings_reps", [])
                if idx < len(reps):
                    old, new = reps[idx]
                    cfg = await self.get_user_cfg(uid)
                    reps_cfg = cfg.get("replacements", {})
                    if old in reps_cfg:
                        del reps_cfg[old]
                        cfg["replacements"] = reps_cfg
                        await self.set_user_cfg(uid, cfg)
                        await self.edit_settings_message(uid, callback_query.message)
                        await callback_query.answer(f"Removed `{old}`")
                return

            if data.startswith("deldelete_"):
                idx = int(data[10:])
                state = self.inline_state.get(uid, {})
                dels = state.get("settings_dels", [])
                if idx < len(dels):
                    word = dels[idx]
                    cfg = await self.get_user_cfg(uid)
                    if word in cfg.get("deletions", []):
                        cfg["deletions"].remove(word)
                        await self.set_user_cfg(uid, cfg)
                        await self.edit_settings_message(uid, callback_query.message)
                        await callback_query.answer(f"Removed `{word}`")
                return

            if data == "delprefix":
                cfg = await self.get_user_cfg(uid)
                cfg["prefix"] = ""
                await self.set_user_cfg(uid, cfg)
                await self.edit_settings_message(uid, callback_query.message)
                await callback_query.answer("Prefix cleared.")
                return

            if data == "delsuffix":
                cfg = await self.get_user_cfg(uid)
                cfg["suffix"] = ""
                await self.set_user_cfg(uid, cfg)
                await self.edit_settings_message(uid, callback_query.message)
                await callback_query.answer("Suffix cleared.")
                return

            if data == "settings":
                await self.send_settings_message(uid, callback_query.message)
                await callback_query.answer()
            elif data == "clearchannel":
                cfg = await self.get_user_cfg(uid)
                cfg["channel"] = None
                cfg["topic"] = None
                await self.set_user_cfg(uid, cfg)
                await callback_query.message.reply_text("🗑️ Destination cleared.")
                await callback_query.answer("Cleared!")
            elif data == "cancelb":
                did = False
                if uid in self.active_tasks and not self.active_tasks[uid].done():
                    self.cancel_flags[uid] = True
                    self.active_tasks[uid].cancel()
                    did = True
                if uid in self.pending_bulk:
                    del self.pending_bulk[uid]
                    did = True
                await callback_query.message.reply_text("🛑 Cancelled!" if did else "⚠️ Nothing active.")
                await callback_query.answer()
            elif data == "batch_help":
                await callback_query.message.reply_text(
                    "Send a `t.me` link to a restricted channel message.\n"
                    "I will ask you how many messages to copy.\n\n"
                    "Or use `/batch <link> <quantity>`."
                )
                await callback_query.answer()
            elif data == "clearrules":
                cfg = await self.get_user_cfg(uid)
                cfg["replacements"] = {}
                cfg["deletions"] = []
                cfg["prefix"] = ""
                cfg["suffix"] = ""
                await self.set_user_cfg(uid, cfg)
                await callback_query.message.reply_text("🗑️ Rules cleared.")
                await callback_query.answer()
            elif data == "replace_ask":
                await callback_query.answer()
                self.inline_state[uid] = {"action": "replace_old"}
                await callback_query.message.reply_text("✏️ Send the word to replace.")
            elif data == "delete_ask":
                await callback_query.answer()
                self.inline_state[uid] = {"action": "delete_word"}
                await callback_query.message.reply_text("❌ Send the word to delete.")
            elif data == "prefix_ask":
                await callback_query.answer()
                self.inline_state[uid] = {"action": "set_prefix"}
                await callback_query.message.reply_text("🔹 Send the prefix text.")
            elif data == "suffix_ask":
                await callback_query.answer()
                self.inline_state[uid] = {"action": "set_suffix"}
                await callback_query.message.reply_text("🔸 Send the suffix text.")
            elif data == "getpremium":
                await callback_query.answer()
                cfg = await self.get_admin_cfg()
                upi_to_show = cfg.get("upi_id") or cfg.get("extracted_upi") or ""
                lines = [
                    "💎 **Get Premium**\n",
                    "• ⚡ 6 Hours → Free via shortener",
                    f"• ☀️ 1 Day → {PREMIUM_PRICES['day']}",
                    f"• 📅 1 Week → {PREMIUM_PRICES['week']}",
                    f"• 🗓 1 Month → {PREMIUM_PRICES['month']}",
                    f"• 🏆 1 Year → {PREMIUM_PRICES['year']}\n",
                ]
                if upi_to_show: lines.append(f"📱 UPI: `{upi_to_show}`")
                if cfg["phone"]: lines.append(f"📞 Phone: `{cfg['phone']}`")
                lines.append("\n📲 Tap a plan below for QR code.")
                await callback_query.message.reply_text("\n".join(lines),
                    reply_markup=await self.build_premium_keyboard())
            elif data == "redeem_promo":
                await callback_query.answer()
                self.inline_state[uid] = {"action": "redeem_promo"}
                await callback_query.message.reply_text("🎟 Send your promo code:")
            elif data == "admin_panel":
                if uid != self.owner_id:
                    await callback_query.answer("❌ Unauthorized", show_alert=True)
                    return
                await callback_query.answer()
                await self.send_admin_panel(callback_query.message)
            elif data.startswith("adm_"):
                if uid != self.owner_id:
                    await callback_query.answer("❌ Unauthorized", show_alert=True)
                    return
                await callback_query.answer()
                action = data[4:]

                if action == "set_upi":
                    self.inline_state[uid] = {"action": "adm_set_upi"}
                    await callback_query.message.reply_text("Send UPI ID:")
                elif action == "set_phone":
                    self.inline_state[uid] = {"action": "adm_set_phone"}
                    await callback_query.message.reply_text("Send Phone/Paytm:")
                elif action == "set_qr":
                    self.inline_state[uid] = {"action": "adm_set_qr"}
                    await callback_query.message.reply_text("Send QR code image:")
                elif action == "set_url":
                    self.inline_state[uid] = {"action": "adm_set_url"}
                    await callback_query.message.reply_text("Send URL shortener link:")
                elif action == "set_rzp":
                    self.inline_state[uid] = {"action": "adm_set_rzp_key"}
                    await callback_query.message.reply_text("Send Razorpay Key ID:")
                elif action == "set_welcome_text":
                    self.inline_state[uid] = {"action": "adm_set_welcome_text"}
                    await callback_query.message.reply_text(
                        "Send welcome text.\nPlaceholders: `{name}`, `{status}`\n`clear` to reset."
                    )
                elif action == "set_welcome_image":
                    self.inline_state[uid] = {"action": "adm_set_welcome_image"}
                    await callback_query.message.reply_text("Send welcome photo. `clear` to reset.")
                elif action == "set_welcome_video":
                    self.inline_state[uid] = {"action": "adm_set_welcome_video"}
                    await callback_query.message.reply_text("Send welcome video. `clear` to reset.")
                elif action == "clear_welcome":
                    await self.update_admin_cfg({"welcome_text": "", "welcome_image": "", "welcome_video": ""})
                    await callback_query.message.reply_text("🧹 Welcome cleared.")
                elif action == "set_forcejoin":
                    self.inline_state[uid] = {"action": "adm_set_forcejoin"}
                    await callback_query.message.reply_text(
                        "Send channel username/ID.\n`clear` to disable."
                    )
                elif action == "set_global_delay":
                    self.inline_state[uid] = {"action": "adm_set_global_delay"}
                    await callback_query.message.reply_text("Send delay (seconds). `clear` to reset.")
                elif action == "set_shortener_api":
                    self.inline_state[uid] = {"action": "adm_set_shortener_api"}
                    await callback_query.message.reply_text("Send shortener API endpoint URL:")
                elif action == "set_shortener_key":
                    self.inline_state[uid] = {"action": "adm_set_shortener_key"}
                    await callback_query.message.reply_text("Send shortener API key:")
                elif action == "toggle_free6h":
                    cfg = await self.get_admin_cfg()
                    new_val = not cfg.get("free_6h_enabled", False)
                    await self.update_admin_cfg({"free_6h_enabled": new_val})
                    await callback_query.message.reply_text(
                        f"✅ Free 6h is now **{'ENABLED' if new_val else 'DISABLED'}**"
                    )
                elif action == "set_claim_limit":
                    self.inline_state[uid] = {"action": "adm_set_claim_limit"}
                    await callback_query.message.reply_text(
                        f"Current limit: {await self.get_free_claims_limit()}\nSend new (1-20):"
                    )
                elif action == "shortener_config":
                    cfg = await self.get_admin_cfg()
                    text = (
                        "⚙️ **Shortener Config**\n\n"
                        f"Method: `{cfg.get('shortener_method','auto').upper()}`\n"
                        f"Param name: `{cfg.get('shortener_param_name','url')}`\n"
                        f"Response key: `{cfg.get('shortener_response_key','auto')}`\n"
                        f"Auth type: `{cfg.get('shortener_auth_type','bearer')}`\n"
                        f"Header name: `{cfg.get('shortener_header_name','Authorization')}`"
                    )
                    await callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("Method", callback_data="adm_config_method")],
                        [InlineKeyboardButton("Param name", callback_data="adm_config_param")],
                        [InlineKeyboardButton("Response key", callback_data="adm_config_reskey")],
                        [InlineKeyboardButton("Auth type", callback_data="adm_config_authtype")],
                        [InlineKeyboardButton("Header name", callback_data="adm_config_headername")],
                    ]))
                elif action == "broadcast":
                    self.inline_state[uid] = {"action": "adm_broadcast_msg"}
                    await callback_query.message.reply_text("📡 Send broadcast message.")
                elif action == "create_promo":
                    await callback_query.message.reply_text("🎟 Choose plan:", reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⚡ 6 Hours",  callback_data="promo_dur_6_6Hours")],
                        [InlineKeyboardButton("☀️ 1 Day",   callback_data="promo_dur_24_1Day")],
                        [InlineKeyboardButton("📅 1 Week",  callback_data="promo_dur_168_1Week")],
                        [InlineKeyboardButton("🗓 1 Month", callback_data="promo_dur_720_1Month")],
                        [InlineKeyboardButton("🏆 1 Year",  callback_data="promo_dur_8760_1Year")],
                    ]))
                elif action == "list_promos":
                    promos = await self._load_promos()
                    if not promos:
                        await callback_query.message.reply_text("No promos.")
                    else:
                        lines = ["🎟 **Promos:**\n"]
                        for code, rec in promos.items():
                            uses = len(rec.get("used_by", []))
                            lines.append(f"• `{code}` — {rec['plan_label']} | {uses}/{rec.get('max_uses',1)}")
                        await callback_query.message.reply_text("\n".join(lines))
                elif action == "del_promo":
                    self.inline_state[uid] = {"action": "adm_del_promo"}
                    await callback_query.message.reply_text("Send promo code to delete:")
                elif action == "give_premium":
                    self.inline_state[uid] = {"action": "adm_give_uid"}
                    await callback_query.message.reply_text("Send user ID:")
                elif action == "revoke_premium":
                    self.inline_state[uid] = {"action": "adm_revoke_uid"}
                    await callback_query.message.reply_text("Send user ID:")
                elif action == "list_premium":
                    pdata = await self._load_premium()
                    if not pdata:
                        await callback_query.message.reply_text("No premium users.")
                    else:
                        lines = ["💎 **Premium:**\n"]
                        now = datetime.now(timezone.utc)
                        for suid, rec in pdata.items():
                            try:
                                exp = datetime.fromisoformat(rec["expiry"])
                                status = "✅" if exp > now else "❌"
                                lines.append(f"• `{suid}` {status} — {rec.get('plan','?')} | {exp.strftime('%Y-%m-%d')}")
                            except Exception:
                                pass
                        await callback_query.message.reply_text("\n".join(lines))
                elif action == "view_usage":
                    udata = await self._load_usage()
                    today = self._today_str()
                    lines = [f"📊 **Usage Today ({today}):**\n"]
                    for suid, rec in udata.items():
                        if rec.get("date") == today:
                            lines.append(f"• `{suid}` — {rec['count']}")
                    if len(lines) == 1:
                        lines.append("No usage today.")
                    await callback_query.message.reply_text("\n".join(lines))
                elif action == "view_sources":
                    src_logs = await self._load_src_log()
                    if not src_logs:
                        await callback_query.message.reply_text("No logs.")
                    else:
                        lines = ["📡 **Recent Sources:**\n"]
                        for entry in src_logs[-20:]:
                            lines.append(f"• `{entry.get('user_id')}` → {entry.get('source_link','N/A')}")
                        await callback_query.message.reply_text("\n".join(lines))
                elif action == "ban_user":
                    self.inline_state[uid] = {"action": "adm_ban_uid"}
                    await callback_query.message.reply_text("Send user ID (and reason):")
                elif action == "unban_user":
                    self.inline_state[uid] = {"action": "adm_unban_uid"}
                    await callback_query.message.reply_text("Send user ID:")
                elif action == "list_banned":
                    bans = await self._load_bans()
                    if not bans:
                        await callback_query.message.reply_text("✅ None banned.")
                    else:
                        lines = [f"🔨 **Banned ({len(bans)}):**\n"]
                        for b_uid, rec in bans.items():
                            lines.append(f"• `{b_uid}` — {rec.get('reason','No reason')}")
                        await callback_query.message.reply_text("\n".join(lines))
                elif action == "restore_chat":
                    self.inline_state[uid] = {"action": "adm_restore_uid"}
                    await callback_query.message.reply_text("Send user ID to restore:")
                elif action == "config_method":
                    self.inline_state[uid] = {"action": "adm_config_method"}
                    await callback_query.message.reply_text("Send GET/POST/AUTO:")
                elif action == "config_param":
                    self.inline_state[uid] = {"action": "adm_config_param"}
                    await callback_query.message.reply_text("Send param name:")
                elif action == "config_reskey":
                    self.inline_state[uid] = {"action": "adm_config_reskey"}
                    await callback_query.message.reply_text("Send response key (or `auto`):")
                elif action == "config_authtype":
                    self.inline_state[uid] = {"action": "adm_config_authtype"}
                    await callback_query.message.reply_text("Send bearer/apikey/none:")
                elif action == "config_headername":
                    self.inline_state[uid] = {"action": "adm_config_headername"}
                    await callback_query.message.reply_text("Send header name:")

            elif data.startswith("promo_dur_"):
                if uid != self.owner_id:
                    await callback_query.answer("❌", show_alert=True)
                    return
                await callback_query.answer()
                parts = data[len("promo_dur_"):].split("_", 1)
                hours = int(parts[0])
                label = parts[1] if len(parts) > 1 else f"{hours}h"
                self.inline_state[uid] = {"action": "adm_promo_uses", "hours": hours, "label": label}
                await callback_query.message.reply_text(
                    f"Plan: **{label}**\n\nHow many uses? (1-100)"
                )

            elif data.startswith("grant_plan_"):
                if uid != self.owner_id:
                    await callback_query.answer("❌", show_alert=True)
                    return
                await callback_query.answer()
                parts = data[len("grant_plan_"):].split("_", 2)
                hours = int(parts[0])
                target = int(parts[1])
                label = parts[2] if len(parts) > 2 else f"{hours}h"
                expiry = await self.grant_premium(target, hours, label)
                await callback_query.message.reply_text(
                    f"✅ Granted!\n👤 `{target}`\n📋 {label}\n⏰ {expiry.strftime('%Y-%m-%d %H:%M UTC')}"
                )
                try:
                    await self.app.send_message(
                        target,
                        f"🎉 **You got {label} Premium!**\n\n"
                        f"⏰ Expires: `{expiry.strftime('%Y-%m-%d %H:%M UTC')}`\n\n"
                        f"Enjoy! 🚀"
                    )
                except Exception as e:
                    log.warning(f"Notify user failed: {e}")

            elif data == "free_6h":
                await callback_query.answer()
                cfg = await self.get_admin_cfg()
                if not cfg.get("free_6h_enabled"):
                    await callback_query.message.reply_text("❌ Free 6h is disabled.")
                    return
                if not await self.can_claim_free(uid):
                    limit = await self.get_free_claims_limit()
                    await callback_query.message.reply_text(
                        f"⛔ You have used your **{limit} free claims** in the last 24h.\n"
                        "Try tomorrow or buy premium."
                    )
                    return
                shortener_url = cfg.get("shortener_api_url", "").strip()
                shortener_key = cfg.get("shortener_api_key", "").strip()
                if not shortener_url or not shortener_key:
                    await callback_query.message.reply_text(
                        "❌ Shortener not configured. Contact admin."
                    )
                    return

                bot_username = await self.get_bot_username()
                claim_code = await self.generate_claim(uid)
                deep_link = f"https://t.me/{bot_username}?start=claim_{claim_code}"
                wait_msg = await callback_query.message.reply_text("⏳ Generating link…")
                short_url = await self.shorten_url(deep_link)
                if short_url:
                    free_url = short_url
                    btn_text = "⚡ Click → Complete → Get Premium"
                    body = (
                        "⚡ **Get 6 Hours Premium for FREE!**\n\n"
                        "1️⃣ Click the button below\n"
                        "2️⃣ Complete the short link\n"
                        "3️⃣ Telegram opens automatically\n"
                        "4️⃣ Premium activates instantly! 🎉\n\n"
                        f"📌 Claim up to {await self.get_free_claims_limit()} times per 24h.\n"
                        "⏰ Link expires in 1 hour.\n"
                        "⚠️ Each link is unique."
                    )
                else:
                    free_url = deep_link
                    btn_text = "⚡ Activate Free 6h Premium"
                    body = (
                        "⚡ **Get 6 Hours Premium for FREE!**\n\n"
                        "Click to activate.\n\n"
                        f"📌 Claim up to {await self.get_free_claims_limit()} times per 24h.\n"
                        "⏰ Link expires in 1 hour.\n"
                        "⚠️ Each link is unique."
                    )
                await self.record_free_claim(uid)
                try:
                    await wait_msg.edit_text(
                        body,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton(btn_text, url=free_url)
                        ]])
                    )
                except Exception:
                    await callback_query.message.reply_text(
                        body,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton(btn_text, url=free_url)
                        ]])
                    )

            elif data.startswith("payqr_"):
                await callback_query.answer("Generating QR…")
                plan_key = data[len("payqr_"):]
                plan_labels = {"day": "1 Day", "week": "1 Week", "month": "1 Month", "year": "1 Year"}
                plan_prices = {"day": "₹3", "week": "₹20", "month": "₹80", "year": "₹900"}
                label = plan_labels.get(plan_key, plan_key)
                price = plan_prices.get(plan_key, "")
                cfg = await self.get_admin_cfg()
                upi_to_show = cfg.get("upi_id") or cfg.get("extracted_upi") or ""
                if not upi_to_show:
                    await callback_query.message.reply_text(
                        "❌ UPI not set. Contact @akashh955956."
                    )
                    return
                caption = (
                    f"📲 **Payment QR — {label} ({price})**\n\n"
                    f"💰 Amount: **{price}**\n"
                    f"Scan with GPay/PhonePe/Paytm.\n"
                )
                if cfg["phone"]:
                    caption += f"\n📞 Phone: `{cfg['phone']}`"
                caption += "\n\n✅ Send payment screenshot to this bot."
                self.inline_state[uid] = {
                    "action": "payment_screenshot", "plan": plan_key,
                    "label": label, "price": price,
                }
                qr_bytes = self.generate_payment_qr(plan_key, upi_to_show)
                if qr_bytes:
                    try:
                        bio = io.BytesIO(qr_bytes)
                        bio.name = f"upi_qr_{plan_key}.png"
                        await callback_query.message.reply_photo(bio, caption=caption)
                        return
                    except Exception as e:
                        log.error(f"QR send failed: {e}")
                qr_file_id = cfg.get("qr_file_id")
                if qr_file_id:
                    try:
                        await callback_query.message.reply_photo(qr_file_id, caption=caption)
                        return
                    except Exception:
                        pass
                await callback_query.message.reply_text(caption + "\n\n⚠️ QR unavailable.")
                self.inline_state.pop(uid, None)

            else:
                await callback_query.answer()

        # ─── MAIN MESSAGE HANDLER ─────────────────────────────
        COMMANDS = [
            "start", "help", "clearchannel",
            "addreplace", "delreplace", "adddelete", "deldelete",
            "setprefix", "setsuffix", "settings", "clearrules", "cancel",
            "tracklog", "trackstats", "trackwipe", "srclog", "batch",
            "getpremium", "redeem", "adminpanel", "givepremium",
            "revokepremium", "mystatus", "ban", "unban", "banned",
            "restore", "setforcechannel", "setdelay", "mydelay",
            "setglobaldelay", "broadcast", "testshortener",
        ]

        @app.on_message(allowed & ~filters.command(COMMANDS))
        async def handle_message(_, msg: Message):
            uid = msg.from_user.id
            text = msg.text or msg.caption or ""

            if not await self.enforce_force_join(msg, uid):
                return

            if uid in self.inline_state:
                state = self.inline_state[uid]
                action = state["action"]
                if text.startswith("/"):
                    return
                cfg = await self.get_user_cfg(uid)

                if action == "adm_broadcast_msg":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    del self.inline_state[uid]
                    await self.execute_broadcast(msg, msg)
                    return

                if action == "adm_set_claim_limit":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    try:
                        val = int(text.strip())
                        if 1 <= val <= 20:
                            await self.update_admin_cfg({"free_claims_limit": val})
                            del self.inline_state[uid]
                            await msg.reply_text(f"✅ Limit: **{val}**.")
                        else:
                            await msg.reply_text("⚠️ 1-20 only.")
                    except ValueError:
                        await msg.reply_text("⚠️ Invalid number.")
                    return

                if action == "adm_config_method":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    val = text.strip().upper()
                    if val in ("GET", "POST", "AUTO"):
                        await self.update_admin_cfg({"shortener_method": val.lower()})
                        del self.inline_state[uid]
                        await msg.reply_text(f"✅ Method: {val}.")
                    else:
                        await msg.reply_text("⚠️ GET/POST/AUTO.")
                    return

                if action == "adm_config_param":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    await self.update_admin_cfg({"shortener_param_name": text.strip()})
                    del self.inline_state[uid]
                    await msg.reply_text(f"✅ Param: `{text.strip()}`.")
                    return

                if action == "adm_config_reskey":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    val = text.strip()
                    await self.update_admin_cfg({"shortener_response_key": val if val != "auto" else ""})
                    del self.inline_state[uid]
                    await msg.reply_text(f"✅ Resp key: `{val or 'auto'}`.")
                    return

                if action == "adm_config_authtype":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    val = text.strip().lower()
                    if val in ("bearer", "apikey", "none"):
                        await self.update_admin_cfg({"shortener_auth_type": val})
                        del self.inline_state[uid]
                        await msg.reply_text(f"✅ Auth: {val}.")
                    else:
                        await msg.reply_text("⚠️ bearer/apikey/none.")
                    return

                if action == "adm_config_headername":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    await self.update_admin_cfg({"shortener_header_name": text.strip()})
                    del self.inline_state[uid]
                    await msg.reply_text(f"✅ Header: `{text.strip()}`.")
                    return

                if action == "set_channel":
                    del self.inline_state[uid]
                    inp = text.strip()
                    if inp.lower() == "clear":
                        cfg["channel"] = None
                        cfg["topic"] = None
                        await self.set_user_cfg(uid, cfg)
                        await msg.reply_text("🗑️ Cleared.")
                        return
                    if inp.lstrip("-").isdigit():
                        channel_id = int(inp)
                        try:
                            chat = await self.app.get_chat(channel_id)
                            ok, reason = await self.check_bot_is_admin(channel_id)
                            if not ok:
                                await msg.reply_text(f"⚠️ Bot not admin there.\n{reason}")
                        except Exception as e:
                            await msg.reply_text(f"❌ Invalid: `{e}`")
                            return
                        cfg["channel"] = channel_id
                        await self.set_user_cfg(uid, cfg)
                        await msg.reply_text(f"✅ Destination: `{channel_id}`")
                    else:
                        try:
                            if "t.me/" in inp:
                                inp = inp.split("t.me/")[-1]
                            chat = await self.app.get_chat(inp)
                            cfg["channel"] = chat.id
                            await self.set_user_cfg(uid, cfg)
                            await msg.reply_text(f"✅ Destination: `{chat.id}` ({chat.title})")
                        except Exception as e:
                            await msg.reply_text(f"❌ Could not resolve: `{e}`")
                    return

                if action == "set_topic":
                    del self.inline_state[uid]
                    inp = text.strip()
                    if inp in ("", "0") or inp.lower() == "general":
                        cfg["topic"] = None
                        await self.set_user_cfg(uid, cfg)
                        await msg.reply_text("✅ Topic: General.")
                        return
                    try:
                        cfg["topic"] = int(inp)
                        await self.set_user_cfg(uid, cfg)
                        await msg.reply_text(f"✅ Topic: `{cfg['topic']}`")
                    except ValueError:
                        await msg.reply_text("⚠️ Invalid integer.")
                    return

                if action == "payment_screenshot":
                    if not msg.photo:
                        await msg.reply_text("⚠️ Send a photo.")
                        return
                    plan = state.get("plan", "Unknown")
                    label = state.get("label", "Unknown")
                    price = state.get("price", "Unknown")
                    user = msg.from_user
                    user_info = f"👤 {user.first_name} {user.last_name or ''} (@{user.username or 'none'}) ID: `{user.id}`"
                    caption = (
                        f"💳 **New Payment Screenshot**\n\n"
                        f"{user_info}\n"
                        f"📋 Plan: **{label}**\n"
                        f"💰 Amount: {price}\n"
                        f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                    )
                    try:
                        await self.app.send_photo(self.owner_id, msg.photo.file_id, caption=caption)
                        await msg.reply_text(
                            "✅ Screenshot sent to admin! You will be notified shortly."
                        )
                        del self.inline_state[uid]
                    except Exception as e:
                        log.error(f"Screenshot forward failed: {e}")
                        await msg.reply_text("❌ Could not send. Contact @akashh955956 directly.")
                        del self.inline_state[uid]
                    return

                if action == "replace_old":
                    old_word = text.strip()
                    if not old_word:
                        await msg.reply_text("⚠️ Send a valid word.")
                        return
                    self.inline_state[uid] = {"action": "replace_new", "old": old_word}
                    await msg.reply_text(f"Word: `{old_word}`\nSend the replacement (space to delete).")
                    return

                if action == "replace_new":
                    old_word = state.get("old")
                    if not old_word:
                        del self.inline_state[uid]
                        return
                    new_word = text.strip()
                    reps = cfg.get("replacements", {})
                    if len(reps) >= 2 and old_word not in reps:
                        del self.inline_state[uid]
                        return await msg.reply_text("⚠️ Max 2 replacement rules.")
                    reps[old_word] = new_word
                    await self.set_user_cfg(uid, cfg)
                    del self.inline_state[uid]
                    await msg.reply_text(f"✅ `{old_word}` → `{new_word or '(delete)'}`.")
                    return

                if action == "delete_word":
                    word = text.strip()
                    if not word:
                        await msg.reply_text("⚠️ Send a valid word.")
                        return
                    if word not in cfg.get("deletions", []):
                        cfg.setdefault("deletions", []).append(word)
                        await self.set_user_cfg(uid, cfg)
                    del self.inline_state[uid]
                    await msg.reply_text(f"✅ `{word}` will be deleted.")
                    return

                if action == "set_prefix":
                    prefix = text.strip()
                    cfg["prefix"] = "" if prefix == " " else prefix
                    await self.set_user_cfg(uid, cfg)
                    del self.inline_state[uid]
                    await msg.reply_text(f"✅ Prefix: `{cfg['prefix'] or '(removed)'}`")
                    return

                if action == "set_suffix":
                    suffix = text.strip()
                    cfg["suffix"] = "" if suffix == " " else suffix
                    await self.set_user_cfg(uid, cfg)
                    del self.inline_state[uid]
                    await msg.reply_text(f"✅ Suffix: `{cfg['suffix'] or '(removed)'}`")
                    return

                if action == "redeem_promo":
                    code = text.strip().upper()
                    del self.inline_state[uid]
                    ok, reply = await self.redeem_promo(code, uid)
                    await msg.reply_text(reply)
                    return

                if action == "adm_set_upi":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    await self.update_admin_cfg({"upi_id": text.strip()})
                    del self.inline_state[uid]
                    await msg.reply_text(f"✅ UPI: `{text.strip()}`")
                    return

                if action == "adm_set_phone":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    await self.update_admin_cfg({"phone": text.strip()})
                    del self.inline_state[uid]
                    await msg.reply_text(f"✅ Phone: `{text.strip()}`")
                    return

                if action == "adm_set_qr":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    if msg.photo:
                        try:
                            qr_path = await self.app.download_media(
                                msg, file_name=str(self.data_dir / "admin_qr.png")
                            )
                            extracted = self.extract_upi_from_qr_image(qr_path) if qr_path else None
                            await self.update_admin_cfg({
                                "qr_file_id": msg.photo.file_id,
                                "qr_local_path": qr_path or "",
                                "extracted_upi": extracted or "",
                            })
                            del self.inline_state[uid]
                            if extracted:
                                await msg.reply_text(f"✅ QR saved!\n📱 UPI: `{extracted}`")
                            else:
                                await msg.reply_text("✅ QR saved! (Could not extract UPI)")
                        except Exception as e:
                            log.error(f"QR save error: {e}")
                            await self.update_admin_cfg({"qr_file_id": msg.photo.file_id})
                            del self.inline_state[uid]
                            await msg.reply_text("✅ QR saved (partial).")
                    else:
                        await msg.reply_text("⚠️ Send a photo.")
                    return

                if action == "adm_set_url":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    await self.update_admin_cfg({"url_shortener": text.strip()})
                    del self.inline_state[uid]
                    await msg.reply_text(f"✅ URL: `{text.strip()}`")
                    return

                if action == "adm_set_rzp_key":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    await self.update_admin_cfg({"razorpay_key": text.strip()})
                    self.inline_state[uid] = {"action": "adm_set_rzp_secret"}
                    await msg.reply_text("✅ Key saved. Now send Secret:")
                    return

                if action == "adm_set_rzp_secret":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    await self.update_admin_cfg({"razorpay_secret": text.strip()})
                    del self.inline_state[uid]
                    await msg.reply_text("✅ Razorpay saved.")
                    return

                if action == "adm_set_welcome_text":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    new_text = (msg.text or msg.caption or "").strip()
                    if new_text.lower() == "clear":
                        await self.update_admin_cfg({"welcome_text": ""})
                        del self.inline_state[uid]
                        await msg.reply_text("🧹 Cleared.")
                    elif not new_text:
                        await msg.reply_text("⚠️ Empty text.")
                    else:
                        await self.update_admin_cfg({"welcome_text": new_text})
                        del self.inline_state[uid]
                        preview = (new_text
                                   .replace("{name}", msg.from_user.first_name or "User")
                                   .replace("{status}", "🆓 Free"))
                        await msg.reply_text(f"✅ Set!\n\n**Preview:**\n{preview}")
                    return

                if action == "adm_set_welcome_image":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    if msg.text and msg.text.strip().lower() == "clear":
                        await self.update_admin_cfg({"welcome_image": ""})
                        del self.inline_state[uid]
                        await msg.reply_text("🧹 Cleared.")
                        return
                    if not msg.photo:
                        await msg.reply_text("⚠️ Send a photo or `clear`.")
                        return
                    await self.update_admin_cfg({"welcome_image": msg.photo.file_id})
                    del self.inline_state[uid]
                    await msg.reply_text("✅ Image saved.")
                    return

                if action == "adm_set_welcome_video":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    if msg.text and msg.text.strip().lower() == "clear":
                        await self.update_admin_cfg({"welcome_video": ""})
                        del self.inline_state[uid]
                        await msg.reply_text("🧹 Cleared.")
                        return
                    if not msg.video:
                        await msg.reply_text("⚠️ Send a video or `clear`.")
                        return
                    await self.update_admin_cfg({"welcome_video": msg.video.file_id})
                    del self.inline_state[uid]
                    await msg.reply_text("✅ Video saved.")
                    return

                if action == "adm_set_forcejoin":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    ok, message = await self.validate_and_set_backup_channel(text.strip())
                    del self.inline_state[uid]
                    await msg.reply_text(message)
                    return

                if action == "adm_set_global_delay":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    arg = text.strip()
                    if arg.lower() == "clear":
                        await self.update_admin_cfg({"global_delay": None})
                        del self.inline_state[uid]
                        return await msg.reply_text("✅ Cleared.")
                    try:
                        seconds = float(arg)
                        if seconds <= 0:
                            return await msg.reply_text("⚠️ Positive only.")
                    except ValueError:
                        return await msg.reply_text("⚠️ Invalid number.")
                    await self.update_admin_cfg({"global_delay": seconds})
                    del self.inline_state[uid]
                    await msg.reply_text(f"✅ Delay: {seconds}s.")
                    return

                if action == "adm_set_shortener_api":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    await self.update_admin_cfg({"shortener_api_url": text.strip()})
                    del self.inline_state[uid]
                    await msg.reply_text("✅ Shortener URL saved.")
                    return

                if action == "adm_set_shortener_key":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    await self.update_admin_cfg({"shortener_api_key": text.strip()})
                    del self.inline_state[uid]
                    await msg.reply_text("✅ Shortener key saved.")
                    return

                if action == "adm_promo_uses":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    if not text.strip().isdigit():
                        await msg.reply_text("⚠️ Send a number 1-100.")
                        return
                    max_uses = max(1, min(100, int(text.strip())))
                    hours = state.get("hours", 24)
                    label = state.get("label", "Custom")
                    code = self.generate_promo_code()
                    await self.create_promo(code, hours, label, max_uses)
                    del self.inline_state[uid]
                    await msg.reply_text(
                        f"🎟 **Promo Created!**\n\n`{code}`\n{label}\n{hours}h\n{max_uses} uses"
                    )
                    return

                if action == "adm_del_promo":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    code = text.strip().upper()
                    del self.inline_state[uid]
                    if await self.delete_promo(code):
                        await msg.reply_text(f"🗑️ Deleted `{code}`.")
                    else:
                        await msg.reply_text(f"⚠️ `{code}` not found.")
                    return

                if action == "adm_give_uid":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    if not text.strip().lstrip("-").isdigit():
                        await msg.reply_text("⚠️ Invalid user ID.")
                        return
                    target = int(text.strip())
                    del self.inline_state[uid]
                    await msg.reply_text(
                        f"👤 User: `{target}`\n\nChoose plan:",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⚡ 6 Hours",  callback_data=f"grant_plan_6_{target}_6Hours"),
                             InlineKeyboardButton("☀️ 1 Day",   callback_data=f"grant_plan_24_{target}_1Day")],
                            [InlineKeyboardButton("📅 1 Week",  callback_data=f"grant_plan_168_{target}_1Week"),
                             InlineKeyboardButton("🗓 1 Month", callback_data=f"grant_plan_720_{target}_1Month")],
                            [InlineKeyboardButton("🏆 1 Year",  callback_data=f"grant_plan_8760_{target}_1Year")],
                        ])
                    )
                    return

                if action == "adm_revoke_uid":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    if not text.strip().lstrip("-").isdigit():
                        await msg.reply_text("⚠️ Invalid user ID.")
                        return
                    await self.revoke_premium(int(text.strip()))
                    del self.inline_state[uid]
                    await msg.reply_text(f"🚫 Revoked for `{text.strip()}`.")
                    return

                if action == "adm_ban_uid":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    parts = text.strip().split(maxsplit=1)
                    if not parts[0].lstrip("-").isdigit():
                        await msg.reply_text("⚠️ Invalid user ID.")
                        return
                    target = int(parts[0])
                    reason = parts[1].strip() if len(parts) > 1 else "Banned by admin"
                    if target == self.owner_id:
                        del self.inline_state[uid]
                        await msg.reply_text("❌ Can't ban owner.")
                        return
                    await self.ban_user(target, reason, banned_by=uid)
                    del self.inline_state[uid]
                    await msg.reply_text(f"🔨 Banned `{target}`.\n📝 {reason}")
                    try:
                        await self.app.send_message(target, f"🚫 **Banned**\n\n📝 {reason}")
                    except Exception:
                        pass
                    return

                if action == "adm_unban_uid":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    if not text.strip().lstrip("-").isdigit():
                        await msg.reply_text("⚠️ Invalid user ID.")
                        return
                    target = int(text.strip())
                    del self.inline_state[uid]
                    if await self.unban_user(target):
                        await msg.reply_text(f"✅ Unbanned `{target}`.")
                        try:
                            await self.app.send_message(target, "✅ You have been unbanned!")
                        except Exception:
                            pass
                    else:
                        await msg.reply_text(f"⚠️ `{target}` not banned.")
                    return

                if action == "adm_restore_uid":
                    if uid != self.owner_id:
                        del self.inline_state[uid]
                        return
                    if not text.strip().lstrip("-").isdigit():
                        await msg.reply_text("⚠️ Invalid user ID.")
                        return
                    target = int(text.strip())
                    history = await self.get_chat_log(target)
                    del self.inline_state[uid]
                    if not history:
                        await msg.reply_text(f"📭 No history for `{target}`.")
                        return
                    await msg.reply_text(f"♻️ Restoring {len(history)} messages…")
                    restored = 0
                    for entry in history:
                        try:
                            ts = entry.get("ts", "?")
                            u_name = entry.get("username") or entry.get("full_name") or f"ID:{target}"
                            content = entry.get("text") or entry.get("caption") or f"[{entry.get('type','media')}]"
                            await self.app.send_message(uid, f"━━━━━━━━━━━━━━━\n👤 {u_name} | 🕐 {ts}\n{content}")
                            restored += 1
                            await asyncio.sleep(0.3)
                        except Exception as e:
                            log.error(f"Restore error: {e}")
                    await msg.reply_text(f"✅ Restored `{restored}` / `{len(history)}`.")
                    return

                del self.inline_state[uid]
                return

            cfg = await self.get_user_cfg(uid)
            dest = cfg.get("channel")
            topic = cfg.get("topic")

            if msg.text or msg.caption or msg.media:
                msg_type = "text"
                if msg.photo: msg_type = "photo"
                elif msg.video: msg_type = "video"
                elif msg.document: msg_type = "document"
                elif msg.audio: msg_type = "audio"
                elif msg.voice: msg_type = "voice"
                elif msg.sticker: msg_type = "sticker"
                elif msg.animation: msg_type = "animation"
                await self.save_chat_message(uid, {
                    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "msg_id": msg.id,
                    "user_id": uid,
                    "username": f"@{msg.from_user.username}" if msg.from_user.username else None,
                    "full_name": " ".join(filter(None, [msg.from_user.first_name, msg.from_user.last_name])),
                    "type": msg_type,
                    "text": msg.text or None,
                    "caption": msg.caption or None,
                })

            def resolve_dest_and_topic():
                d, t = dest, topic
                if not d:
                    d, t = msg.chat.id, None
                return d, t

            if uid in self.pending_bulk:
                pending = self.pending_bulk.pop(uid)
                qty_text = text.strip()
                if not qty_text.isdigit() or int(qty_text) < 1:
                    return await msg.reply_text(
                        "⚠️ Send a valid number.\nSend the link again to restart."
                    )
                quantity = int(qty_text)
                if quantity > 10_000_000:
                    return await msg.reply_text("⚠️ Max 10,000,000.")
                d, t = resolve_dest_and_topic()
                if d != msg.chat.id:
                    ok, reason = await self.check_bot_is_admin(d)
                    if not ok:
                        return await msg.reply_text(reason)
                if uid in self.active_tasks and not self.active_tasks[uid].done():
                    self.active_tasks[uid].cancel()
                bulk_cfg = cfg.copy()
                bulk_cfg["channel"] = d
                bulk_cfg["topic"] = t
                source_link = pending.get("source_link", "")
                task = asyncio.create_task(
                    self.run_bulk_copy(msg, uid, pending["chat_id"],
                                      pending["start_msg_id"], quantity, bulk_cfg, source_link)
                )
                self.active_tasks[uid] = task
                return

            parsed = self.parse_tme_link(text)
            if parsed:
                if not await self.is_premium(uid):
                    allowed_copy, remaining = await self.check_limit(uid, 1)
                    if not allowed_copy:
                        await msg.reply_text(
                            "⛔ **Daily limit reached!**",
                            reply_markup=await self.build_premium_keyboard()
                        )
                        return
                d, t = resolve_dest_and_topic()
                if d != msg.chat.id:
                    ok, reason = await self.check_bot_is_admin(d)
                    if not ok:
                        return await msg.reply_text(reason)
                chat_ref, msg_id = parsed
                source_link = self.extract_full_link(text) or ""
                self.pending_bulk[uid] = {
                    "chat_id": chat_ref,
                    "start_msg_id": msg_id,
                    "source_link": source_link,
                }
                await self.track(msg.from_user, "link_detected", source=chat_ref, dest=d, msg_id=msg_id)
                await msg.reply_text(
                    f"🔗 **Link detected!**\n\n"
                    f"📌 Start ID: `{msg_id}`\n"
                    f"📤 Destination: `{d}`\n\n"
                    f"**How many messages to copy?**\n_(Reply with a number)_\n\n`/cancel` to abort."
                )
                return

            if msg.forward_from_chat or msg.forward_from:
                allowed_copy, remaining = await self.check_limit(uid, 1)
                if not allowed_copy:
                    await msg.reply_text("⛔ Daily limit reached.",
                        reply_markup=await self.build_premium_keyboard())
                    return
                d, t = resolve_dest_and_topic()
                if d != msg.chat.id:
                    ok_admin, reason = await self.check_bot_is_admin(d)
                    if not ok_admin:
                        return await msg.reply_text(reason)
                if msg.forward_from_chat:
                    source_chat_id = msg.forward_from_chat.id
                    source_username = msg.forward_from_chat.username
                else:
                    source_chat_id = msg.forward_from.id
                    source_username = msg.forward_from.username
                if source_username:
                    fwd_link = f"https://t.me/{source_username}/{msg.forward_from_message_id or 1}"
                elif msg.forward_from_chat:
                    chat_id_str = str(source_chat_id).replace("-100", "")
                    fwd_link = f"https://t.me/c/{chat_id_str}/{msg.forward_from_message_id or 1}"
                else:
                    fwd_link = f"Forward from ID {source_chat_id}"
                asyncio.create_task(self.send_src_tracker(
                    msg.from_user, source_chat_id,
                    msg.forward_from_message_id or 1, 1,
                    fwd_link, "SINGLE FORWARD"
                ))
                temp_cfg = cfg.copy()
                temp_cfg["channel"] = d
                temp_cfg["topic"] = t
                status = await msg.reply_text("⏳ Copying…")
                ok = await self.copy_one(msg, temp_cfg, uid)
                if ok and not await self.is_premium(uid):
                    await self.increment_usage(uid, 1)
                await self.track(msg.from_user, "single_copy",
                      source=source_chat_id, dest=d,
                      copied=1 if ok else 0, failed=0 if ok else 1)
                try:
                    await status.edit_text("✅ Copied!" if ok else "❌ Failed.")
                except Exception:
                    pass
                return

        @app.on_chat_member_updated()
        async def on_chat_member_update(client, update):
            try:
                me = await client.get_me()
                if update.new_chat_member and update.new_chat_member.user.id == me.id:
                    await self.add_broadcast_chat(update.chat.id)
                    log.info(f"Added chat {update.chat.id} to broadcast list.")
            except Exception as e:
                log.error(f"on_chat_member_update error: {e}")

    # ─── Start / stop ─────────────────────────────────────────
    async def start(self):
        await self.app.start()
        log.info(f"Bot {self.token[:8]}... started.")

    async def stop(self):
        for uid, task in list(self.active_tasks.items()):
            if not task.done():
                self.cancel_flags[uid] = True
                task.cancel()
        await asyncio.gather(*[t for t in self.active_tasks.values() if not t.done()],
                             return_exceptions=True)
        try:
            await self.app.stop()
        except Exception:
            pass
        log.info(f"Bot {self.token[:8]}... stopped.")


# ═══════════════════════════════════════════════════════════════
# HEALTH SERVER (for Render / UptimeRobot)
# ═══════════════════════════════════════════════════════════════
async def start_health_server():
    """Simple HTTP server so Render detects an open port.
    UptimeRobot will ping /health every 5 min to prevent sleep."""
    start_time = datetime.now(timezone.utc)

    async def health(request):
        uptime = (datetime.now(timezone.utc) - start_time).total_seconds()
        return web.json_response({
            "status": "ok",
            "uptime_seconds": int(uptime),
            "bots": len(BOT_CONFIGS),
        })

    async def root(request):
        return web.Response(
            text="🤖 Multi-Bot is running 24/7",
            content_type="text/plain"
        )

    health_app = web.Application()
    health_app.router.add_get("/", root)
    health_app.router.add_get("/health", health)
    health_app.router.add_get("/healthz", health)

    runner = web.AppRunner(health_app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info(f"✅ Health server listening on 0.0.0.0:{port}")
    return runner


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
async def main():
    # 1. Start health server FIRST (Render needs the port open)
    health_runner = await start_health_server()

    # 2. Start all bots (polling mode)
    bots = []
    for config in BOT_CONFIGS:
        try:
            bot = BotApp(
                token=config["token"],
                api_id=config["api_id"],
                api_hash=config["api_hash"],
                owner_id=config["owner_id"],
                data_dir=config["data_dir"],
                session_name=config["session_name"],
            )
            await bot.start()
            bots.append(bot)
        except Exception as e:
            log.error(f"❌ Failed to start bot {config['token'][:8]}: {e}")

    log.info(f"🚀 {len(bots)}/{len(BOT_CONFIGS)} bots running 24/7. Health: /health")

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        for bot in bots:
            try:
                await bot.stop()
            except Exception:
                pass
        try:
            await health_runner.cleanup()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down...")