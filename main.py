import asyncio
import requests
import base64
import re
import socket
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

TOKEN = os.getenv('BOT_TOKEN')

SOURCES = [
    "https://raw.githubusercontent.com/Kirillo4ka/eavevpn-configs/refs/heads/main/BLACK_VLESS_RUS_mobile.txt",
    "https://raw.githubusercontent.com/Kirillo4ka/eavevpn-configs/refs/heads/main/BLACK_VLESS_RUS.txt",
    "https://raw.githubusercontent.com/Kirillo4ka/eavevpn-configs/refs/heads/main/Vless-Reality-White-Lists-Rus-Mobile-2.txt",
    "https://raw.githubusercontent.com/Kirillo4ka/eavevpn-configs/refs/heads/main/Vless-Reality-White-Lists-Rus-Mobile.txt",
    "https://white-lists.vercel.app/api/filter?code=ALL&type=black&min=false",
    "https://gistpad.com/raw/mia-vpn-tg-reverse-engineer-s-basement",
    "https://white-lists.vercel.app/api/filter?code=RU&type=white&min=false",
    "https://raw.githubusercontent.com/zieng2/wl/main/vless_universal.txt",
    "https://raw.githubusercontent.com/Mihuil121/vpn-checker-backend-fox/main/checked/My_Euro/euro_universal.txt",
    "https://raw.githubusercontent.com/Mihuil121/vpn-checker-backend-fox/main/checked/RU_Best/ru_white.txt",
    "https://raw.githubusercontent.com/Ilyacom4ik/free-v2ray-2026/main/subscriptions/FreeCFGHub1.txt",
    "https://raw.githubusercontent.com/ByeWhiteLists/ByeWhiteLists2/refs/heads/main/ByeWhiteLists2.txt",
    "https://raw.githubusercontent.com/kort0881/vpn-checker-backend/refs/heads/main/checked/RU_Best/ru_white_all_WHITE.txt",
    "https://raw.githubusercontent.com/Maskkost93/kizyak-vpn-4.0/refs/heads/main/kizyaktestru.txt",
    "https://raw.githubusercontent.com/Maskkost93/kizyak-vpn-4.0/refs/heads/main/kizyakbeta6BL.txt",
    "https://raw.githubusercontent.com/Maskkost93/kizyak-vpn-4.0/refs/heads/main/kizyakbeta6.txt",
]

# ip-api.com: бесплатно до 45 запросов/мин — не превышаем
MAX_WORKERS = 30
MAX_PER_COUNTRY = 10
GEO_TIMEOUT = 3
HTTP_TIMEOUT = 10

bot = Bot(token=TOKEN)
dp = Dispatcher()


def extract_host_port(uri: str):
    """Извлекает (host, port) из URI любого протокола."""
    proto = uri.split('://')[0].lower()

    if proto == 'vmess':
        try:
            padded = uri[8:] + '=='
            data = json.loads(base64.b64decode(padded).decode(errors='ignore'))
            return str(data.get('add', '')), int(data.get('port', 0))
        except Exception:
            return None, None

    # vless, trojan, ss: ...@host:port или ...@[ipv6]:port
    m = re.search(r'@([^:/\[\]]+|\[[^\]]+\]):(\d+)', uri)
    if m:
        return m.group(1), int(m.group(2))
    return None, None


def resolve_host(host: str) -> str | None:
    """DNS-резолвинг хоста. Возвращает IP или None."""
    try:
        return socket.gethostbyname(host)
    except Exception:
        return None


def get_geo(ip: str) -> tuple:
    """Возвращает (country_code, country_name) по IP через ip-api.com."""
    try:
        r = requests.get(
            f"http://ip-api.com/json/{ip}?fields=countryCode,country",
            timeout=GEO_TIMEOUT
        ).json()
        code = r.get('countryCode') or 'UN'
        name = r.get('country') or 'Unknown'
        return code, name
    except Exception:
        return 'UN', 'Unknown'


def process_config(uri: str):
    """
    Обрабатывает один конфиг:
    1. Парсим host:port
    2. DNS-резолвинг (если не резолвится — конфиг мёртв)
    3. Геолокация по IP
    4. Возвращаем (tagged_uri, country_code, country_name)
    """
    host, port = extract_host_port(uri)
    if not host or not port:
        return None

    ip = resolve_host(host)
    if not ip:
        return None

    code, name = get_geo(ip)
    base = uri.split('#')[0]
    tagged = f"{base}#{code}-VlessFlow"
    return tagged, code, name


def fetch_raw_configs() -> list:
    """Скачивает и дедуплицирует конфиги из всех источников."""
    seen = set()
    headers = {'User-Agent': 'Mozilla/5.0'}

    for url in SOURCES:
        try:
            resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                found = re.findall(
                    r'(?:vless|vmess|ss|trojan)://[^\s#"\'<]+',
                    resp.text
                )
                for f in found:
                    seen.add(f.strip())
        except Exception:
            continue

    return list(seen)


def build_subscription():
    """
    Полный пайплайн: сбор → DNS-валидация → геолокация → группировка.
    Возвращает (base64_строка, статистика_по_странам).
    """
    print(f"Сбор конфигов из {len(SOURCES)} источников...")
    raw = fetch_raw_configs()
    print(f"Уникальных конфигов: {len(raw)}, запускаю обработку...")

    by_country = {}  # code -> {'name': str, 'configs': [str]}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_config, uri): uri for uri in raw}
        for fut in as_completed(futures):
            result = fut.result()
            if result:
                tagged, code, name = result
                if code not in by_country:
                    by_country[code] = {'name': name, 'configs': []}
                by_country[code]['configs'].append(tagged)

    # Итоговый список: по алфавиту стран, макс. MAX_PER_COUNTRY на страну
    final = []
    for code in sorted(by_country):
        configs = by_country[code]['configs'][:MAX_PER_COUNTRY]
        final.extend(configs)

    total = len(final)
    print(f"Готово: {total} серверов в {len(by_country)} странах")

    if not final:
        return '', {}

    b64 = base64.b64encode('\n'.join(final).encode()).decode()
    return b64, by_country


@dp.message(Command('start'))
async def cmd_start(m: types.Message):
    await m.answer(
        "👋 <b>VlessFlow</b>\n\n"
        "Собираю рабочие VPN-серверы из открытых источников, "
        "проверяю доступность и группирую по странам.\n\n"
        "🔹 /get_sub — получить подписку для Happ/Hiddify\n\n"
        "После добавления подписки в Happ — выбери нужную страну, "
        "приложение само протестирует серверы и подключится к лучшему.",
        parse_mode="HTML"
    )


@dp.message(Command('get_sub'))
async def cmd_get_sub(m: types.Message):
    msg = await m.answer(
        "🔄 Собираю конфиги и проверяю серверы... Подожди 1-2 минуты."
    )

    loop = asyncio.get_event_loop()
    b64, by_country = await loop.run_in_executor(None, build_subscription)

    if not b64:
        await msg.edit_text("⚠️ Рабочих серверов не найдено. Попробуй позже.")
        return

    total = sum(
        min(len(v['configs']), MAX_PER_COUNTRY)
        for v in by_country.values()
    )

    lines = []
    for code in sorted(by_country):
        name = by_country[code]['name']
        count = min(len(by_country[code]['configs']), MAX_PER_COUNTRY)
        lines.append(f"  <code>{code}</code> {name}: {count} серв.")
    stats = "\n".join(lines)

    await msg.delete()
    await m.answer(
        f"✅ <b>Подписка готова</b>\n\n"
        f"Серверов: <b>{total}</b> в <b>{len(by_country)}</b> странах\n\n"
        f"<b>По странам:</b>\n{stats}",
        parse_mode="HTML"
    )
    await m.answer(f"<code>{b64}</code>", parse_mode="HTML")


async def main():
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
