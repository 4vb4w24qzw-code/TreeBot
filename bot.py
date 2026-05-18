"""
🌳 Telegram-бот "Посади дерево — получи скидку"
Район: Левенцовский, Ростов-на-Дону
"""

import asyncio
import logging
import os
import secrets
import io
import base64
import aiohttp

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, Contact, PhotoSize,
    ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

import imagehash
from PIL import Image

from database import Database

BOT_TOKEN  = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
GEMINI_KEY = os.getenv("GEMINI_KEY", "AIzaSyCCviLfgx38fM_GMAc2SGIeLvMwlmvG14c")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
PHASH_THRESHOLD = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

class PlantStates(StatesGroup):
    waiting_phone = State()
    waiting_photo = State()

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())
db  = Database("tree_bot.db")

def phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )

def remove_keyboard():
    return ReplyKeyboardRemove()

def generate_promo():
    return "TREE-" + secrets.token_hex(4).upper()

async def download_photo(photo: PhotoSize) -> bytes:
    file = await bot.get_file(photo.file_id)
    buf  = io.BytesIO()
    await bot.download_file(file.file_path, destination=buf)
    buf.seek(0)
    return buf.read()

def compute_phash(image_bytes: bytes) -> str:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    return str(imagehash.phash(img))

def is_duplicate(new_hash_str: str, existing_hashes: list) -> bool:
    new_hash = imagehash.hex_to_hash(new_hash_str)
    for h in existing_hashes:
        if new_hash - imagehash.hex_to_hash(h) < PHASH_THRESHOLD:
            return True
    return False

async def check_tree_with_gemini(image_bytes: bytes) -> tuple:
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                {"text": (
                    "Посмотри на это фото. На нём есть дерево (саженец, молодое или взрослое дерево)? "
                    "Ответь строго в формате:\n"
                    "ДЕРЕВО: ДА или ДЕРЕВО: НЕТ\n"
                    "ПРИЧИНА: (одно предложение на русском языке)"
                )}
            ]
        }]
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{GEMINI_URL}?key={GEMINI_KEY}",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return True, "Проверка недоступна"
                data = await resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                log.info(f"Gemini: {text}")
                is_tree = "ДЕРЕВО: ДА" in text.upper()
                reason = ""
                for line in text.split("\n"):
                    if "ПРИЧИНА:" in line.upper():
                        reason = line.split(":", 1)[-1].strip()
                        break
                return is_tree, reason
    except Exception as e:
        log.error(f"Gemini error: {e}")
        return True, "Проверка недоступна"

@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    if db.is_verified(msg.from_user.id):
        await msg.answer("👋 Ты уже верифицирован!\n\n🌳 Отправь фото посаженного дерева в Левенцовском районе — и получи промокод на скидку в кафе.", reply_markup=remove_keyboard())
        await state.set_state(PlantStates.waiting_photo)
    else:
        await msg.answer("🌱 <b>Привет! Это бот «Посади дерево»</b>\n\nСажай деревья в <b>Левенцовском районе</b> Ростова-на-Дону и получай скидки в кафе-партнёрах!\n\nДля начала нужно подтвердить свой номер телефона 👇", parse_mode="HTML", reply_markup=phone_keyboard())
        await state.set_state(PlantStates.waiting_phone)

@dp.message(PlantStates.waiting_phone, F.contact)
async def handle_phone(msg: Message, state: FSMContext):
    contact = msg.contact
    if contact.user_id != msg.from_user.id:
        await msg.answer("⚠️ Пожалуйста, поделись своим собственным номером телефона.")
        return
    phone = contact.phone_number.lstrip("+")
    if db.phone_exists(phone):
        await msg.answer("❌ Этот номер уже зарегистрирован.", reply_markup=remove_keyboard())
        return
    db.register_user(msg.from_user.id, phone, msg.from_user.username or "")
    await msg.answer("✅ <b>Номер подтверждён!</b>\n\n🌳 Посади дерево в <b>Левенцовском районе</b> и пришли фото — получишь промокод на скидку!", parse_mode="HTML", reply_markup=remove_keyboard())
    await state.set_state(PlantStates.waiting_photo)

@dp.message(PlantStates.waiting_phone)
async def handle_phone_wrong(msg: Message):
    await msg.answer("👇 Нажми кнопку <b>«Поделиться номером»</b> ниже.", parse_mode="HTML", reply_markup=phone_keyboard())

@dp.message(PlantStates.waiting_photo, F.photo)
async def handle_photo(msg: Message, state: FSMContext):
    user_id = msg.from_user.id
    best_photo = msg.photo[-1]
    await msg.answer("🔍 Анализируем фото...")
    try:
        image_bytes = await download_photo(best_photo)
        new_hash    = compute_phash(image_bytes)
    except Exception as e:
        log.error(f"Ошибка фото: {e}")
        await msg.answer("⚠️ Не удалось обработать фото. Попробуй ещё раз.")
        return

    # Проверка Gemini
    await msg.answer("🌳 Проверяем наличие дерева на фото...")
    is_tree, reason = await check_tree_with_gemini(image_bytes)
    if not is_tree:
        await msg.answer(f"❌ <b>На фото не обнаружено дерево.</b>\n\n<i>{reason}</i>\n\nОтправь фото с посаженным деревом или саженцем.", parse_mode="HTML")
        return

    # Антиплагиат
    if is_duplicate(new_hash, db.get_all_hashes()):
        await msg.answer("❌ <b>Это фото уже было загружено ранее.</b>\n\nОтправь новое уникальное фото.", parse_mode="HTML")
        return

    # Лимит
    if db.get_user_promo_count(user_id) >= 5:
        await msg.answer("⚠️ Ты уже получил максимум промокодов (5). Спасибо за участие! 🌿")
        return

    # Промокод
    promo = generate_promo()
    db.save_submission(user_id, new_hash, promo)
    await msg.answer(f"🎉 <b>Дерево засчитано!</b>\n\nТвой промокод:\n\n<code>{promo}</code>\n\n📍 Покажи на кассе в кафе-партнёрах Левенцовского района.\n<i>Действует 30 дней.</i>", parse_mode="HTML")

@dp.message(PlantStates.waiting_photo)
async def handle_photo_wrong(msg: Message):
    await msg.answer("📸 Отправь <b>фотографию</b> посаженного дерева.", parse_mode="HTML")

@dp.message(Command("my_promos"))
async def cmd_my_promos(msg: Message):
    promos = db.get_user_promos(msg.from_user.id)
    if not promos:
        await msg.answer("У тебя пока нет промокодов. Посади дерево! 🌳")
        return
    lines = ["🎟 <b>Твои промокоды:</b>\n"]
    for promo, created_at in promos:
        lines.append(f"• <code>{promo}</code> — {created_at[:10]}")
    await msg.answer("\n".join(lines), parse_mode="HTML")

@dp.message(Command("stats"))
async def cmd_stats(msg: Message):
    stats = db.get_stats()
    await msg.answer(f"📊 <b>Статистика:</b>\n\n👥 Пользователей: {stats['users']}\n📸 Фото принято: {stats['submissions']}\n🎟 Промокодов: {stats['promos']}", parse_mode="HTML")

async def main():
    db.init()
    log.info("Бот запущен 🌳")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
