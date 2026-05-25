from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, PreCheckoutQuery
from dotenv import load_dotenv

try:
    from py3xui import AsyncApi, Client
except ImportError:  # pragma: no cover
    AsyncApi = None
    Client = None


load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("astrovpn")
UTC = timezone.utc


# ══════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════

def parse_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_admin_ids() -> list[int]:
    raw = os.getenv("BOT_ADMINS") or os.getenv("ADMIN_IDS") or os.getenv("ADMIN_ID") or ""
    result: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            result.append(int(part))
    return list(dict.fromkeys(result))


@dataclass(frozen=True)
class ServerConfig:
    id: int
    name: str
    host: str
    server_ip: str
    max_clients: int
    inbound_id: Optional[int] = None
    subscription_base: str = ""
    vless_port: int = 443
    vless_sni: str = "www.apple.com"
    vless_fingerprint: str = "chrome"
    vless_flow: str = "xtls-rprx-vision"
    vless_public_key: str = "PUBLIC_KEY"
    vless_short_id: str = "SHORT_ID"


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: list[int]
    support_username: str
    port: int
    db_file: str
    price_rub: int
    stars_amount: int
    subscription_days: int
    max_devices: int
    payment_stars_enabled: bool
    payment_tribute_enabled: bool
    tribute_link: str
    tribute_expected_event: str
    tribute_secret: str
    xui_username: str
    xui_password: str
    xui_token: Optional[str]
    xui_dynamic_inbound: bool
    servers: list[ServerConfig]

    @classmethod
    def from_env(cls) -> "Config":
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        if not bot_token:
            raise RuntimeError("BOT_TOKEN is required")

        admin_ids = parse_admin_ids()
        if not admin_ids:
            raise RuntimeError("ADMIN_ID, ADMIN_IDS or BOT_ADMINS must contain at least one Telegram user id")

        payment_stars_enabled = parse_bool("PAYMENT_STARS_ENABLED", parse_bool("SHOP_PAYMENT_STARS_ENABLED", True))
        payment_tribute_enabled = parse_bool("PAYMENT_TRIBUTE_ENABLED", True)
        if not payment_stars_enabled and not payment_tribute_enabled:
            logger.warning("No payment method is enabled; Telegram Stars will be enabled as safe default.")
            payment_stars_enabled = True

        servers = load_servers_from_env()
        return cls(
            bot_token=bot_token,
            admin_ids=admin_ids,
            support_username=os.getenv("SUPPORT_USERNAME", "@support").strip(),
            port=int(os.getenv("PORT", "8080")),
            db_file=os.getenv("DB_FILE", "astrovpn.sqlite3").strip(),
            price_rub=int(os.getenv("PRICE_RUB", "199")),
            stars_amount=int(os.getenv("STARS_AMOUNT", "199")),
            subscription_days=int(os.getenv("SUBSCRIPTION_DAYS", "30")),
            max_devices=int(os.getenv("MAX_DEVICES", os.getenv("XUI_IP_LIMIT", "1"))),
            payment_stars_enabled=payment_stars_enabled,
            payment_tribute_enabled=payment_tribute_enabled,
            tribute_link=os.getenv("TRIBUTE_LINK", "https://t.me/tribute/app?startapp=dI5p").strip(),
            tribute_expected_event=os.getenv("TRIBUTE_EVENT", "new_donation").strip(),
            tribute_secret=os.getenv("TRIBUTE_SECRET", "").strip(),
            xui_username=os.getenv("XUI_USERNAME", "").strip(),
            xui_password=os.getenv("XUI_PASSWORD", "").strip(),
            xui_token=os.getenv("XUI_TOKEN") or None,
            xui_dynamic_inbound=parse_bool("XUI_DYNAMIC_INBOUND", False),
            servers=servers,
        )


def load_servers_from_env() -> list[ServerConfig]:
    raw = os.getenv("XUI_SERVERS_JSON", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            servers: list[ServerConfig] = []
            for index, item in enumerate(data, start=1):
                server_id = int(item.get("id", index))
                host = str(item["host"]).rstrip("/")
                server_ip = str(item.get("server_ip") or item.get("ip") or host.replace("https://", "").replace("http://", "").split(":")[0])
                servers.append(
                    ServerConfig(
                        id=server_id,
                        name=str(item.get("name", f"server-{server_id}")),
                        host=host,
                        server_ip=server_ip,
                        max_clients=int(item.get("max_clients", os.getenv("MAX_CLIENTS", "100"))),
                        inbound_id=int(item["inbound_id"]) if item.get("inbound_id") is not None else None,
                        subscription_base=str(item.get("subscription_base", os.getenv("XUI_SUBSCRIPTION_BASE", ""))).strip(),
                        vless_port=int(item.get("vless_port", os.getenv("VLESS_PORT", "443"))),
                        vless_sni=str(item.get("vless_sni", os.getenv("VLESS_SNI", "www.apple.com"))),
                        vless_fingerprint=str(item.get("vless_fingerprint", os.getenv("VLESS_FINGERPRINT", "chrome"))),
                        vless_flow=str(item.get("vless_flow", os.getenv("VLESS_FLOW", "xtls-rprx-vision"))),
                        vless_public_key=str(item.get("vless_public_key", os.getenv("VLESS_PUBLIC_KEY", "PUBLIC_KEY"))),
                        vless_short_id=str(item.get("vless_short_id", os.getenv("VLESS_SHORT_ID", "SHORT_ID"))),
                    )
                )
            if servers:
                return servers
        except Exception as exc:
            raise RuntimeError(f"Invalid XUI_SERVERS_JSON: {exc}") from exc

    server_ip = os.getenv("SERVER_IP", "89.127.207.207").strip()
    xui_host = os.getenv("XUI_HOST", f"https://{server_ip}:2053").strip().rstrip("/")
    return [
        ServerConfig(
            id=int(os.getenv("SERVER_ID", "1")),
            name=os.getenv("SERVER_NAME", "primary"),
            host=xui_host,
            server_ip=server_ip,
            max_clients=int(os.getenv("MAX_CLIENTS", "100")),
            inbound_id=int(os.getenv("XUI_INBOUND_ID")) if os.getenv("XUI_INBOUND_ID") else None,
            subscription_base=os.getenv("XUI_SUBSCRIPTION_BASE", "").strip(),
            vless_port=int(os.getenv("VLESS_PORT", "443")),
            vless_sni=os.getenv("VLESS_SNI", "www.apple.com").strip(),
            vless_fingerprint=os.getenv("VLESS_FINGERPRINT", "chrome").strip(),
            vless_flow=os.getenv("VLESS_FLOW", "xtls-rprx-vision").strip(),
            vless_public_key=os.getenv("VLESS_PUBLIC_KEY", "PUBLIC_KEY").strip(),
            vless_short_id=os.getenv("VLESS_SHORT_ID", "SHORT_ID").strip(),
        )
    ]


config = Config.from_env()
bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


# ══════════════════════════════════════════════════════
#  TIME AND FORMATTING
# ══════════════════════════════════════════════════════

def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def from_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def fmt_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    return dt.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")


def ms_timestamp(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def safe_name(user: types.User) -> str:
    return user.full_name or user.first_name or f"user_{user.id}"


def is_admin(user_id: int) -> bool:
    return user_id in config.admin_ids


# ══════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════

class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()

    def _has_column(self, table: str, column: str) -> bool:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row["name"] == column for row in rows)

    def _add_column(self, table: str, column: str, definition: str) -> None:
        if not self._has_column(table, column):
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def init(self) -> None:
        async with self.lock:
            self.conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    tg_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    username TEXT,
                    vpn_id TEXT NOT NULL UNIQUE,
                    server_id INTEGER,
                    subscription_expires TEXT,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    expiry_notified_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    plan TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payment_id TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(tg_id)
                );

                CREATE TABLE IF NOT EXISTS pending_payments (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    amount_rub INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS processed_events (
                    event_hash TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._add_column("users", "username", "TEXT")
            self._add_column("users", "server_id", "INTEGER")
            self._add_column("users", "expiry_notified_at", "TEXT")
            self.conn.commit()

    async def ensure_user(self, tg_id: int, name: str, username: Optional[str] = None) -> sqlite3.Row:
        current = now_utc()
        async with self.lock:
            row = self.conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE users SET name = ?, username = ?, updated_at = ? WHERE tg_id = ?",
                    (name, username, to_iso(current), tg_id),
                )
            else:
                self.conn.execute(
                    """
                    INSERT INTO users (tg_id, name, username, vpn_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (tg_id, name, username, str(uuid.uuid4()), to_iso(current), to_iso(current)),
                )
            self.conn.commit()
            return self.conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()

    async def get_user(self, tg_id: int) -> Optional[sqlite3.Row]:
        async with self.lock:
            return self.conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()

    async def all_users(self) -> list[sqlite3.Row]:
        async with self.lock:
            return self.conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()

    async def active_users_count(self, server_id: Optional[int] = None) -> int:
        async with self.lock:
            if server_id is None:
                row = self.conn.execute(
                    "SELECT COUNT(*) AS c FROM users WHERE is_active = 1 AND subscription_expires > ?",
                    (to_iso(now_utc()),),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT COUNT(*) AS c FROM users WHERE server_id = ? AND is_active = 1 AND subscription_expires > ?",
                    (server_id, to_iso(now_utc())),
                ).fetchone()
            return int(row["c"])

    async def assign_server(self, user_id: int, server_id: int) -> None:
        async with self.lock:
            self.conn.execute(
                "UPDATE users SET server_id = ?, updated_at = ? WHERE tg_id = ?",
                (server_id, to_iso(now_utc()), user_id),
            )
            self.conn.commit()

    async def create_pending(self, user_id: int, amount_rub: int, source: str) -> str:
        current = now_utc()
        payment_id = str(uuid.uuid4())
        async with self.lock:
            self.conn.execute(
                "UPDATE pending_payments SET status = 'expired', updated_at = ? WHERE user_id = ? AND status = 'waiting' AND source = ?",
                (to_iso(current), user_id, source),
            )
            self.conn.execute(
                """
                INSERT INTO pending_payments (id, user_id, amount_rub, source, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'waiting', ?, ?)
                """,
                (payment_id, user_id, amount_rub, source, to_iso(current), to_iso(current)),
            )
            self.conn.commit()
            return payment_id

    async def complete_pending(self, user_id: int, source: str) -> None:
        async with self.lock:
            self.conn.execute(
                """
                UPDATE pending_payments
                SET status = 'completed', updated_at = ?
                WHERE id = (
                    SELECT id FROM pending_payments
                    WHERE user_id = ? AND source = ? AND status = 'waiting'
                    ORDER BY created_at DESC LIMIT 1
                )
                """,
                (to_iso(now_utc()), user_id, source),
            )
            self.conn.commit()

    async def cancel_expired_pending(self, older_than_minutes: int = 60) -> int:
        threshold = now_utc() - timedelta(minutes=older_than_minutes)
        async with self.lock:
            cur = self.conn.execute(
                "UPDATE pending_payments SET status = 'expired', updated_at = ? WHERE status = 'waiting' AND created_at < ?",
                (to_iso(now_utc()), to_iso(threshold)),
            )
            self.conn.commit()
            return int(cur.rowcount)

    async def mark_event_processed(self, event_hash: str, source: str) -> bool:
        async with self.lock:
            exists = self.conn.execute("SELECT 1 FROM processed_events WHERE event_hash = ?", (event_hash,)).fetchone()
            if exists:
                return False
            self.conn.execute(
                "INSERT INTO processed_events (event_hash, source, created_at) VALUES (?, ?, ?)",
                (event_hash, source, to_iso(now_utc())),
            )
            self.conn.commit()
            return True

    async def calculate_period(self, user_id: int, days: int) -> tuple[datetime, datetime]:
        user = await self.get_user(user_id)
        existing = from_iso(user["subscription_expires"]) if user else None
        current = now_utc()
        start = existing if existing and existing > current else current
        return start, start + timedelta(days=days)

    async def activate_subscription(self, user_id: int, plan: str, source: str, payment_id: Optional[str], start: datetime, end: datetime) -> sqlite3.Row:
        async with self.lock:
            self.conn.execute(
                """
                UPDATE users
                SET subscription_expires = ?, is_active = 1, expiry_notified_at = NULL, updated_at = ?
                WHERE tg_id = ?
                """,
                (to_iso(end), to_iso(now_utc()), user_id),
            )
            self.conn.execute(
                """
                INSERT OR IGNORE INTO subscriptions (user_id, plan, start_date, end_date, source, payment_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, plan, to_iso(start), to_iso(end), source, payment_id, to_iso(now_utc())),
            )
            self.conn.commit()
            return self.conn.execute("SELECT * FROM users WHERE tg_id = ?", (user_id,)).fetchone()

    async def deactivate_user(self, user_id: int) -> None:
        async with self.lock:
            self.conn.execute("UPDATE users SET is_active = 0, updated_at = ? WHERE tg_id = ?", (to_iso(now_utc()), user_id))
            self.conn.commit()

    async def expired_active_users(self) -> list[sqlite3.Row]:
        async with self.lock:
            return self.conn.execute(
                "SELECT * FROM users WHERE is_active = 1 AND subscription_expires IS NOT NULL AND subscription_expires <= ?",
                (to_iso(now_utc()),),
            ).fetchall()

    async def users_expiring_soon(self, hours: int = 24) -> list[sqlite3.Row]:
        current = now_utc()
        until = current + timedelta(hours=hours)
        async with self.lock:
            return self.conn.execute(
                """
                SELECT * FROM users
                WHERE is_active = 1
                  AND subscription_expires > ?
                  AND subscription_expires <= ?
                  AND expiry_notified_at IS NULL
                """,
                (to_iso(current), to_iso(until)),
            ).fetchall()

    async def mark_expiry_notified(self, user_id: int) -> None:
        async with self.lock:
            self.conn.execute("UPDATE users SET expiry_notified_at = ? WHERE tg_id = ?", (to_iso(now_utc()), user_id))
            self.conn.commit()


DB = Database(config.db_file)


# ══════════════════════════════════════════════════════
#  3X-UI SERVER POOL AND VPN SERVICE
# ══════════════════════════════════════════════════════

@dataclass
class Connection:
    server: ServerConfig
    api: Any


class ServerPool:
    def __init__(self, cfg: Config, db: Database) -> None:
        self.cfg = cfg
        self.db = db
        self.connections: dict[int, Connection] = {}
        self.lock = asyncio.Lock()

    async def sync(self) -> None:
        if AsyncApi is None:
            raise RuntimeError("py3xui is not installed")
        if not (self.cfg.xui_username and self.cfg.xui_password):
            raise RuntimeError("XUI_USERNAME and XUI_PASSWORD are required")
        async with self.lock:
            for server in self.cfg.servers:
                if server.id in self.connections:
                    continue
                api = AsyncApi(
                    host=server.host,
                    username=self.cfg.xui_username,
                    password=self.cfg.xui_password,
                    token=self.cfg.xui_token,
                    logger=logging.getLogger(f"py3xui.{server.name}"),
                )
                try:
                    await api.login()
                    self.connections[server.id] = Connection(server=server, api=api)
                    logger.info("3X-UI server connected: %s (%s)", server.name, server.host)
                except Exception as exc:
                    logger.error("3X-UI server is unavailable: %s (%s): %s", server.name, server.host, exc)

    async def get_available_server(self) -> ServerConfig:
        await self.sync()
        online = [conn.server for conn in self.connections.values()]
        if not online:
            raise RuntimeError("No online 3X-UI servers are available")
        scored: list[tuple[int, ServerConfig]] = []
        for server in online:
            active = await self.db.active_users_count(server.id)
            if active < server.max_clients:
                scored.append((active, server))
        if not scored:
            scored = [(await self.db.active_users_count(server.id), server) for server in online]
            logger.warning("All configured servers are full; using least loaded server.")
        return sorted(scored, key=lambda item: item[0])[0][1]

    async def get_connection_for_user(self, user: sqlite3.Row) -> Connection:
        await self.sync()
        server_id = user["server_id"]
        if not server_id:
            server = await self.get_available_server()
            await self.db.assign_server(user["tg_id"], server.id)
            refreshed = await self.db.get_user(user["tg_id"])
            user = refreshed or user
            server_id = server.id
        conn = self.connections.get(int(server_id))
        if not conn:
            server = await self.get_available_server()
            await self.db.assign_server(user["tg_id"], server.id)
            conn = self.connections[server.id]
        return conn

    async def get_inbound_id(self, conn: Connection) -> int:
        if conn.server.inbound_id and not self.cfg.xui_dynamic_inbound:
            return conn.server.inbound_id
        try:
            inbounds = await conn.api.inbound.get_list()
            if not inbounds:
                raise RuntimeError("3X-UI has no inbounds")
            return int(inbounds[0].id)
        except Exception as exc:
            if conn.server.inbound_id:
                logger.warning("Dynamic inbound lookup failed; fallback to configured inbound_id=%s: %s", conn.server.inbound_id, exc)
                return conn.server.inbound_id
            raise


class VPNService:
    def __init__(self, cfg: Config, db: Database, pool: ServerPool) -> None:
        self.cfg = cfg
        self.db = db
        self.pool = pool

    async def ensure_client(self, user: sqlite3.Row, expires: datetime) -> sqlite3.Row:
        if Client is None:
            raise RuntimeError("py3xui Client class is unavailable")
        conn = await self.pool.get_connection_for_user(user)
        refreshed = await self.db.get_user(user["tg_id"])
        user = refreshed or user
        email = str(user["tg_id"])
        vpn_id = user["vpn_id"]
        expiry = ms_timestamp(expires)
        inbound_id = await self.pool.get_inbound_id(conn)
        client = await conn.api.client.get_by_email(email)
        if client:
            original_uuid = client.id
            client.enable = True
            client.id = vpn_id
            client.email = email
            client.expiry_time = expiry
            client.flow = conn.server.vless_flow
            client.limit_ip = self.cfg.max_devices
            client.sub_id = vpn_id
            client.total_gb = 0
            await conn.api.client.update(client_uuid=original_uuid, client=client)
            logger.info("Updated VPN client %s on %s", email, conn.server.name)
            return user
        new_client = Client(
            email=email,
            enable=True,
            id=vpn_id,
            expiry_time=expiry,
            flow=conn.server.vless_flow,
            limit_ip=self.cfg.max_devices,
            sub_id=vpn_id,
            total_gb=0,
        )
        await conn.api.client.add(inbound_id=inbound_id, clients=[new_client])
        logger.info("Created VPN client %s on %s", email, conn.server.name)
        return user

    async def disable_client(self, user: sqlite3.Row) -> bool:
        try:
            conn = await self.pool.get_connection_for_user(user)
            client = await conn.api.client.get_by_email(str(user["tg_id"]))
            if not client:
                return False
            client.enable = False
            await conn.api.client.update(client_uuid=client.id, client=client)
            logger.info("Disabled VPN client %s", user["tg_id"])
            return True
        except Exception as exc:
            logger.exception("Failed to disable VPN client %s: %s", user["tg_id"], exc)
            return False


POOL = ServerPool(config, DB)
VPN = VPNService(config, DB, POOL)


def server_for_user(user: sqlite3.Row) -> ServerConfig:
    server_id = user["server_id"] or config.servers[0].id
    return next((server for server in config.servers if server.id == server_id), config.servers[0])


def build_access_link(user: sqlite3.Row) -> str:
    server = server_for_user(user)
    if server.subscription_base:
        return f"{server.subscription_base.rstrip('/')}/{user['vpn_id']}"
    tag = quote("AstroVPN", safe="")
    return (
        f"vless://{user['vpn_id']}@{server.server_ip}:{server.vless_port}"
        f"?type=tcp&security=reality"
        f"&pbk={server.vless_public_key}"
        f"&sni={server.vless_sni}"
        f"&fp={server.vless_fingerprint}"
        f"&sid={server.vless_short_id}"
        f"&flow={server.vless_flow}"
        f"#{tag}"
    )


# ══════════════════════════════════════════════════════
#  UI
# ══════════════════════════════════════════════════════

def kb_main(has_sub: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_sub:
        rows.append([InlineKeyboardButton(text="Мой ключ", callback_data="mykey"), InlineKeyboardButton(text="Профиль", callback_data="profile")])
        rows.append([InlineKeyboardButton(text="Продлить", callback_data="buy")])
    else:
        rows.append([InlineKeyboardButton(text="Получить доступ", callback_data="buy")])
    rows.append([InlineKeyboardButton(text="Как подключить", callback_data="guide")])
    rows.append([InlineKeyboardButton(text="Поддержка", callback_data="support")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Главное меню", callback_data="menu")]])


def kb_buy() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if config.payment_stars_enabled:
        rows.append([InlineKeyboardButton(text=f"Telegram Stars — {config.stars_amount} Stars", callback_data="pay_stars")])
    if config.payment_tribute_enabled:
        rows.append([InlineKeyboardButton(text=f"Карта / СБП — {config.price_rub} ₽", callback_data="pay_tribute")])
    rows.append([InlineKeyboardButton(text="Главное меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_tribute() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к оплате", url=config.tribute_link)],
            [InlineKeyboardButton(text="Я оплатил", callback_data="tribute_check")],
            [InlineKeyboardButton(text="Главное меню", callback_data="menu")],
        ]
    )


def kb_guide() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="iOS", callback_data="guide_ios"), InlineKeyboardButton(text="Android", callback_data="guide_android")],
            [InlineKeyboardButton(text="Главное меню", callback_data="menu")],
        ]
    )


def is_sub_active(user: Optional[sqlite3.Row]) -> bool:
    if not user or not user["is_active"]:
        return False
    expires = from_iso(user["subscription_expires"])
    return bool(expires and expires > now_utc())


async def safe_edit(message: types.Message, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await message.edit_text(text=text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        await message.answer(text=text, reply_markup=reply_markup)


async def notify_admins(text: str, document: Optional[FSInputFile] = None) -> None:
    for admin_id in config.admin_ids:
        try:
            if document:
                await bot.send_document(admin_id, document=document, caption=text)
            else:
                await bot.send_message(admin_id, text)
        except Exception as exc:
            logger.warning("Failed to notify admin %s: %s", admin_id, exc)


def format_profile(user: sqlite3.Row) -> str:
    active = is_sub_active(user)
    expires = from_iso(user["subscription_expires"])
    username = f"@{user['username']}" if user["username"] else "—"
    status = f"активна до <b>{fmt_dt(expires)}</b>" if active else "нет активной подписки"
    return (
        f"<b>AstroVPN</b>\n\n"
        f"Пользователь: <b>{user['name']}</b>\n"
        f"Username: {username}\n"
        f"ID: <code>{user['tg_id']}</code>\n"
        f"Статус: {status}\n\n"
        f"Тариф: <b>{config.subscription_days} дней</b>, {config.max_devices} устройство(а)\n"
        f"Цена: <b>{config.stars_amount} Stars</b> или <b>{config.price_rub} ₽</b>"
    )


async def show_menu(user_id: int, message: Optional[types.Message] = None, chat_id: Optional[int] = None) -> None:
    user = await DB.get_user(user_id)
    if not user:
        return
    text = format_profile(user)
    markup = kb_main(is_sub_active(user))
    if message:
        await safe_edit(message, text, markup)
    elif chat_id:
        await bot.send_message(chat_id, text, reply_markup=markup)


async def send_receipt(chat_id: int, user: sqlite3.Row, start: datetime, end: datetime, source: str) -> None:
    method = {"stars": "Telegram Stars", "tribute": "Tribute", "admin": "выдано администратором"}.get(source, source)
    link = build_access_link(user)
    text = (
        "<b>Доступ AstroVPN активирован</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Способ: <b>{method}</b>\n"
        f"Начало: <b>{fmt_dt(start)}</b>\n"
        f"Окончание: <b>{fmt_dt(end)}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Ваш ключ/ссылка подписки:\n"
        f"<code>{link}</code>\n\n"
        "Скопируйте ссылку и импортируйте её в приложении из раздела «Как подключить»."
    )
    await bot.send_message(chat_id, text, reply_markup=kb_back())


# ══════════════════════════════════════════════════════
#  PAYMENT AND SUBSCRIPTION BUSINESS FLOW
# ══════════════════════════════════════════════════════

async def issue_subscription(tg_id: int, name: str, username: Optional[str], source: str, payment_id: Optional[str] = None, days: Optional[int] = None) -> tuple[bool, Optional[str]]:
    days = days or config.subscription_days
    user = await DB.ensure_user(tg_id, name, username)
    if payment_id:
        event_key = hashlib.sha256(f"{source}:{payment_id}".encode()).hexdigest()
        if not await DB.mark_event_processed(event_key, source):
            logger.info("Duplicate payment ignored: %s %s", source, payment_id)
            return True, None
    start, end = await DB.calculate_period(tg_id, days)
    try:
        user = await VPN.ensure_client(user, end)
        user = await DB.activate_subscription(tg_id, f"vpn_{days}_days_{config.max_devices}_devices", source, payment_id, start, end)
        await DB.complete_pending(tg_id, source)
        await send_receipt(tg_id, user, start, end, source)
        await notify_admins(
            f"<b>Выдан доступ AstroVPN</b>\n"
            f"Пользователь: {user['name']} (<code>{tg_id}</code>)\n"
            f"Источник: <b>{source}</b>\n"
            f"До: <b>{fmt_dt(end)}</b>"
        )
        return True, None
    except Exception as exc:
        logger.exception("Failed to issue subscription for %s", tg_id)
        await notify_admins(
            f"<b>Ошибка выдачи AstroVPN</b>\n"
            f"Пользователь: <code>{tg_id}</code>\n"
            f"Источник: <b>{source}</b>\n"
            f"Ошибка: <code>{str(exc)}</code>"
        )
        return False, str(exc)


# ══════════════════════════════════════════════════════
#  USER HANDLERS
# ══════════════════════════════════════════════════════

@dp.message(CommandStart())
async def start_handler(message: types.Message) -> None:
    await DB.ensure_user(message.from_user.id, safe_name(message.from_user), message.from_user.username)
    await show_menu(message.from_user.id, chat_id=message.chat.id)


@dp.callback_query(F.data == "menu")
async def menu_callback(call: types.CallbackQuery) -> None:
    await DB.ensure_user(call.from_user.id, safe_name(call.from_user), call.from_user.username)
    await show_menu(call.from_user.id, message=call.message)
    await call.answer()


@dp.callback_query(F.data == "profile")
async def profile_callback(call: types.CallbackQuery) -> None:
    await show_menu(call.from_user.id, message=call.message)
    await call.answer()


@dp.callback_query(F.data == "buy")
async def buy_callback(call: types.CallbackQuery) -> None:
    try:
        server = await POOL.get_available_server()
        active = await DB.active_users_count(server.id)
        free_slots = max(server.max_clients - active, 0)
    except Exception:
        free_slots = 0
    if free_slots <= 0:
        await safe_edit(call.message, f"Сейчас нет свободных мест. Напишите в поддержку: {config.support_username}", kb_back())
        await call.answer()
        return
    text = (
        f"<b>AstroVPN на {config.subscription_days} дней</b>\n\n"
        f"Устройства: <b>{config.max_devices}</b>\n"
        f"Стоимость: <b>{config.stars_amount} Stars</b> или <b>{config.price_rub} ₽</b>\n"
        f"Свободных мест: <b>{free_slots}</b>\n\n"
        "Выберите способ оплаты:"
    )
    await safe_edit(call.message, text, kb_buy())
    await call.answer()


@dp.callback_query(F.data == "pay_stars")
async def pay_stars_callback(call: types.CallbackQuery) -> None:
    if not config.payment_stars_enabled:
        await call.answer("Оплата Stars сейчас недоступна", show_alert=True)
        return
    await DB.ensure_user(call.from_user.id, safe_name(call.from_user), call.from_user.username)
    await bot.send_invoice(
        chat_id=call.message.chat.id,
        title=f"AstroVPN на {config.subscription_days} дней",
        description=f"VPN-доступ на {config.subscription_days} дней, {config.max_devices} устройство(а)",
        payload=f"stars:{call.from_user.id}:{uuid.uuid4()}",
        currency="XTR",
        prices=[LabeledPrice(label="AstroVPN", amount=config.stars_amount)],
        provider_token="",
    )
    await call.answer()


@dp.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery) -> None:
    await bot.answer_pre_checkout_query(query.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message) -> None:
    payment = message.successful_payment
    if payment.currency != "XTR" or payment.total_amount < config.stars_amount:
        await message.answer(f"Платёж получен, но сумма не совпадает с тарифом. Напишите {config.support_username}.")
        await notify_admins(f"Некорректный Stars-платёж от <code>{message.from_user.id}</code>: {payment.total_amount} {payment.currency}")
        return
    payment_id = payment.telegram_payment_charge_id or f"stars:{message.from_user.id}:{uuid.uuid4()}"
    ok, _error = await issue_subscription(message.from_user.id, safe_name(message.from_user), message.from_user.username, "stars", payment_id)
    if not ok:
        await message.answer(f"Оплата прошла, но ключ не создан автоматически. Напишите {config.support_username}.")


@dp.callback_query(F.data == "pay_tribute")
async def pay_tribute_callback(call: types.CallbackQuery) -> None:
    if not config.payment_tribute_enabled:
        await call.answer("Оплата картой/СБП сейчас недоступна", show_alert=True)
        return
    await DB.ensure_user(call.from_user.id, safe_name(call.from_user), call.from_user.username)
    await DB.create_pending(call.from_user.id, config.price_rub, "tribute")
    text = (
        "<b>Оплата через Tribute</b>\n\n"
        f"Сумма: <b>{config.price_rub} ₽</b>.\n"
        "После оплаты Tribute пришлёт webhook, и ключ будет создан автоматически.\n\n"
        "Важно: если в Tribute есть поле комментария, укажите ваш Telegram ID:\n"
        f"<code>{call.from_user.id}</code>"
    )
    await safe_edit(call.message, text, kb_tribute())
    await call.answer()


@dp.callback_query(F.data == "tribute_check")
async def tribute_check_callback(call: types.CallbackQuery) -> None:
    user = await DB.get_user(call.from_user.id)
    if is_sub_active(user):
        await safe_edit(call.message, f"Оплата подтверждена. Подписка активна до <b>{fmt_dt(from_iso(user['subscription_expires']))}</b>.", kb_main(True))
    else:
        await safe_edit(call.message, f"Оплата пока не подтверждена. Подождите 1–2 минуты или напишите {config.support_username}.", kb_tribute())
    await call.answer()


@dp.callback_query(F.data == "mykey")
async def mykey_callback(call: types.CallbackQuery) -> None:
    user = await DB.get_user(call.from_user.id)
    if is_sub_active(user):
        await safe_edit(
            call.message,
            f"Ваш активный ключ/ссылка подписки:\n\n<code>{build_access_link(user)}</code>\n\nДействует до: <b>{fmt_dt(from_iso(user['subscription_expires']))}</b>",
            kb_back(),
        )
    else:
        await safe_edit(call.message, "У вас нет активной подписки. Нажмите «Получить доступ».", kb_main(False))
    await call.answer()


@dp.callback_query(F.data == "guide")
async def guide_callback(call: types.CallbackQuery) -> None:
    await safe_edit(call.message, "Выберите вашу платформу:", kb_guide())
    await call.answer()


@dp.callback_query(F.data == "guide_ios")
async def guide_ios_callback(call: types.CallbackQuery) -> None:
    await safe_edit(
        call.message,
        "<b>iOS</b>\n\n"
        "1. Установите FoXray, Streisand или V2Box из App Store.\n"
        "2. Нажмите «Мой ключ» в боте и скопируйте ссылку.\n"
        "3. В приложении выберите импорт из буфера обмена или по URL.\n"
        "4. Включите подключение.",
        kb_back(),
    )
    await call.answer()


@dp.callback_query(F.data == "guide_android")
async def guide_android_callback(call: types.CallbackQuery) -> None:
    await safe_edit(
        call.message,
        "<b>Android</b>\n\n"
        "1. Установите v2rayNG, Hiddify или NekoBox.\n"
        "2. Скопируйте ссылку из раздела «Мой ключ».\n"
        "3. Импортируйте ссылку из буфера обмена или по URL.\n"
        "4. Запустите профиль.",
        kb_back(),
    )
    await call.answer()


@dp.callback_query(F.data == "support")
async def support_callback(call: types.CallbackQuery) -> None:
    await safe_edit(call.message, f"Поддержка: {config.support_username}", kb_back())
    await call.answer()


# ══════════════════════════════════════════════════════
#  ADMIN HANDLERS
# ══════════════════════════════════════════════════════

@dp.message(Command("addkey"))
async def admin_addkey(message: types.Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "Ручной склад ключей больше не нужен: бот автоматически создаёт и продлевает клиентов в 3X-UI.\n\n"
        "Используйте:\n"
        "<code>/give &lt;user_id&gt; [days]</code> — выдать доступ;\n"
        "<code>/revoke &lt;user_id&gt;</code> — отозвать доступ;\n"
        "<code>/stats</code> — посмотреть загрузку серверов."
    )


@dp.message(Command("give"))
async def admin_give(message: types.Message) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /give <user_id> [days]")
        return
    try:
        target_id = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else config.subscription_days
    except ValueError:
        await message.answer("user_id и days должны быть числами")
        return
    user = await DB.get_user(target_id)
    name = user["name"] if user else f"user_{target_id}"
    username = user["username"] if user else None
    ok, error = await issue_subscription(target_id, name, username, "admin", f"admin:{message.from_user.id}:{uuid.uuid4()}", days)
    await message.answer(f"Готово: доступ выдан <code>{target_id}</code> на {days} дней" if ok else f"Ошибка: <code>{error}</code>")


@dp.message(Command("revoke"))
async def admin_revoke(message: types.Message) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: /revoke <user_id>")
        return
    target_id = int(parts[1])
    user = await DB.get_user(target_id)
    if not user:
        await message.answer("Пользователь не найден")
        return
    disabled = await VPN.disable_client(user)
    await DB.deactivate_user(target_id)
    await message.answer(f"Подписка отозвана у <code>{target_id}</code>. 3X-UI: {'disabled' if disabled else 'not found/error'}")
    try:
        await bot.send_message(target_id, "Ваша подписка AstroVPN была отозвана администратором.")
    except Exception:
        pass


@dp.message(Command("users"))
async def admin_users(message: types.Message) -> None:
    if not is_admin(message.from_user.id):
        return
    users = await DB.all_users()
    lines = ["<b>Пользователи</b>"]
    for user in users[:50]:
        expires = from_iso(user["subscription_expires"])
        active = "active" if is_sub_active(user) else "inactive"
        uname = f"@{user['username']}" if user["username"] else "—"
        lines.append(f"<code>{user['tg_id']}</code> | {uname} | {active} | {fmt_dt(expires)}")
    if len(users) > 50:
        lines.append(f"…и ещё {len(users) - 50}")
    await message.answer("\n".join(lines))


@dp.message(Command("stats"))
@dp.message(Command("keys"))
async def admin_stats(message: types.Message) -> None:
    if not is_admin(message.from_user.id):
        return
    total = len(await DB.all_users())
    active_total = await DB.active_users_count()
    server_lines = []
    for server in config.servers:
        active = await DB.active_users_count(server.id)
        server_lines.append(f"{server.name}: <b>{active}</b>/{server.max_clients}")
    await message.answer(
        "<b>Статистика AstroVPN</b>\n\n"
        f"Всего пользователей: <b>{total}</b>\n"
        f"Активных подписок: <b>{active_total}</b>\n"
        f"Серверы:\n" + "\n".join(server_lines)
    )


@dp.message(Command("backup"))
async def admin_backup(message: types.Message) -> None:
    if not is_admin(message.from_user.id):
        return
    db_path = Path(config.db_file)
    if not db_path.exists():
        await message.answer("Файл базы данных не найден")
        return
    backup_path = Path(f"backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{db_path.name}")
    shutil.copy2(db_path, backup_path)
    await message.answer_document(FSInputFile(str(backup_path)), caption="Резервная копия базы данных")
    try:
        backup_path.unlink()
    except OSError:
        pass


@dp.message()
async def any_message(message: types.Message) -> None:
    await DB.ensure_user(message.from_user.id, safe_name(message.from_user), message.from_user.username)
    await show_menu(message.from_user.id, chat_id=message.chat.id)


# ══════════════════════════════════════════════════════
#  TRIBUTE WEBHOOK AND HEALTHCHECK
# ══════════════════════════════════════════════════════

async def tribute_webhook(request: web.Request) -> web.Response:
    if config.tribute_secret:
        provided = request.headers.get("X-Tribute-Secret") or request.query.get("secret")
        if provided != config.tribute_secret:
            logger.warning("Tribute webhook rejected: bad secret")
            return web.Response(status=403, text="Forbidden")
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="Bad JSON")
    logger.info("Tribute webhook received: %s", data)
    if data.get("test_event") == "test_event":
        return web.Response(text="OK")
    if data.get("name") != config.tribute_expected_event:
        return web.Response(text="OK")
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    tg_raw = payload.get("telegram_user_id") or payload.get("telegram_id") or payload.get("tg_id") or payload.get("comment")
    amount = int(payload.get("amount") or 0)
    currency = str(payload.get("currency") or "RUB").upper()
    payment_id = str(payload.get("id") or payload.get("payment_id") or hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest())
    try:
        tg_id = int(str(tg_raw).strip())
    except Exception:
        await notify_admins(
            f"<b>Tribute: оплата без Telegram ID</b>\n"
            f"Сумма: {amount / 100:.0f} {currency}\n"
            f"Payload: <code>{str(payload)[:3000]}</code>"
        )
        return web.Response(text="OK")
    min_amount = config.price_rub * 100 - 100
    if currency == "RUB" and amount < min_amount:
        try:
            await bot.send_message(tg_id, f"Получена оплата {amount / 100:.0f} ₽, но требуется {config.price_rub} ₽. Напишите {config.support_username}.")
        except Exception:
            pass
        return web.Response(text="OK")
    user = await DB.get_user(tg_id)
    name = user["name"] if user else f"user_{tg_id}"
    username = user["username"] if user else None
    try:
        chat = await bot.get_chat(tg_id)
        name = chat.full_name or name
        username = getattr(chat, "username", username)
    except Exception:
        pass
    try:
        await bot.send_message(tg_id, "Оплата получена. Создаю VPN-доступ...")
    except Exception:
        pass
    ok, error = await issue_subscription(tg_id, name, username, "tribute", payment_id)
    if not ok:
        try:
            await bot.send_message(tg_id, f"Оплата прошла, но ключ не создан автоматически. Напишите {config.support_username}.")
        except Exception:
            pass
    return web.Response(status=200, text="OK")


async def healthcheck(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "time": to_iso(now_utc())})


# ══════════════════════════════════════════════════════
#  BACKGROUND TASKS AND STARTUP
# ══════════════════════════════════════════════════════

async def subscription_maintenance() -> None:
    while True:
        try:
            await DB.cancel_expired_pending(older_than_minutes=90)
            for user in await DB.users_expiring_soon(hours=24):
                try:
                    await bot.send_message(
                        user["tg_id"],
                        f"Ваша подписка AstroVPN истекает <b>{fmt_dt(from_iso(user['subscription_expires']))}</b>. Нажмите «Продлить» в меню /start.",
                    )
                    await DB.mark_expiry_notified(user["tg_id"])
                except Exception as exc:
                    logger.warning("Failed to send expiry reminder to %s: %s", user["tg_id"], exc)
            for user in await DB.expired_active_users():
                await VPN.disable_client(user)
                await DB.deactivate_user(user["tg_id"])
                try:
                    await bot.send_message(user["tg_id"], f"Подписка AstroVPN истекла. Для продления откройте /start.")
                except Exception:
                    pass
        except Exception as exc:
            logger.exception("Subscription maintenance failed: %s", exc)
        await asyncio.sleep(600)


async def start_web_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_post("/tribute/webhook", tribute_webhook)
    app.router.add_get("/health", healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.port)
    await site.start()
    logger.info("HTTP server started on port %s", config.port)
    return runner


async def main() -> None:
    await DB.init()
    try:
        await POOL.sync()
    except Exception as exc:
        logger.warning("3X-UI pool was not fully initialized at startup: %s", exc)
    runner = await start_web_server()
    maintenance_task = asyncio.create_task(subscription_maintenance())
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        maintenance_task.cancel()
        try:
            await maintenance_task
        except asyncio.CancelledError:
            pass
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
