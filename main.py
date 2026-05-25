import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from dotenv import load_dotenv
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, PreCheckoutQuery

try:
    from py3xui import AsyncApi, Client
except ImportError:  # pragma: no cover - dependency is installed in production from requirements.txt
    AsyncApi = None
    Client = None


load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("astrovpn")

UTC = timezone.utc


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_id: int
    support_username: str
    tribute_link: str
    port: int
    db_file: str
    stars_amount: int
    price_rub: int
    subscription_days: int
    server_ip: str
    vless_port: int
    vless_sni: str
    vless_fingerprint: str
    vless_flow: str
    vless_public_key: str
    vless_short_id: str
    xui_host: str
    xui_username: str
    xui_password: str
    xui_token: Optional[str]
    xui_inbound_id: int
    xui_ip_limit: int
    max_clients: int

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN is required")

        admin_id_raw = os.getenv("ADMIN_ID", "0").strip()
        if not admin_id_raw.isdigit() or int(admin_id_raw) <= 0:
            raise RuntimeError("ADMIN_ID must be a positive integer")

        server_ip = os.getenv("SERVER_IP", "89.127.207.207").strip()
        xui_host = os.getenv("XUI_HOST", f"https://{server_ip}:2053").strip().rstrip("/")

        return cls(
            bot_token=token,
            admin_id=int(admin_id_raw),
            support_username=os.getenv("SUPPORT_USERNAME", "@support").strip(),
            tribute_link=os.getenv("TRIBUTE_LINK", "https://t.me/tribute/app?startapp=dI5p").strip(),
            port=int(os.getenv("PORT", "8080")),
            db_file=os.getenv("DB_FILE", "astrovpn.sqlite3"),
            stars_amount=int(os.getenv("STARS_AMOUNT", "199")),
            price_rub=int(os.getenv("PRICE_RUB", "199")),
            subscription_days=int(os.getenv("SUBSCRIPTION_DAYS", "30")),
            server_ip=server_ip,
            vless_port=int(os.getenv("VLESS_PORT", "443")),
            vless_sni=os.getenv("VLESS_SNI", "www.apple.com").strip(),
            vless_fingerprint=os.getenv("VLESS_FINGERPRINT", "chrome").strip(),
            vless_flow=os.getenv("VLESS_FLOW", "xtls-rprx-vision").strip(),
            vless_public_key=os.getenv("VLESS_PUBLIC_KEY", "PUBLIC_KEY").strip(),
            vless_short_id=os.getenv("VLESS_SHORT_ID", "SHORT_ID").strip(),
            xui_host=xui_host,
            xui_username=os.getenv("XUI_USERNAME", "").strip(),
            xui_password=os.getenv("XUI_PASSWORD", "").strip(),
            xui_token=os.getenv("XUI_TOKEN") or None,
            xui_inbound_id=int(os.getenv("XUI_INBOUND_ID", "1")),
            xui_ip_limit=int(os.getenv("XUI_IP_LIMIT", "1")),
            max_clients=int(os.getenv("MAX_CLIENTS", "100")),
        )


config = Config.from_env()
bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()


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
    return user.full_name or user.first_name or "Пользователь"


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()

    async def init(self) -> None:
        async with self.lock:
            self.conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    tg_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    vpn_id TEXT NOT NULL UNIQUE,
                    server_id INTEGER NOT NULL DEFAULT 1,
                    subscription_expires TEXT,
                    is_active INTEGER NOT NULL DEFAULT 0,
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

                CREATE TABLE IF NOT EXISTS manual_keys (
                    id TEXT PRIMARY KEY,
                    key_value TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'free',
                    user_id INTEGER,
                    created_at TEXT NOT NULL,
                    assigned_at TEXT
                );
                """
            )
            self.conn.commit()

    async def ensure_user(self, tg_id: int, name: str) -> sqlite3.Row:
        current = now_utc()
        async with self.lock:
            row = self.conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE users SET name = ?, updated_at = ? WHERE tg_id = ?",
                    (name, to_iso(current), tg_id),
                )
                self.conn.commit()
                return self.conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()

            self.conn.execute(
                """
                INSERT INTO users (tg_id, name, vpn_id, server_id, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (tg_id, name, str(uuid.uuid4()), to_iso(current), to_iso(current)),
            )
            self.conn.commit()
            return self.conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()

    async def get_user(self, tg_id: int) -> Optional[sqlite3.Row]:
        async with self.lock:
            return self.conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()

    async def all_users(self) -> list[sqlite3.Row]:
        async with self.lock:
            return self.conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()

    async def active_users_count(self) -> int:
        async with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE is_active = 1 AND subscription_expires > ?",
                (to_iso(now_utc()),),
            ).fetchone()
            return int(row["c"])

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
        current = now_utc()
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
                (to_iso(current), user_id, source),
            )
            self.conn.commit()

    async def mark_event_processed(self, event_hash: str, source: str) -> bool:
        async with self.lock:
            exists = self.conn.execute(
                "SELECT 1 FROM processed_events WHERE event_hash = ?",
                (event_hash,),
            ).fetchone()
            if exists:
                return False
            self.conn.execute(
                "INSERT INTO processed_events (event_hash, source, created_at) VALUES (?, ?, ?)",
                (event_hash, source, to_iso(now_utc())),
            )
            self.conn.commit()
            return True

    async def activate_subscription(
        self,
        user_id: int,
        plan: str,
        source: str,
        payment_id: Optional[str],
        days: int,
    ) -> tuple[sqlite3.Row, datetime, datetime]:
        current = now_utc()
        async with self.lock:
            user = self.conn.execute("SELECT * FROM users WHERE tg_id = ?", (user_id,)).fetchone()
            if not user:
                raise RuntimeError(f"User {user_id} not found")

            existing_expires = from_iso(user["subscription_expires"])
            start = existing_expires if existing_expires and existing_expires > current else current
            end = start + timedelta(days=days)

            self.conn.execute(
                "UPDATE users SET subscription_expires = ?, is_active = 1, updated_at = ? WHERE tg_id = ?",
                (to_iso(end), to_iso(current), user_id),
            )
            self.conn.execute(
                """
                INSERT OR IGNORE INTO subscriptions (user_id, plan, start_date, end_date, source, payment_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, plan, to_iso(start), to_iso(end), source, payment_id, to_iso(current)),
            )
            self.conn.commit()
            user = self.conn.execute("SELECT * FROM users WHERE tg_id = ?", (user_id,)).fetchone()
            return user, start, end

    async def deactivate_user(self, user_id: int) -> None:
        async with self.lock:
            self.conn.execute(
                "UPDATE users SET is_active = 0, updated_at = ? WHERE tg_id = ?",
                (to_iso(now_utc()), user_id),
            )
            self.conn.commit()

    async def expired_active_users(self) -> list[sqlite3.Row]:
        async with self.lock:
            return self.conn.execute(
                "SELECT * FROM users WHERE is_active = 1 AND subscription_expires IS NOT NULL AND subscription_expires <= ?",
                (to_iso(now_utc()),),
            ).fetchall()

    async def add_manual_key(self, key_value: str) -> bool:
        async with self.lock:
            try:
                self.conn.execute(
                    "INSERT INTO manual_keys (id, key_value, status, created_at) VALUES (?, ?, 'free', ?)",
                    (str(uuid.uuid4()), key_value, to_iso(now_utc())),
                )
                self.conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    async def key_stats(self) -> tuple[int, int, int]:
        active = await self.active_users_count()
        free_slots = max(config.max_clients - active, 0)
        async with self.lock:
            manual_free = self.conn.execute(
                "SELECT COUNT(*) AS c FROM manual_keys WHERE status = 'free'"
            ).fetchone()["c"]
        return active, free_slots, int(manual_free)


class XUIService:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.api: Optional[Any] = None
        self.logged_in = False

    async def login(self) -> None:
        if AsyncApi is None:
            raise RuntimeError("py3xui is not installed")
        if self.logged_in and self.api:
            return
        if not (self.cfg.xui_username and self.cfg.xui_password):
            raise RuntimeError("XUI_USERNAME and XUI_PASSWORD are required for 3X-UI integration")
        self.api = AsyncApi(
            host=self.cfg.xui_host,
            username=self.cfg.xui_username,
            password=self.cfg.xui_password,
            token=self.cfg.xui_token,
            logger=logging.getLogger("py3xui"),
        )
        await self.api.login()
        self.logged_in = True
        logger.info("Connected to 3X-UI: %s", self.cfg.xui_host)

    async def ensure_client(self, user: sqlite3.Row, expires: datetime) -> None:
        await self.login()
        assert self.api is not None
        if Client is None:
            raise RuntimeError("py3xui Client class is unavailable")

        tg_id = str(user["tg_id"])
        vpn_id = user["vpn_id"]
        expiry = ms_timestamp(expires)

        existing = await self.api.client.get_by_email(tg_id)
        if existing:
            existing.id = vpn_id
            existing.email = tg_id
            existing.enable = True
            existing.expiry_time = expiry
            existing.flow = self.cfg.vless_flow
            existing.limit_ip = self.cfg.xui_ip_limit
            existing.sub_id = vpn_id
            existing.total_gb = 0
            await self.api.client.update(client_uuid=vpn_id, client=existing)
            logger.info("Updated 3X-UI client for %s", tg_id)
            return

        new_client = Client(
            email=tg_id,
            enable=True,
            id=vpn_id,
            expiry_time=expiry,
            flow=self.cfg.vless_flow,
            limit_ip=self.cfg.xui_ip_limit,
            sub_id=vpn_id,
            total_gb=0,
        )
        await self.api.client.add(inbound_id=self.cfg.xui_inbound_id, clients=[new_client])
        logger.info("Created 3X-UI client for %s", tg_id)

    async def disable_client(self, user: sqlite3.Row) -> bool:
        try:
            await self.login()
            assert self.api is not None
            tg_id = str(user["tg_id"])
            client = await self.api.client.get_by_email(tg_id)
            if not client:
                logger.warning("3X-UI client for %s was not found during revoke", tg_id)
                return False
            client.enable = False
            await self.api.client.update(client_uuid=client.id, client=client)
            logger.info("Disabled 3X-UI client for %s", tg_id)
            return True
        except Exception as exc:
            logger.exception("Failed to disable 3X-UI client for %s: %s", user["tg_id"], exc)
            return False


DB = Database(config.db_file)
XUI = XUIService(config)


def build_vless_link(vpn_id: str) -> str:
    return (
        f"vless://{vpn_id}@{config.server_ip}:{config.vless_port}"
        f"?type=tcp&security=reality"
        f"&pbk={config.vless_public_key}"
        f"&sni={config.vless_sni}"
        f"&fp={config.vless_fingerprint}"
        f"&sid={config.vless_short_id}"
        f"&flow={config.vless_flow}"
        "#AstroVPN"
    )


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Получить доступ", callback_data="buy")],
            [InlineKeyboardButton(text="Мой ключ", callback_data="mykey")],
            [InlineKeyboardButton(text="Гайд по подключению", callback_data="guide")],
            [InlineKeyboardButton(text="Поддержка", callback_data="support")],
        ]
    )


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Главное меню", callback_data="menu")]])


def buy_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Telegram Stars ({config.stars_amount} Stars)", callback_data="pay_stars")],
            [InlineKeyboardButton(text=f"Tribute / карта ({config.price_rub} ₽)", callback_data="pay_tribute")],
            [InlineKeyboardButton(text="Главное меню", callback_data="menu")],
        ]
    )


def tribute_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к оплате Tribute", url=config.tribute_link)],
            [InlineKeyboardButton(text="Я оплатил", callback_data="tribute_check")],
            [InlineKeyboardButton(text="Главное меню", callback_data="menu")],
        ]
    )


def guide_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="iOS", callback_data="guide_ios")],
            [InlineKeyboardButton(text="Android", callback_data="guide_android")],
            [InlineKeyboardButton(text="Главное меню", callback_data="menu")],
        ]
    )


async def safe_edit(message: types.Message, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await message.edit_text(text=text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        await message.answer(text=text, reply_markup=reply_markup)


async def notify_admin(text: str) -> None:
    try:
        await bot.send_message(config.admin_id, text)
    except Exception as exc:  # pragma: no cover - notification failures must not break business flow
        logger.warning("Failed to notify admin: %s", exc)


async def show_profile(chat_id: int, user_id: int, message: Optional[types.Message] = None) -> None:
    user = await DB.get_user(user_id)
    if not user:
        return
    active_count, free_slots, _ = await DB.key_stats()
    expires = from_iso(user["subscription_expires"])
    is_active = bool(user["is_active"]) and bool(expires and expires > now_utc())
    status = f"Активна до {fmt_dt(expires)}" if is_active else "Подписка отсутствует"
    text = (
        f"Привет, <b>{user['name']}</b>!\n\n"
        f"ID: <code>{user['tg_id']}</code>\n"
        f"Стоимость: <b>{config.price_rub} ₽</b> или <b>{config.stars_amount} Stars</b> за {config.subscription_days} дней\n"
        f"Свободных мест: <b>{free_slots}</b>\n\n"
        f"Статус: <b>{status}</b>"
    )
    if message:
        await safe_edit(message, text, main_menu())
    else:
        await bot.send_message(chat_id, text, reply_markup=main_menu())


async def send_receipt(chat_id: int, user: sqlite3.Row, expires: datetime, source: str) -> None:
    method = "Telegram Stars" if source == "stars" else "Tribute" if source == "tribute" else "Выдан администратором"
    link = build_vless_link(user["vpn_id"])
    text = (
        "<b>ЧЕК ОБ ОПЛАТЕ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Товар: VPN доступ на {config.subscription_days} дней\n"
        f"Способ: {method}\n"
        f"Дата: {fmt_dt(now_utc())}\n"
        f"Истекает: {fmt_dt(expires)}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Ваш ключ:\n"
        f"<code>{link}</code>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Скопируйте ключ и вставьте его в приложение из раздела «Гайд»."
    )
    await bot.send_message(chat_id, text, reply_markup=back_to_menu())


async def issue_access(user_id: int, name: str, source: str, payment_id: Optional[str] = None) -> tuple[bool, Optional[str]]:
    await DB.ensure_user(user_id, name)
    try:
        user, _start, expires = await DB.activate_subscription(
            user_id=user_id,
            plan=f"vpn_{config.subscription_days}_days",
            source=source,
            payment_id=payment_id,
            days=config.subscription_days,
        )
        await XUI.ensure_client(user, expires)
        await send_receipt(user_id, user, expires, source)
        await notify_admin(
            f"Новая выдача VPN\n\n"
            f"Пользователь: <b>{user['name']}</b>\n"
            f"ID: <code>{user_id}</code>\n"
            f"Источник: <b>{source}</b>\n"
            f"Действует до: <b>{fmt_dt(expires)}</b>"
        )
        return True, None
    except Exception as exc:
        logger.exception("Failed to issue access for %s", user_id)
        await notify_admin(
            f"Ошибка выдачи VPN\n\n"
            f"ID: <code>{user_id}</code>\n"
            f"Источник: <b>{source}</b>\n"
            f"Ошибка: <code>{str(exc)}</code>"
        )
        return False, str(exc)


@dp.message(CommandStart())
async def start_handler(message: types.Message) -> None:
    await DB.ensure_user(message.from_user.id, safe_name(message.from_user))
    await show_profile(message.chat.id, message.from_user.id)


@dp.callback_query(F.data == "menu")
async def menu_callback(call: types.CallbackQuery) -> None:
    await DB.ensure_user(call.from_user.id, safe_name(call.from_user))
    await show_profile(call.message.chat.id, call.from_user.id, call.message)
    await call.answer()


@dp.callback_query(F.data == "buy")
async def buy_callback(call: types.CallbackQuery) -> None:
    active_count, free_slots, _ = await DB.key_stats()
    if free_slots <= 0:
        await safe_edit(
            call.message,
            f"Свободных мест сейчас нет. Напишите в поддержку: {config.support_username}",
            back_to_menu(),
        )
        await call.answer()
        return
    text = (
        f"VPN доступ на <b>{config.subscription_days} дней</b>.\n\n"
        f"Стоимость: <b>{config.price_rub} ₽</b> или <b>{config.stars_amount} Stars</b>.\n"
        f"Свободных мест: <b>{free_slots}</b>.\n\n"
        "Выберите способ оплаты:"
    )
    await safe_edit(call.message, text, buy_menu())
    await call.answer()


@dp.callback_query(F.data == "pay_stars")
async def pay_stars_callback(call: types.CallbackQuery) -> None:
    await DB.ensure_user(call.from_user.id, safe_name(call.from_user))
    active_count, free_slots, _ = await DB.key_stats()
    if free_slots <= 0:
        await call.answer("Свободных мест нет. Напишите в поддержку.", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=call.message.chat.id,
        title="VPN доступ на 30 дней",
        description=f"AstroVPN на {config.subscription_days} дней",
        payload=f"stars:{call.from_user.id}:{uuid.uuid4()}",
        currency="XTR",
        prices=[LabeledPrice(label=f"VPN {config.subscription_days} дней", amount=config.stars_amount)],
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
        await message.answer("Платёж получен, но сумма не соответствует тарифу. Напишите в поддержку.")
        await notify_admin(f"Некорректный Stars платёж от <code>{message.from_user.id}</code>: {payment.total_amount} {payment.currency}")
        return

    payment_id = getattr(payment, "telegram_payment_charge_id", None) or f"stars:{message.from_user.id}:{uuid.uuid4()}"
    ok, error = await issue_access(message.from_user.id, safe_name(message.from_user), "stars", payment_id)
    if not ok:
        await message.answer(
            f"Оплата прошла, но ключ не удалось создать автоматически. Напишите в поддержку: {config.support_username}"
        )


@dp.callback_query(F.data == "pay_tribute")
async def pay_tribute_callback(call: types.CallbackQuery) -> None:
    await DB.ensure_user(call.from_user.id, safe_name(call.from_user))
    await DB.create_pending(call.from_user.id, config.price_rub, "tribute")
    text = (
        "Оплата через <b>Tribute</b>.\n\n"
        f"1. Перейдите по ссылке и оплатите <b>{config.price_rub} ₽</b>.\n"
        "2. После оплаты Tribute отправит уведомление боту.\n"
        "3. Ключ будет создан автоматически и придёт сюда в чат.\n\n"
        "Если ключ не пришёл в течение 1–2 минут, нажмите «Я оплатил» или напишите в поддержку."
    )
    await safe_edit(call.message, text, tribute_menu())
    await call.answer()


@dp.callback_query(F.data == "tribute_check")
async def tribute_check_callback(call: types.CallbackQuery) -> None:
    user = await DB.get_user(call.from_user.id)
    expires = from_iso(user["subscription_expires"]) if user else None
    if user and user["is_active"] and expires and expires > now_utc():
        await safe_edit(
            call.message,
            f"Оплата подтверждена. Подписка активна до <b>{fmt_dt(expires)}</b>.\n\nНажмите «Мой ключ», чтобы скопировать доступ.",
            main_menu(),
        )
    else:
        await safe_edit(
            call.message,
            f"Оплата пока не подтверждена. Подождите 1–2 минуты. Если проблема останется, напишите {config.support_username}.",
            tribute_menu(),
        )
    await call.answer()


@dp.callback_query(F.data == "mykey")
async def mykey_callback(call: types.CallbackQuery) -> None:
    user = await DB.get_user(call.from_user.id)
    expires = from_iso(user["subscription_expires"]) if user else None
    if user and user["is_active"] and expires and expires > now_utc():
        link = build_vless_link(user["vpn_id"])
        text = (
            "Ваш активный ключ:\n\n"
            f"<code>{link}</code>\n\n"
            f"Действует до: <b>{fmt_dt(expires)}</b>"
        )
    else:
        text = "У вас нет активной подписки. Нажмите «Получить доступ», чтобы оформить VPN."
    await safe_edit(call.message, text, back_to_menu())
    await call.answer()


@dp.callback_query(F.data == "guide")
async def guide_callback(call: types.CallbackQuery) -> None:
    await safe_edit(call.message, "Выберите вашу платформу:", guide_menu())
    await call.answer()


@dp.callback_query(F.data == "guide_ios")
async def guide_ios_callback(call: types.CallbackQuery) -> None:
    text = (
        "<b>Подключение на iOS</b>\n\n"
        "1. Установите приложение <b>Streisand</b> из App Store.\n"
        "2. Скопируйте VLESS-ключ из раздела «Мой ключ».\n"
        "3. Откройте приложение и нажмите <b>+</b>.\n"
        "4. Вставьте VLESS-ключ.\n"
        "5. Сохраните профиль и подключитесь."
    )
    await safe_edit(call.message, text, back_to_menu())
    await call.answer()


@dp.callback_query(F.data == "guide_android")
async def guide_android_callback(call: types.CallbackQuery) -> None:
    text = (
        "<b>Подключение на Android</b>\n\n"
        "1. Установите приложение <b>v2rayNG</b> из Play Store.\n"
        "2. Скопируйте VLESS-ключ из раздела «Мой ключ».\n"
        "3. Откройте приложение и нажмите <b>+</b>.\n"
        "4. Вставьте VLESS-ключ.\n"
        "5. Сохраните профиль и подключитесь."
    )
    await safe_edit(call.message, text, back_to_menu())
    await call.answer()


@dp.callback_query(F.data == "support")
async def support_callback(call: types.CallbackQuery) -> None:
    await safe_edit(call.message, f"Поддержка: {config.support_username}\n\nУкажите ваш ID: <code>{call.from_user.id}</code>", back_to_menu())
    await call.answer()


@dp.message(F.text.startswith("/addkey"))
async def admin_addkey(message: types.Message) -> None:
    if message.from_user.id != config.admin_id:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) == 1:
        await message.answer(
            "Ключи создаются автоматически в 3X-UI после оплаты.\n"
            "Для ручной выдачи используйте: <code>/give user_id</code>\n"
            "Для сохранения резервного ключа: <code>/addkey vless://...</code>"
        )
        return
    added = await DB.add_manual_key(parts[1].strip())
    await message.answer("Резервный ключ добавлен." if added else "Такой резервный ключ уже есть.")


@dp.message(F.text.startswith("/give "))
async def admin_give(message: types.Message) -> None:
    if message.from_user.id != config.admin_id:
        return
    try:
        target_id = int(message.text.split()[1])
    except (IndexError, ValueError):
        await message.answer("Использование: <code>/give user_id</code>")
        return
    target = await DB.get_user(target_id)
    name = target["name"] if target else "Пользователь"
    ok, error = await issue_access(target_id, name, "manual", f"manual:{target_id}:{uuid.uuid4()}")
    await message.answer("Доступ выдан." if ok else f"Не удалось выдать доступ: <code>{error}</code>")


@dp.message(F.text.startswith("/revoke "))
async def admin_revoke(message: types.Message) -> None:
    if message.from_user.id != config.admin_id:
        return
    try:
        target_id = int(message.text.split()[1])
    except (IndexError, ValueError):
        await message.answer("Использование: <code>/revoke user_id</code>")
        return
    user = await DB.get_user(target_id)
    if not user:
        await message.answer("Пользователь не найден.")
        return
    disabled = await XUI.disable_client(user)
    await DB.deactivate_user(target_id)
    await message.answer("Доступ отключён." if disabled else "Подписка отключена в базе, но клиент 3X-UI не был найден или панель недоступна.")
    try:
        await bot.send_message(target_id, "Ваша подписка отключена. Для продления нажмите «Получить доступ».", reply_markup=main_menu())
    except (TelegramForbiddenError, TelegramBadRequest):
        pass


@dp.message(F.text == "/users")
async def admin_users(message: types.Message) -> None:
    if message.from_user.id != config.admin_id:
        return
    users = await DB.all_users()
    if not users:
        await message.answer("Пользователей пока нет.")
        return
    lines = ["<b>Пользователи</b>\n"]
    active = 0
    for user in users[:80]:
        expires = from_iso(user["subscription_expires"])
        is_active = bool(user["is_active"]) and bool(expires and expires > now_utc())
        active += int(is_active)
        status = f"активен до {fmt_dt(expires)}" if is_active else "нет активной подписки"
        lines.append(f"{user['name']} — <code>{user['tg_id']}</code> — {status}")
    lines.append(f"\nАктивных: <b>{active}</b> / {len(users)}")
    await message.answer("\n".join(lines))


@dp.message(F.text == "/keys")
async def admin_keys(message: types.Message) -> None:
    if message.from_user.id != config.admin_id:
        return
    active, free_slots, manual_free = await DB.key_stats()
    text = (
        "<b>Ключи и лимиты</b>\n\n"
        f"Активных 3X-UI клиентов: <b>{active}</b>\n"
        f"Свободных мест по MAX_CLIENTS: <b>{free_slots}</b>\n"
        f"Резервных ручных ключей: <b>{manual_free}</b>\n"
        f"Inbound ID: <code>{config.xui_inbound_id}</code>\n"
        f"IP limit: <code>{config.xui_ip_limit}</code>"
    )
    await message.answer(text)


@dp.message()
async def any_message(message: types.Message) -> None:
    await DB.ensure_user(message.from_user.id, safe_name(message.from_user))
    await show_profile(message.chat.id, message.from_user.id)


async def tribute_webhook(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid json"}, status=400)

    logger.info("Tribute webhook: %s", data)

    if data == {"test_event": "test_event"} or data.get("test_event") == "test_event":
        return web.json_response({"ok": True, "ignored": "test_event"})

    if data.get("name") != "new_donation":
        return web.json_response({"ok": True, "ignored": "unsupported_event"})

    payload = data.get("payload") or {}
    telegram_user_id = payload.get("telegram_user_id")
    amount_kopecks = int(payload.get("amount") or 0)

    if not telegram_user_id:
        await notify_admin(f"Tribute оплата без telegram_user_id. Payload: <code>{json.dumps(payload, ensure_ascii=False)}</code>")
        return web.json_response({"ok": True, "ignored": "missing_telegram_user_id"})

    if amount_kopecks < config.price_rub * 100:
        await notify_admin(
            f"Tribute платёж с недостаточной суммой от <code>{telegram_user_id}</code>: {amount_kopecks / 100:.2f} ₽"
        )
        return web.json_response({"ok": True, "ignored": "amount_too_low"})

    raw_event = json.dumps(data, sort_keys=True, ensure_ascii=False)
    event_hash = hashlib.sha256(raw_event.encode("utf-8")).hexdigest()
    if not await DB.mark_event_processed(event_hash, "tribute"):
        return web.json_response({"ok": True, "ignored": "duplicate"})

    user_id = int(telegram_user_id)
    await DB.complete_pending(user_id, "tribute")
    payment_id = str(payload.get("id") or payload.get("donation_id") or payload.get("transaction_id") or event_hash)
    ok, error = await issue_access(user_id, "Пользователь", "tribute", payment_id)
    if not ok:
        try:
            await bot.send_message(
                user_id,
                f"Оплата получена, но ключ не удалось создать автоматически. Напишите в поддержку: {config.support_username}",
            )
        except Exception:
            pass
        return web.json_response({"ok": False, "error": error}, status=200)

    return web.json_response({"ok": True})


async def healthcheck(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "AstroVPN bot"})


async def expiry_checker() -> None:
    while True:
        try:
            expired_users = await DB.expired_active_users()
            for user in expired_users:
                await XUI.disable_client(user)
                await DB.deactivate_user(user["tg_id"])
                try:
                    await bot.send_message(
                        user["tg_id"],
                        "Ваша подписка закончилась. Для продления нажмите «Получить доступ».",
                        reply_markup=main_menu(),
                    )
                except (TelegramForbiddenError, TelegramBadRequest):
                    pass
                await notify_admin(f"Подписка пользователя <code>{user['tg_id']}</code> истекла и была отключена.")
        except Exception as exc:
            logger.exception("Subscription expiry check failed: %s", exc)
        await asyncio.sleep(600)


async def start_web_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", healthcheck)
    app.router.add_post("/tribute/webhook", tribute_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.port)
    await site.start()
    logger.info("Webhook server started on port %s", config.port)
    return runner


async def main() -> None:
    await DB.init()
    try:
        await XUI.login()
    except Exception as exc:
        logger.warning("3X-UI is not available on startup: %s. The bot will continue and retry on issuance.", exc)
    runner = await start_web_server()
    asyncio.create_task(expiry_checker())
    logger.info("Telegram bot polling started")
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
