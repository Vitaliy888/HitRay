import asyncio
import requests
import base64
import re
import socket
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# Получаем токен из настроек хостинга (Environment Variables)
TOKEN = os.getenv('BOT_TOKEN')

# Твои источники ссылок (очищены от ссылок на папки, оставлены только RAW и API)
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

def get_country_flag(address):
    """Определяет страну по IP/Домену и возвращает флаг или код."""
    try:
        ip = socket.gethostbyname(address)
        # Используем бесплатный API для определения страны
        res = requests.get(f"http://ip-api.com/json/{ip}?fields=status,countryCode", timeout=2).json()
        if res.get('status') == 'success':
            code = res.get('countryCode', 'UN')
            return f"[{code}]"
    except:
        pass
    return "[??]"

def process_configs():
    """Сбор, фильтрация и сортировка конфигов."""
    unique_configs = set()
    
    for url in SOURCES:
        try:
            response = requests.get(url, timeout=15).text
            # Ищем протоколы vless, vmess и ss
            found = re.findall(r'(vless|vmess|ss)://[^\s|#]+', response)
            unique_configs.update(found)
        except Exception as e:
            print(f"Ошибка при чтении {url}: {e}")
            continue

    final_list = []
    # Берем первые 150 для стабильности на бесплатном хостинге
    for config in list(unique_configs)[:150]:
        # Извлекаем адрес сервера для GeoIP
        match = re.search(r'@([^:]+):', config)
        if match:
            addr = match.group(1)
            flag = get_country_flag(addr)
            # Добавляем метку страны в конец ссылки через #
            config += f"#{flag}_VlessFlow_Node"
        final_list.append(config)
    
    return "\n".join(final_list)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 **Добро пожаловать в VlessFlow!**\n\n"
        "Я автоматически собираю и фильтрую VLESS/SS ссылки из надежных источников.\n\n"
        "🔹 Используй /get_sub чтобы получить подписку.\n"
        "🔹 Все ссылки проверены на формат и размечены по странам."
    )

@dp.message(Command("get_sub"))
async def cmd_get_sub(message: types.Message):
    wait_msg = await message.answer("⏳ Собираю актуальные узлы... Пожалуйста, подождите.")
    
    # Запускаем тяжелую задачу в отдельном потоке, чтобы бот не «зависал»
    loop = asyncio.get_event_loop()
    configs_data = await loop.run_in_executor(None, process_configs)
    
    if configs_data:
        # Кодируем в Base64 для прямой вставки в HAP/Hiddify
        encoded = base64.b64encode(configs_data.encode()).decode()
        await wait_msg.delete()
        await message.answer("✅ **Ваша подписка готова!**\n\nСкопируйте код ниже и добавьте его в HAP:")
        await message.answer(f"`{encoded}`", parse_mode="Markdown")
    else:
        await message.answer("❌ Ошибка: не удалось получить данные из источников.")

async def main():
    if not TOKEN:
        print("КРИТИЧЕСКАЯ ОШИБКА: Токен не найден в переменных окружения!")
        return
    print("Бот запущен и готов к работе...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
