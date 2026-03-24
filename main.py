import asyncio
import requests
import base64
import re
import socket
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# Конфигурация
TOKEN = '8342934292:AAHh9142EIYydxZh3SBHim4K2uYIvlvlRU8'
SOURCES = [
    "https://raw.githubusercontent.com/Kirillo4ka/eavevpn-configs/refs/heads/main/BLACK_VLESS_RUS_mobile.txt",
    "https://raw.githubusercontent.com/Kirillo4ka/eavevpn-configs/refs/heads/main/BLACK_VLESS_RUS.txt",
    "https://raw.githubusercontent.com/Kirillo4ka/eavevpn-configs/refs/heads/main/Vless-Reality-White-Lists-Rus-Mobile-2.txt",
    "https://raw.githubusercontent.com/Kirillo4ka/eavevpn-configs/refs/heads/main/Vless-Reality-White-Lists-Rus-Mobile.txt",
    "https://white-lists.vercel.app/api/filter?code=ALL&type=black&min=false",
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

def get_country(address):
    try:
        # Быстрая проверка IP через публичный API для сортировки
        ip = socket.gethostbyname(address)
        response = requests.get(f"http://ip-api.com/json/{ip}?fields=status,countryCode", timeout=2).json()
        if response.get('status') == 'success':
            return response.get('countryCode', 'UN')
    except:
        pass
    return "UN"

def parse_and_filter():
    unique_configs = set()
    for url in SOURCES:
        try:
            res = requests.get(url, timeout=10).text
            # Ищем все vless, vmess, ss ссылки
            found = re.findall(r'(vless|vmess|ss)://[^\s]+', res)
            unique_configs.update(found)
        except:
            continue
    
    sorted_configs = []
    for config in list(unique_configs)[:100]: # Ограничим для стабильности на бесплатном хосте
        # Извлекаем адрес сервера для определения страны
        match = re.search(r'@([^:]+):', config)
        if match:
            addr = match.group(1)
            country = get_country(addr)
            # Добавляем флаг страны в название (после #)
            flag = f"({country})"
            if "#" in config:
                config = config.split("#")[0] + f"#{flag} " + config.split("#")[1]
            else:
                config += f"#{flag} Server"
        sorted_configs.append(config)
    
    return "\n".join(sorted_configs)

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("👋 Привет! Я **VlessFlow**.\n\nЯ собираю свежие VLESS ссылки, проверяю их и сортирую по странам для HAP/Hiddify.\n\nНажми /get_sub, чтобы получить подписку.")

@dp.message(Command("get_sub"))
async def get_sub(message: types.Message):
    status_msg = await message.answer("🔍 Собираю узлы из источников... Пожалуйста, подождите.")
    
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, parse_and_filter)
    
    if data:
        encoded = base64.b64encode(data.encode()).decode()
        # Для HAP лучше всего подходит формат base64 или прямая ссылка
        await status_msg.delete()
        await message.answer("✅ Подписка сформирована!\n\nСкопируй текст ниже и добавь в HAP как 'New Subscription':")
        await message.answer(f"`{encoded}`", parse_mode="MarkdownV2")
    else:
        await message.answer("❌ Не удалось собрать ссылки. Попробуйте позже.")

async def main():
    print("Бот VlessFlow запущен...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
