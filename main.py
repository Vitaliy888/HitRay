import asyncio
import requests
import base64
import re
import json
import os
import socket
import time
import urllib.parse
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
MAX_COUNTRIES = 10
PING_TIMEOUT = 2.0
MAX_PER_COUNTRY = 50   # сколько серверов одной страны пингуем
SERVERS_PER_COUNTRY = 3  # сколько лучших серверов берём в подписку
MAX_PING_MS = 400    # только быстрые серверы (медленные = нерабочие)
PING_ROUNDS = 2      # пингуем дважды — берём только стабильно отвечающие

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# ─── FSM ─────────────────────────────────────────────────────────────────────

class AddSource(StatesGroup):
    waiting_url = State()


# ─── Источники ───────────────────────────────────────────────────────────────

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
    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            return 0
        found = re.findall(r'(?:vless|vmess|ss|trojan)://[^\s"\'<]+', resp.text)
        return len(found)
    except Exception:
        return 0


# ─── Клавиатуры ──────────────────────────────────────────────────────────────

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
    b = InlineKeyboardBuilder()
    per_page = 5
    start = page * per_page
    chunk = sources[start:start + per_page]

    for i, url in enumerate(chunk):
        idx = start + i
        short = url.split('/')[-1][:35] or url[:35]
        b.button(text=f"🗑 {short}", callback_data=f"del_{idx}")

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


def kb_country():
    b = InlineKeyboardBuilder()
    b.button(text="🇷🇺 Россия", callback_data="sub_ru")
    b.button(text="🌍 Европа",  callback_data="sub_eu")
    b.button(text="🌐 Все",     callback_data="sub_all")
    b.button(text="🔙 Назад",   callback_data="main_menu")
    b.adjust(3, 1)
    return b.as_markup()


# ─── VPN логика ──────────────────────────────────────────────────────────────

RU_KW = ['rus', '/ru_', 'ru_white', 'code=ru', 'kizyak']
EU_KW = ['euro']

# Стандартные флаги стран в виде эмодзи
FLAG_MAP = {chr(0x1F1E6 + i): chr(ord('A') + i) for i in range(26)}

# Русские названия стран → ISO-код
RU_NAMES: dict[str, str] = {
    'россия': 'RU', 'русь': 'RU',
    'сербия': 'RS', 'польша': 'PL', 'франция': 'FR',
    'германия': 'DE', 'нидерланды': 'NL', 'голландия': 'NL',
    'швеция': 'SE', 'норвегия': 'NO', 'финляндия': 'FI',
    'австрия': 'AT', 'швейцария': 'CH', 'чехия': 'CZ',
    'румыния': 'RO', 'болгария': 'BG', 'венгрия': 'HU',
    'словакия': 'SK', 'словения': 'SI', 'хорватия': 'HR',
    'турция': 'TR', 'украина': 'UA', 'литва': 'LT',
    'латвия': 'LV', 'эстония': 'EE', 'беларусь': 'BY',
    'италия': 'IT', 'испания': 'ES', 'португалия': 'PT',
    'великобритания': 'GB', 'британия': 'GB', 'англия': 'GB',
    'сша': 'US', 'америка': 'US', 'япония': 'JP',
    'китай': 'CN', 'сингапур': 'SG', 'австралия': 'AU',
    'канада': 'CA', 'бразилия': 'BR', 'индия': 'IN',
    'казахстан': 'KZ', 'молдова': 'MD', 'грузия': 'GE',
    'армения': 'AM', 'азербайджан': 'AZ',
}


def filter_sources(sources: list, country: str) -> list:
    if country == 'all':
        return sources
    kw = RU_KW if country == 'ru' else EU_KW
    return [s for s in sources if any(k in s.lower() for k in kw)]


def parse_config(config: str):
    """Вернуть (host, port, remark) из URI конфига."""
    remark = ''
    body = config
    if '#' in config:
        body, tail = config.rsplit('#', 1)
        remark = urllib.parse.unquote(tail)

    try:
        proto = body.split('://', 1)[0].lower()

        if proto in ('vless', 'trojan'):
            after = body.split('://', 1)[1]
            host_port = after.split('@', 1)[1].split('?')[0].split('/')[0]
            host, port_s = host_port.rsplit(':', 1)
            return host.strip('[]'), int(port_s), remark

        if proto == 'vmess':
            b64 = body.split('://', 1)[1]
            padded = b64 + '=' * (-len(b64) % 4)
            data = json.loads(base64.b64decode(padded).decode('utf-8', errors='ignore'))
            host = str(data.get('add', ''))
            port = int(data.get('port', 443))
            remark = remark or str(data.get('ps', ''))
            return host, port, remark

        if proto == 'ss':
            after = body.split('://', 1)[1]
            if '@' in after:
                host_port = after.split('@', 1)[1].split('/')[0]
                host, port_s = host_port.rsplit(':', 1)
                return host.strip('[]'), int(port_s), remark
            # Legacy base64
            padded = after + '=' * (-len(after) % 4)
            decoded = base64.b64decode(padded).decode('utf-8', errors='ignore')
            host_port = decoded.split('@', 1)[1]
            host, port_s = host_port.rsplit(':', 1)
            return host.strip('[]'), int(port_s.split('/')[0]), remark
    except Exception:
        pass
    return '', 0, remark


def extract_country(remark: str) -> str:
    """Извлечь двухбуквенный код страны из remark."""
    # 1. Флаг-эмодзи: пары региональных индикаторов
    chars = list(remark)
    i = 0
    while i < len(chars) - 1:
        a, b = chars[i], chars[i + 1]
        if a in FLAG_MAP and b in FLAG_MAP:
            return FLAG_MAP[a] + FLAG_MAP[b]
        i += 1

    # 2. Русские названия стран
    low = remark.lower()
    for name, code in RU_NAMES.items():
        if name in low:
            return code

    # 3. Скобочные паттерны: [RU], (DE), |FR|
    m = re.search(r'[\[\(|]([A-Za-z]{2})[\]\)|]', remark)
    if m:
        return m.group(1).upper()

    # 4. Просто два заглавных в слове
    SKIP = {'OK', 'NO', 'IS', 'DO', 'GO', 'TO', 'BE', 'OR', 'AS', 'IN', 'ON', 'AN',
            'LT', 'LTE', 'GB', 'MB', 'IP', 'ID', 'SS', 'VL', 'VM', 'TG', 'UP'}
    m = re.search(r'\b([A-Z]{2})\b', remark.upper())
    if m and m.group(1) not in SKIP:
        return m.group(1)

    return 'XX'


def tcp_ping(host: str, port: int) -> float:
    """Латентность TCP-соединения в мс, или inf при ошибке."""
    try:
        t = time.perf_counter()
        s = socket.create_connection((host, port), timeout=PING_TIMEOUT)
        ms = (time.perf_counter() - t) * 1000
        s.close()
        return round(ms, 1)
    except Exception:
        return float('inf')


def fetch_one(url: str) -> list:
    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=HTTP_TIMEOUT)
        if resp.status_code == 200:
            # Захватываем весь URI включая #remark
            return re.findall(r'(?:vless|vmess|ss|trojan)://[^\s"\'<]+', resp.text)
    except Exception:
        pass
    return []


def _ping_entry(entry):
    """(config, host, port) → (avg_latency, config); inf если хотя бы 1 попытка упала."""
    cfg, host, port = entry
    results = [tcp_ping(host, port) for _ in range(PING_ROUNDS)]
    if any(r == float('inf') for r in results):
        return float('inf'), cfg
    return round(sum(results) / len(results), 1), cfg


def build_best_subscription(sources: list):
    """
    Возвращает (b64, summary).
    summary = [(country, latency_ms), ...] — топ MAX_COUNTRIES стран.
    1 самый быстрый сервер на страну.
    """
    # 1. Собираем все конфиги
    all_configs = []
    workers = max(1, len(sources))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for batch in ex.map(fetch_one, sources):
            all_configs.extend(batch)

    if not all_configs:
        return '', []

    # Дедупликация
    all_configs = list(dict.fromkeys(all_configs))

    # 2. Парсим и группируем по стране; дедупликация по хосту
    by_country: dict[str, list] = {}
    seen_hosts: set[str] = set()
    for cfg in all_configs:
        host, port, remark = parse_config(cfg)
        if not host or not port:
            continue
        if host in seen_hosts:
            continue
        seen_hosts.add(host)
        country = extract_country(remark)
        by_country.setdefault(country, []).append((cfg, host, port))

    if not by_country:
        return '', []

    # 3. Для каждой страны пингуем серверы и берём лучший
    ping_tasks = []
    country_of = {}  # id(entry) → country
    for country, entries in by_country.items():
        for entry in entries[:MAX_PER_COUNTRY]:
            ping_tasks.append(entry)
            country_of[id(entry)] = country

    # Параллельный пинг
    entry_lat = {}  # entry → latency
    with ThreadPoolExecutor(max_workers=min(100, len(ping_tasks))) as ex:
        for entry, (lat, _) in zip(ping_tasks, ex.map(_ping_entry, ping_tasks)):
            entry_lat[id(entry)] = (lat, entry[0])  # lat, config_str

    # Топ SERVERS_PER_COUNTRY серверов на страну (по возрастанию пинга)
    country_servers: dict[str, list] = {}  # country → [(lat, config), ...]
    for entry in ping_tasks:
        country = country_of[id(entry)]
        lat, cfg = entry_lat[id(entry)]
        if lat == float('inf') or lat > MAX_PING_MS:
            continue
        country_servers.setdefault(country, []).append((lat, cfg))

    # Убираем XX если есть нормальные страны
    if len(country_servers) > 1:
        country_servers.pop('XX', None)

    if not country_servers:
        return '', []

    # Лучший пинг страны = минимум среди её серверов
    country_best_lat = {c: min(lat for lat, _ in entries)
                        for c, entries in country_servers.items()}

    # 4. Топ MAX_COUNTRIES стран по минимальному пингу
    top_countries = sorted(country_best_lat, key=lambda c: country_best_lat[c])[:MAX_COUNTRIES]

    selected = []
    summary = []
    for country in top_countries:
        servers = sorted(country_servers[country])[:SERVERS_PER_COUNTRY]
        for lat, cfg in servers:
            selected.append(cfg)
        summary.append((country, servers[0][0]))

    b64 = base64.b64encode('\n'.join(selected).encode()).decode()
    return b64, summary


def upload_subscription(b64: str) -> str:
    content = b64.encode()

    # 1. 0x0.st
    try:
        r = requests.post('https://0x0.st',
                          files={'file': ('sub.txt', content, 'text/plain')},
                          timeout=12)
        if r.status_code == 200 and r.text.strip().startswith('http'):
            return r.text.strip()
    except Exception:
        pass

    # 2. transfer.sh
    try:
        r = requests.put('https://transfer.sh/sub.txt',
                         data=content,
                         headers={'Content-Type': 'text/plain', 'Max-Days': '3'},
                         timeout=12)
        if r.status_code == 200 and r.text.strip().startswith('http'):
            return r.text.strip()
    except Exception:
        pass

    # 3. paste.rs
    try:
        r = requests.post('https://paste.rs/',
                          data=content,
                          headers={'Content-Type': 'text/plain'},
                          timeout=12)
        if r.status_code in (200, 201) and r.text.strip().startswith('http'):
            return r.text.strip()
    except Exception:
        pass

    # 4. ix.io
    try:
        r = requests.post('https://ix.io',
                          data={'f:1': content},
                          timeout=12)
        if r.status_code == 200 and r.text.strip().startswith('http'):
            return r.text.strip()
    except Exception:
        pass

    return ''


# ─── Хэндлеры ────────────────────────────────────────────────────────────────

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


# ── Получить подписку ─────────────────────────────────────────────────────────

@dp.callback_query(F.data == "get_sub")
async def cb_get_sub(cb: types.CallbackQuery):
    sources = load_sources()
    if not sources:
        await cb.answer("Нет источников! Добавьте хотя бы один.", show_alert=True)
        return
    await cb.answer()
    await cb.message.edit_text(
        "🌍 <b>Выбери регион</b>",
        parse_mode="HTML",
        reply_markup=kb_country()
    )


@dp.callback_query(F.data.startswith("sub_"))
async def cb_get_sub_country(cb: types.CallbackQuery):
    country = cb.data[4:]  # ru / eu / all
    sources = filter_sources(load_sources(), country)
    if not sources:
        await cb.answer("Нет источников для этого региона.", show_alert=True)
        return

    await cb.answer()
    await cb.message.edit_text(
        "🔄 Собираю конфиги и тестирую серверы...\n"
        "<i>(занимает ~15–30 сек)</i>",
        parse_mode="HTML"
    )

    loop = asyncio.get_running_loop()
    b64, summary = await loop.run_in_executor(None, build_best_subscription, sources)

    if not b64:
        await cb.message.edit_text(
            "⚠️ Живых серверов не найдено. Попробуй другой регион.",
            reply_markup=kb_country()
        )
        return

    lines = [f"• {c} — {lat:.0f} мс" for c, lat in summary]
    summary_text = "\n".join(lines)

    url = await loop.run_in_executor(None, upload_subscription, b64)
    header = (
        f"✅ <b>Подписка готова</b> — {len(summary)} стран, по 1 серверу\n\n"
        f"<b>Результаты:</b>\n{summary_text}\n\n"
    )

    if url:
        await cb.message.edit_text(
            header + f"<code>{url}</code>\n\n"
            "<i>Вставь ссылку в приложение как Subscription URL</i>",
            parse_mode="HTML",
            reply_markup=kb_back_main()
        )
    else:
        await cb.message.edit_text(
            header + "⚠️ Не удалось загрузить подписку на хостинг.\n"
            "Проверь соединение сервера с интернетом.",
            parse_mode="HTML",
            reply_markup=kb_back_main()
        )


# ── Меню источников ───────────────────────────────────────────────────────────

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
    lines = [f"{start + i + 1}. <code>{url}</code>" for i, url in enumerate(chunk)]

    await cb.message.edit_text(
        f"📋 <b>Источники</b> (стр. {page + 1}/{total_pages})\n\n"
        + "\n".join(lines)
        + "\n\n<i>Нажми на источник чтобы удалить его</i>",
        parse_mode="HTML",
        reply_markup=markup
    )


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

    page = max(0, idx // 5)
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


@dp.message(AddSource.waiting_url)
async def msg_add_source_url(m: types.Message, state: FSMContext):
    url = m.text.strip()

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


# ─── Запуск ───────────────────────────────────────────────────────────────────

async def main():
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
