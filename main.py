import asyncio
import requests
import base64
import re
import json
import os
from concurrent.futures import ThreadPoolExecutor

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.getenv('BOT_TOKEN')
SOURCES_FILE = os.path.join(os.path.dirname(__file__), 'sources.json')

HTTP_TIMEOUT = 6

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# ─── FSM ────────────────────────────────────────────────────────────────────

class AddSource(StatesGroup):
    waiting_url = State()


# ─── Источники ──────────────────────────────────────────────────────────────

def load_sources() -> list:
    try:
        with open(SOURCES_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_sources(sources: list):
    with open(SOURCES_FILE, 'w', encoding='utf-8') as f:
        json.dump(sources, f, ensure_ascii=False, indent=2)


def validate_source(url: str) -> int:
    """
    Проверяет URL источника.
    Возвращает количество найденных конфигов или 0 если ничего / ошибка.
    """
    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            return 0
        found = re.findall(r'(?:vless|vmess|ss|trojan)://[^\s#"\'<]+', resp.text)
        return len(found)
    except Exception:
        return 0


# ─── Клавиатуры ─────────────────────────────────────────────────────────────

def kb_main():
    b = InlineKeyboardBuilder()
    b.button(text="📥 Получить подписку", callback_data="get_sub")
    b.button(text="⚙️ Управление источниками", callback_data="sources_menu")
    b.adjust(1)
    return b.as_markup()


def kb_sources_menu():
    b = InlineKeyboardBuilder()
    b.button(text="📋 Список источников", callback_data="list_sources")
    b.button(text="➕ Добавить источник", callback_data="add_source")
    b.button(text="🔙 Главное меню", callback_data="main_menu")
    b.adjust(1)
    return b.as_markup()


def kb_sources_list(sources: list, page: int = 0):
    """Список источников с кнопками удаления, по 5 на страницу."""
    b = InlineKeyboardBuilder()
    per_page = 5
    start = page * per_page
    chunk = sources[start:start + per_page]

    for i, url in enumerate(chunk):
        idx = start + i
        short = url.split('/')[-1][:35] or url[:35]
        b.button(text=f"🗑 {short}", callback_data=f"del_{idx}")

    # Навигация
    total_pages = (len(sources) - 1) // per_page + 1
    nav = []
    if page > 0:
        nav.append(("◀️", f"src_page_{page - 1}"))
    if page < total_pages - 1:
        nav.append(("▶️", f"src_page_{page + 1}"))
    for text, cb in nav:
        b.button(text=text, callback_data=cb)

    b.button(text="🔙 Назад", callback_data="sources_menu")
    b.adjust(1)
    return b.as_markup(), start, chunk, total_pages


def kb_cancel():
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data="sources_menu")
    return b.as_markup()


def kb_back_main():
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Главное меню", callback_data="main_menu")
    return b.as_markup()


# ─── VPN логика ─────────────────────────────────────────────────────────────

def fetch_one(url: str) -> list:
    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=HTTP_TIMEOUT)
        if resp.status_code == 200:
            return re.findall(r'(?:vless|vmess|ss|trojan)://[^\s#"\'<]+', resp.text)
    except Exception:
        pass
    return []


def build_subscription(sources: list):
    seen = set()
    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        for configs in ex.map(fetch_one, sources):
            for c in configs:
                seen.add(c.strip())
    configs = list(seen)
    if not configs:
        return '', 0
    b64 = base64.b64encode('\n'.join(configs).encode()).decode()
    return b64, len(configs)


# ─── Хэндлеры ───────────────────────────────────────────────────────────────

@dp.message(Command('start'))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer(
        "👋 <b>VlessFlow</b>\n\n"
        "Собираю рабочие VPN-серверы из открытых источников, "
        "проверяю доступность и группирую по странам.",
        parse_mode="HTML",
        reply_markup=kb_main()
    )


# Главное меню (по кнопке)
@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await cb.message.edit_text(
        "👋 <b>VlessFlow</b>\n\n"
        "Собираю рабочие VPN-серверы из открытых источников, "
        "проверяю доступность и группирую по странам.",
        parse_mode="HTML",
        reply_markup=kb_main()
    )


# ── Получить подписку ────────────────────────────────────────────────────────

@dp.callback_query(F.data == "get_sub")
async def cb_get_sub(cb: types.CallbackQuery):
    sources = load_sources()
    if not sources:
        await cb.answer("Нет источников! Добавьте хотя бы один.", show_alert=True)
        return

    await cb.answer()
    await cb.message.edit_text("🔄 Собираю конфиги из источников...")

    loop = asyncio.get_running_loop()
    b64, total = await loop.run_in_executor(None, build_subscription, sources)

    if not b64:
        await cb.message.edit_text(
            "⚠️ Конфигов не найдено. Проверь источники и попробуй снова.",
            reply_markup=kb_back_main()
        )
        return

    await cb.message.edit_text(
        f"✅ <b>Подписка готова</b> — {total} конфигов\n\n"
        "Импортируй файл в приложение (V2rayNG, Hiddify, Streisand и др.)",
        parse_mode="HTML",
        reply_markup=kb_back_main()
    )
    sub_file = types.BufferedInputFile(b64.encode(), filename="subscription.txt")
    await cb.message.answer_document(sub_file)


# ── Меню источников ──────────────────────────────────────────────────────────

@dp.callback_query(F.data == "sources_menu")
async def cb_sources_menu(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    sources = load_sources()
    await cb.message.edit_text(
        f"⚙️ <b>Управление источниками</b>\n\nСейчас активно: <b>{len(sources)}</b>",
        parse_mode="HTML",
        reply_markup=kb_sources_menu()
    )


# Список источников
@dp.callback_query(F.data.in_({"list_sources"}) | F.data.startswith("src_page_"))
async def cb_list_sources(cb: types.CallbackQuery):
    await cb.answer()
    page = 0
    if cb.data.startswith("src_page_"):
        page = int(cb.data.split("_")[-1])

    sources = load_sources()
    if not sources:
        await cb.message.edit_text(
            "📋 Источников нет. Добавьте первый!",
            reply_markup=kb_sources_menu()
        )
        return

    markup, start, chunk, total_pages = kb_sources_list(sources, page)
    lines = []
    for i, url in enumerate(chunk):
        lines.append(f"{start + i + 1}. <code>{url}</code>")

    await cb.message.edit_text(
        f"📋 <b>Источники</b> (стр. {page + 1}/{total_pages})\n\n"
        + "\n".join(lines)
        + "\n\n<i>Нажми на источник чтобы удалить его</i>",
        parse_mode="HTML",
        reply_markup=markup
    )


# Удаление источника
@dp.callback_query(F.data.startswith("del_"))
async def cb_delete_source(cb: types.CallbackQuery):
    idx = int(cb.data.split("_")[1])
    sources = load_sources()
    if idx >= len(sources):
        await cb.answer("Источник уже удалён.", show_alert=True)
        return

    removed = sources.pop(idx)
    save_sources(sources)
    short = removed.split('/')[-1][:50] or removed[:50]
    await cb.answer(f"Удалён: {short}", show_alert=True)

    # Обновляем список
    page = max(0, (idx // 5))
    if page * 5 >= len(sources) and page > 0:
        page -= 1

    if not sources:
        await cb.message.edit_text(
            "📋 Источников нет. Добавьте первый!",
            reply_markup=kb_sources_menu()
        )
        return

    markup, start, chunk, total_pages = kb_sources_list(sources, page)
    lines = [f"{start + i + 1}. <code>{url}</code>" for i, url in enumerate(chunk)]
    await cb.message.edit_text(
        f"📋 <b>Источники</b> (стр. {page + 1}/{total_pages})\n\n"
        + "\n".join(lines)
        + "\n\n<i>Нажми на источник чтобы удалить его</i>",
        parse_mode="HTML",
        reply_markup=markup
    )


# Добавить источник — шаг 1: запрос URL
@dp.callback_query(F.data == "add_source")
async def cb_add_source(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(AddSource.waiting_url)
    await cb.answer()
    await cb.message.edit_text(
        "➕ <b>Добавить источник</b>\n\n"
        "Отправь ссылку на файл с VPN-конфигами.\n\n"
        "<i>Файл должен содержать строки вида:\n"
        "vless://..., vmess://..., ss://..., trojan://...</i>",
        parse_mode="HTML",
        reply_markup=kb_cancel()
    )


# Добавить источник — шаг 2: получаем URL, валидируем
@dp.message(AddSource.waiting_url)
async def msg_add_source_url(m: types.Message, state: FSMContext):
    url = m.text.strip()

    # Базовая проверка формата
    if not url.startswith(('http://', 'https://')):
        await m.answer(
            "⚠️ Ссылка должна начинаться с http:// или https://\n"
            "Попробуй ещё раз или нажми Отмена.",
            reply_markup=kb_cancel()
        )
        return

    msg = await m.answer("🔍 Проверяю источник...")

    loop = asyncio.get_running_loop()
    count = await loop.run_in_executor(None, validate_source, url)

    if count == 0:
        await msg.edit_text(
            "❌ <b>Источник не прошёл проверку</b>\n\n"
            "Либо URL недоступен, либо в нём нет vless/vmess/ss/trojan конфигов.\n\n"
            "Попробуй другую ссылку или нажми Отмена.",
            parse_mode="HTML",
            reply_markup=kb_cancel()
        )
        return

    # Проверяем дубликат
    sources = load_sources()
    if url in sources:
        await msg.edit_text(
            "ℹ️ Этот источник уже есть в списке.",
            reply_markup=kb_sources_menu()
        )
        await state.clear()
        return

    sources.append(url)
    save_sources(sources)
    await state.clear()

    await msg.edit_text(
        f"✅ <b>Источник добавлен!</b>\n\n"
        f"<code>{url}</code>\n\n"
        f"Найдено конфигов: <b>{count}</b>\n"
        f"Всего источников: <b>{len(sources)}</b>",
        parse_mode="HTML",
        reply_markup=kb_sources_menu()
    )


# ─── Запуск ─────────────────────────────────────────────────────────────────

async def main():
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
