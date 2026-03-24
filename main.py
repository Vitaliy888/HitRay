import asyncio
import requests
import base64
import re
import socket
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

TOKEN = os.getenv('BOT_TOKEN')

# Список твоих источников
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
    "https://raw.githubusercontent.com/Maskkost93/kizyak-vpn-4.0/refs/heads/main/kizyakbeta6.txt"
]

bot = Bot(token=TOKEN)
dp = Dispatcher()

def get_flag(addr):
    """Определяет страну по IP/Хосту."""
    try:
        ip = socket.gethostbyname(addr)
        r = requests.get(f"http://ip-api.com/json/{ip}?fields=countryCode", timeout=1).json()
        return f"[{r.get('countryCode', '??')}]"
    except:
        return "[UN]"

def fetch_data():
    all_configs = set()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    for url in SOURCES:
        # Авто-замена обычных ссылок GitHub на RAW, если ты случайно вставил не ту
        url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/").replace("/tree/", "/")
        
        try:
            print(f"Проверяю: {url}")
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                # Ищем vless, vmess, ss, trojan
                found = re.findall(r'(?:vless|vmess|ss|trojan)://[^\s#"\'<]+', resp.text)
                all_configs.update(found)
        except:
            continue

    final = []
    # Обрабатываем найденное
    for conf in list(all_configs)[:200]:
        # Чистим от мусора
        conf = conf.strip()
        # Пробуем вытащить адрес для флага
        match = re.search(r'@([^:/]+)[:/]', conf)
        if match:
            host = match.group(1)
            flag = get_flag(host)
            # Убираем старый тег после # и ставим свой
            base = conf.split('#')[0]
            conf = f"{base}#{flag}_VlessFlow"
        final.append(conf)
        
    return "\n".join(final)

@dp.message(Command("start"))
async def start(m: types.Message):
    await m.answer("✅ Бот готов. Нажми /get_sub для получения свежей подписки.")

@dp.message(Command("get_sub"))
async def sub(m: types.Message):
    msg = await m.answer("🔄 Сканирую источники... Это может занять до 20 секунд.")
    
    # Выполняем сбор в отдельном потоке
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, fetch_data)
    
    if data:
        b64 = base64.b64encode(data.encode()).decode()
        await msg.delete()
        await m.answer("🚀 Твоя подписка (Base64) для HAP/Hiddify:")
        # Отправляем в моноширинном шрифте, чтобы удобно было копировать
        await m.answer(f"`{b64}`", parse_mode="Markdown")
    else:
        await m.answer("⚠️ Ссылки не найдены. Проверь источники или попробуй позже.")

async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
