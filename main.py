import asyncio
import json
import uuid
from datetime import datetime, timedelta

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery
)
from aiogram.filters import CommandStart

# ====== КОНФИГ ======
BOT_TOKEN = ""
TRIBUTE_LINK = "https://t.me/tribute/app?startapp=dI5p"
SUPPORT_USERNAME = "@ВАШ_НИКНЕЙМ"
ADMIN_ID = 8339239363
STARS_AMOUNT = 1
PRICE_RUB = 199
DB_FILE = "users.json"
KEYS_FILE = "keys.json"
PENDING_FILE = "pending.json"  # заявки ожидающие оплаты

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ====== БАЗА ДАННЫХ ======
def load_db():
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_keys():
    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_keys(data):
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_pending():
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_pending(data):
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ====== ЗАЯВКИ ======
def create_pending(user_id, amount):
    """Создать заявку на оплату"""
    pending = load_pending()
    # Удаляем старые заявки этого пользователя
    to_delete = [k for k, v in pending.items() if v["user_id"] == str(user_id)]
    for k in to_delete:
        del pending[k]
    
    payment_id = str(uuid.uuid4())
    pending[payment_id] = {
        "user_id": str(user_id),
        "amount": amount,
        "created_at": datetime.now().isoformat(),
        "status": "waiting"
    }
    save_pending(pending)
    return payment_id

def find_pending(amount):
    """Найти заявку по сумме и времени (не старше 30 минут)"""
    pending = load_pending()
    now = datetime.now()
    candidates = []
    
    for payment_id, data in pending.items():
        if data["status"] != "waiting":
            continue
        if data["amount"] != amount:
            continue
        created = datetime.fromisoformat(data["created_at"])
        # Заявка не старше 30 минут
        if (now - created).seconds > 1800:
            continue
        candidates.append((payment_id, data, created))
    
    if not candidates:
        return None, None
    
    # Берём самую свежую заявку
    candidates.sort(key=lambda x: x[2], reverse=True)
    payment_id, data, _ = candidates[0]
    return payment_id, data

def complete_pending(payment_id):
    """Пометить заявку как выполненную"""
    pending = load_pending()
    if payment_id in pending:
        pending[payment_id]["status"] = "completed"
        save_pending(pending)

def cleanup_pending():
    """Удалить старые заявки (старше 1 часа)"""
    pending = load_pending()
    now = datetime.now()
    to_delete = []
    for payment_id, data in pending.items():
        created = datetime.fromisoformat(data["created_at"])
        if (now - created).seconds > 3600:
            to_delete.append(payment_id)
    for k in to_delete:
        del pending[k]
    save_pending(pending)

# ====== КЛЮЧИ ======
def add_key(key_value):
    keys = load_keys()
    for k, v in keys.items():
        if v["key"] == key_value:
            return False
    key_id = str(uuid.uuid4())
    keys[key_id] = {
        "key": key_value,
        "status": "free",
        "user_id": None,
        "assigned_at": None
    }
    save_keys(keys)
    return True

def get_free_key():
    keys = load_keys()
    for key_id, data in keys.items():
        if data["status"] == "free":
            return key_id, data["key"]
    return None, None

def assign_key(key_id, user_id):
    keys = load_keys()
    if key_id in keys:
        keys[key_id]["status"] = "used"
        keys[key_id]["user_id"] = str(user_id)
        keys[key_id]["assigned_at"] = datetime.now().isoformat()
        save_keys(keys)

def get_user_key(user_id):
    keys = load_keys()
    uid = str(user_id)
    for key_id, data in keys.items():
        if data["user_id"] == uid and data["status"] == "used":
            return data["key"]
    return None

def delete_key_by_user(user_id):
    keys = load_keys()
    uid = str(user_id)
    to_delete = None
    for key_id, data in keys.items():
        if data["user_id"] == uid:
            to_delete = key_id
            break
    if to_delete:
        del keys[to_delete]
        save_keys(keys)
        return True
    return False

def count_free_keys():
    keys = load_keys()
    return sum(1 for v in keys.values() if v["status"] == "free")

# ====== ПОЛЬЗОВАТЕЛИ ======
def create_user(user_id, name):
    db = load_db()
    uid = str(user_id)
    if uid not in db:
        db[uid] = {
            "name": name,
            "balance": 0,
            "subscription": None,
            "total_paid": 0
        }
        save_db(db)

def set_subscription(user_id, days=30, source="stars"):
    db = load_db()
    uid = str(user_id)
    if uid not in db:
        return None, None

    existing_key = get_user_key(user_id)

    if existing_key:
        existing_sub = db[uid].get("subscription")
        if existing_sub:
            old_exp = datetime.fromisoformat(existing_sub["expires"])
            base = old_exp if old_exp > datetime.now() else datetime.now()
        else:
            base = datetime.now()
        expires = base + timedelta(days=days)
        db[uid]["subscription"] = {
            "key": existing_key,
            "expires": expires.isoformat(),
            "source": source
        }
        db[uid]["total_paid"] = db[uid].get("total_paid", 0) + 1
        save_db(db)
        return existing_key, expires

    key_id, key_value = get_free_key()
    if not key_value:
        return None, None

    expires = datetime.now() + timedelta(days=days)
    assign_key(key_id, user_id)
    db[uid]["subscription"] = {
        "key": key_value,
        "expires": expires.isoformat(),
        "source": source
    }
    db[uid]["total_paid"] = db[uid].get("total_paid", 0) + 1
    save_db(db)
    return key_value, expires

def get_active_subscription(user_id):
    db = load_db()
    uid = str(user_id)
    if uid not in db:
        return None
    sub = db[uid].get("subscription")
    if not sub:
        return None
    if datetime.fromisoformat(sub["expires"]) > datetime.now():
        return sub
    return None

def get_user(user_id):
    db = load_db()
    return db.get(str(user_id))

# ====== КЛАВИАТУРЫ ======
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐️ Получить доступ", callback_data="buy")],
        [InlineKeyboardButton(text="🔑 Мой ключ", callback_data="mykey")],
        [InlineKeyboardButton(text="📖 Гайд по подключению", callback_data="guide")],
        [InlineKeyboardButton(text="🆘 Поддержка", callback_data="support")]
    ])

def back_to_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")]
    ])

def guide_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍏 iOS", callback_data="guide_ios")],
        [InlineKeyboardButton(text="🤖 Android", callback_data="guide_android")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")]
    ])

def buy_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"⭐️ Telegram Stars ({STARS_AMOUNT} ⭐)",
            callback_data="pay_stars"
        )],
        [InlineKeyboardButton(
            text=f"💳 СБП / Карта ({PRICE_RUB} ₽)",
            callback_data="pay_tribute"
        )],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")]
    ])

def confirm_tribute_menu():
    """Кнопка после создания заявки"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data="tribute_check")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")]
    ])

# ====== ЧЕК ======
async def send_receipt(chat_id, key_value, expires, stars_amount=None, source="stars"):
    if source == "stars":
        method = f"⭐️ Telegram Stars ({stars_amount} ⭐)"
    elif source == "manual":
        method = "👤 Выдан администратором"
    else:
        method = f"💳 СБП / Карта ({PRICE_RUB} ₽)"

    text = (
        "🧾 <b>ЧЕК ОБ ОПЛАТЕ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📦 Товар: VPN доступ на 30 дней\n"
        f"💳 Способ: {method}\n"
        f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        f"⏳ Истекает: {expires.strftime('%d.%m.%Y %H:%M')}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🔑 Ваш ключ:\n<code>{key_value}</code>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💡 Нажмите на ключ чтобы скопировать\n"
        "📖 Как подключить — раздел <b>Гайд</b>"
    )
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=back_to_menu())

# ====== ПРОФИЛЬ ======
async def show_profile(chat_id, user_id, message_id=None):
    db = load_db()
    uid = str(user_id)
    if uid not in db:
        return
    user = db[uid]
    active_sub = get_active_subscription(user_id)
    free_keys = count_free_keys()

    if active_sub:
        exp = datetime.fromisoformat(active_sub["expires"])
        sub_text = f"✅ Подписка активна до {exp.strftime('%d.%m.%Y %H:%M')}"
    else:
        sub_text = "❌ Подписка отсутствует"

    text = (
        f"👋 Привет, {user['name']}!\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"💰 Стоимость: {PRICE_RUB} ₽ / {STARS_AMOUNT} ⭐\n"
        f"🔓 Свободных мест: {free_keys}\n\n"
        f"{sub_text}"
    )
    if message_id:
        await bot.edit_message_text(
            text=text, chat_id=chat_id,
            message_id=message_id,
            reply_markup=main_menu(),
            parse_mode="HTML"
        )
    else:
        await bot.send_message(
            chat_id, text,
            reply_markup=main_menu(),
            parse_mode="HTML"
        )

# ====== TRIBUTE WEBHOOK ======
async def tribute_webhook(request):
    try:
        data = await request.json()
        print(f"Tribute webhook: {data}")

        name = data.get("name")
        if name != "new_donation":
            return web.Response(status=200)

        payload = data.get("payload", {})
        amount = payload.get("amount", 0)
        telegram_user_id = payload.get("telegram_user_id")

        if not telegram_user_id:
            await bot.send_message(
                ADMIN_ID,
                f"⚠️ Оплата без Telegram ID!\nСумма: {amount/100} ₽",
                parse_mode="HTML"
            )
            return web.Response(status=200)

        user_id = int(telegram_user_id)
        db = load_db()
        uid = str(user_id)

        if uid not in db:
            create_user(user_id, "Пользователь")

        key_value, expires = set_subscription(user_id, days=30, source="tribute")

        if not key_value:
            await bot.send_message(
                user_id,
                f"⚠️ Оплата прошла но свободных ключей нет!\n"
                f"Напишите {SUPPORT_USERNAME}"
            )
            return web.Response(status=200)

        await send_receipt(user_id, key_value, expires, source="tribute")

        user = get_user(user_id)
        await bot.send_message(
            ADMIN_ID,
            f"💰 <b>Новая оплата Tribute!</b>\n\n"
            f"👤 {user['name']} (ID: <code>{user_id}</code>)\n"
            f"💳 Сумма: {amount/100} ₽\n"
            f"📅 До: {expires.strftime('%d.%m.%Y %H:%M')}\n"
            f"🔓 Осталось свободных: {count_free_keys()}",
            parse_mode="HTML"
        )

    except Exception as e:
        print(f"Webhook error: {e}")
        await bot.send_message(ADMIN_ID, f"❌ Ошибка webhook: {e}")

    return web.Response(status=200)

# ====== МОЙ КЛЮЧ ======
@dp.callback_query(F.data == "mykey")
async def mykey_callback(call: types.CallbackQuery):
    active_sub = get_active_subscription(call.from_user.id)
    if active_sub:
        exp = datetime.fromisoformat(active_sub["expires"])
        text = (
            "🔑 <b>Ваш активный ключ:</b>\n\n"
            f"<code>{active_sub['key']}</code>\n\n"
            f"📅 Действует до: {exp.strftime('%d.%m.%Y %H:%M')}\n\n"
            "💡 Нажмите на ключ чтобы скопировать"
        )
    else:
        text = (
            "❌ У вас нет активного ключа.\n\n"
            "Нажмите <b>⭐️ Получить доступ</b> чтобы оформить подписку."
        )
    await bot.edit_message_text(
        text=text, chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=back_to_menu(),
        parse_mode="HTML"
    )
    await call.answer()

# ====== ГАЙД ======
@dp.callback_query(F.data == "guide")
async def guide_callback(call: types.CallbackQuery):
    await bot.edit_message_text(
        text="📖 <b>Выберите вашу платформу:</b>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=guide_menu(),
        parse_mode="HTML"
    )
    await call.answer()

@dp.callback_query(F.data == "guide_ios")
async def guide_ios_callback(call: types.CallbackQuery):
    text = (
        "🍏 <b>Подключение на iOS:</b>\n\n"
        "1️⃣ Скачайте Amnezia VPN:\n"
        "https://apps.apple.com/us/app/amneziavpn/id1600529900\n\n"
        "2️⃣ Оформите подписку в боте\n\n"
        "3️⃣ Скопируйте ключ в разделе 🔑 Мой ключ\n\n"
        "4️⃣ Откройте Amnezia VPN\n\n"
        "5️⃣ Нажмите <b>+</b> внизу экрана\n\n"
        "6️⃣ Вставьте ключ и нажмите <b>Подключиться</b> ✅"
    )
    await bot.edit_message_text(
        text=text, chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=back_to_menu(),
        parse_mode="HTML"
    )
    await call.answer()

@dp.callback_query(F.data == "guide_android")
async def guide_android_callback(call: types.CallbackQuery):
    text = (
        "🤖 <b>Подключение на Android:</b>\n\n"
        "1️⃣ Скачайте Amnezia VPN:\n"
        "https://play.google.com/store/apps/details?id=org.amnezia.vpn\n\n"
        "2️⃣ Оформите подписку в боте\n\n"
        "3️⃣ Скопируйте ключ в разделе 🔑 Мой ключ\n\n"
        "4️⃣ Откройте Amnezia VPN\n\n"
        "5️⃣ Нажмите <b>+</b> внизу экрана\n\n"
        "6️⃣ Вставьте ключ и нажмите <b>Подключиться</b> ✅"
    )
    await bot.edit_message_text(
        text=text, chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=back_to_menu(),
        parse_mode="HTML"
    )
    await call.answer()

# ====== ПОДДЕРЖКА ======
@dp.callback_query(F.data == "support")
async def support_callback(call: types.CallbackQuery):
    text = (
        f"🆘 <b>Поддержка:</b>\n\n"
        f"По всем вопросам: {SUPPORT_USERNAME}\n\n"
        f"⏱ Отвечаем в течение 24 часов."
    )
    await bot.edit_message_text(
        text=text, chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=back_to_menu(),
        parse_mode="HTML"
    )
    await call.answer()

# ====== КУПИТЬ ======
@dp.callback_query(F.data == "buy")
async def buy_callback(call: types.CallbackQuery):
    free_keys = count_free_keys()
    if free_keys == 0:
        await bot.edit_message_text(
            text=(
                "😔 <b>Свободных мест нет.</b>\n\n"
                f"Напишите {SUPPORT_USERNAME} — добавим вас в очередь."
            ),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=back_to_menu(),
            parse_mode="HTML"
        )
        await call.answer()
        return

    active_sub = get_active_subscription(call.from_user.id)
    if active_sub:
        exp = datetime.fromisoformat(active_sub["expires"])
        text = (
            f"✅ Подписка активна до {exp.strftime('%d.%m.%Y %H:%M')}.\n\n"
            f"Хотите продлить? Выберите способ оплаты:"
        )
    else:
        text = (
            f"💰 <b>Доступ на 30 дней</b>\n\n"
            f"🔓 Свободных мест: {free_keys}\n\n"
            f"Выберите способ оплаты:"
        )
    await bot.edit_message_text(
        text=text, chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=buy_menu(),
        parse_mode="HTML"
    )
    await call.answer()

# ====== ОПЛАТА STARS ======
@dp.callback_query(F.data == "pay_stars")
async def pay_stars_callback(call: types.CallbackQuery):
    if count_free_keys() == 0:
        await call.answer("😔 Свободных мест нет! Напишите в поддержку.", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=call.message.chat.id,
        title="🔐 VPN доступ на 30 дней",
        description=f"Подписка на 30 дней — {STARS_AMOUNT} ⭐",
        payload=f"sub30_{call.from_user.id}_{uuid.uuid4()}",
        currency="XTR",
        prices=[LabeledPrice(label="Подписка 30 дней", amount=STARS_AMOUNT)],
        provider_token="",
    )
    await call.answer()

# ====== PRE-CHECKOUT ======
@dp.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

# ====== УСПЕШНАЯ ОПЛАТА STARS ======
@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    user_id = message.from_user.id
    stars = message.successful_payment.total_amount
    key_value, expires = set_subscription(user_id, days=30, source="stars")

    if not key_value:
        await message.answer(
            f"⚠️ Оплата прошла но свободных ключей нет!\n"
            f"Напишите {SUPPORT_USERNAME}"
        )
        return

    await send_receipt(message.chat.id, key_value, expires, stars_amount=stars, source="stars")

    user = get_user(user_id)
    await bot.send_message(
        ADMIN_ID,
        f"💰 <b>Новая оплата Stars!</b>\n\n"
        f"👤 {user['name']} (ID: <code>{user_id}</code>)\n"
        f"⭐ Stars: {stars}\n"
        f"📅 До: {expires.strftime('%d.%m.%Y %H:%M')}\n"
        f"🔓 Осталось свободных: {count_free_keys()}",
        parse_mode="HTML"
    )

# ====== ОПЛАТА TRIBUTE ======
@dp.callback_query(F.data == "pay_tribute")
async def pay_tribute_callback(call: types.CallbackQuery):
    user_id = call.from_user.id
    create_user(user_id, call.from_user.first_name or "Пользователь")

    # Создаём заявку
    create_pending(user_id, PRICE_RUB)

    text = (
        "💳 <b>Оплата через СБП / Карту:</b>\n\n"
        f"1️⃣ Перейдите по ссылке и оплатите {PRICE_RUB} ₽:\n"
        f"{TRIBUTE_LINK}\n\n"
        "2️⃣ После оплаты нажмите кнопку <b>✅ Я оплатил</b>\n\n"
        "⏱ Ключ выдаётся автоматически в течение минуты"
    )
    await bot.edit_message_text(
        text=text, chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=confirm_tribute_menu(),
        parse_mode="HTML"
    )
    await call.answer()

# ====== ПРОВЕРКА ОПЛАТЫ TRIBUTE ======
@dp.callback_query(F.data == "tribute_check")
async def tribute_check_callback(call: types.CallbackQuery):
    user_id = call.from_user.id
    active_sub = get_active_subscription(user_id)

    if active_sub:
        exp = datetime.fromisoformat(active_sub["expires"])
        await bot.edit_message_text(
            text=(
                "✅ <b>Оплата подтверждена!</b>\n\n"
                f"Ваш ключ уже выдан.\n"
                f"Подписка до: {exp.strftime('%d.%m.%Y %H:%M')}\n\n"
                "Нажмите 🔑 Мой ключ чтобы скопировать."
            ),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=main_menu(),
            parse_mode="HTML"
        )
    else:
        await bot.edit_message_text(
            text=(
                "⏳ <b>Оплата ещё не подтверждена.</b>\n\n"
                "Подождите 1-2 минуты и нажмите снова.\n\n"
                f"Если проблема — напишите {SUPPORT_USERNAME}"
            ),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=confirm_tribute_menu(),
            parse_mode="HTML"
        )
    await call.answer()

# ====== АДМИН: добавить ключ ======
@dp.message(F.text.startswith("/addkey "))
async def admin_addkey(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    key_value = message.text[8:].strip()
    if not key_value:
        await message.answer("Использование: /addkey <ключ>")
        return
    success = add_key(key_value)
    if success:
        await message.answer(
            f"✅ Ключ добавлен!\n"
            f"🔓 Свободных: {count_free_keys()}"
        )
    else:
        await message.answer("❌ Такой ключ уже есть.")

# ====== АДМИН: выдать ключ вручную ======
@dp.message(F.text.startswith("/give "))
async def admin_give(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        target_id = int(message.text.split()[1])
        key_value, expires = set_subscription(target_id, days=30, source="manual")
        if not key_value:
            await message.answer("❌ Нет свободных ключей или пользователь не найден.")
            return
        await send_receipt(target_id, key_value, expires, source="manual")
        await message.answer(
            f"✅ Ключ выдан <code>{target_id}</code>\n"
            f"📅 До: {expires.strftime('%d.%m.%Y %H:%M')}",
            parse_mode="HTML"
        )
    except (IndexError, ValueError):
        await message.answer("Использование: /give <user_id>")

# ====== АДМИН: удалить ключ ======
@dp.message(F.text.startswith("/revoke "))
async def admin_revoke(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        target_id = int(message.text.split()[1])
        db = load_db()
        uid = str(target_id)
        if uid in db:
            db[uid]["subscription"] = None
            save_db(db)
        delete_key_by_user(target_id)
        await message.answer(
            f"✅ Ключ <code>{target_id}</code> удалён.",
            parse_mode="HTML"
        )
        await bot.send_message(
            target_id,
            "⚠️ <b>Ваша подписка закончилась.</b>\n\n"
            "Для продления нажмите кнопку ниже 👇",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⭐️ Продлить подписку", callback_data="buy")]
            ])
        )
    except (IndexError, ValueError):
        await message.answer("Использование: /revoke <user_id>")

# ====== АДМИН: ключи ======
@dp.message(F.text == "/keys")
async def admin_keys(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    keys = load_keys()
    if not keys:
        await message.answer("📭 База ключей пуста.")
        return
    free = [v for v in keys.values() if v["status"] == "free"]
    used = [v for v in keys.values() if v["status"] == "used"]
    text = (
        f"🗝 <b>База ключей:</b>\n\n"
        f"🟢 Свободных: {len(free)}\n"
        f"🔴 Занятых: {len(used)}\n"
        f"📊 Всего: {len(keys)}"
    )
    await message.answer(text, parse_mode="HTML")

# ====== АДМИН: пользователи ======
@dp.message(F.text == "/users")
async def admin_users(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    db = load_db()
    now = datetime.now()
    active_count = 0
    text = "👥 <b>Пользователи:</b>\n\n"
    for uid, user in db.items():
        sub = user.get("subscription")
        if sub and datetime.fromisoformat(sub["expires"]) > now:
            exp = datetime.fromisoformat(sub["expires"])
            status = f"✅ до {exp.strftime('%d.%m')}"
            active_count += 1
        else:
            status = "❌"
        text += f"{status} — {user['name']} <code>{uid}</code>\n"
    text += f"\n📊 Активных: {active_count} / {len(db)}"
    await message.answer(text, parse_mode="HTML")

# ====== ЛЮБОЕ СООБЩЕНИЕ ======
@dp.message()
async def any_message(message: types.Message):
    create_user(message.from_user.id, message.from_user.first_name or "Пользователь")
    await show_profile(message.chat.id, message.from_user.id)

# ====== ЗАПУСК ======
async def main():
    app = web.Application()
    app.router.add_post("/tribute/webhook", tribute_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    print("🤖 Бот запущен!")
    print("🌐 Webhook сервер запущен на порту 8080")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
