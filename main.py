import asyncio
import ipaddress
import requests
import base64
import re
import json
import os
import socket
import ssl
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import (
    init_db, load_sources, add_source, remove_source_by_hash,
    source_exists, sources_count, save_history, last_history, url_hash,
    get_alive_configs, save_config_results, configs_alive_count,
    configs_cache_age_minutes, save_discovered_source, get_discovered_sources,
    mark_discovered_added,
)

TOKEN = os.getenv('BOT_TOKEN')
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN', '')   # необязателен, но снимает rate-limit

try:
    ADMIN_ID = int(os.getenv('ADMIN_ID', '-1'))
except ValueError:
    ADMIN_ID = -1

HTTP_TIMEOUT = 6
MAX_COUNTRIES = 10
PING_TIMEOUT = 4.0   # запас для дальних серверов
MAX_PER_COUNTRY = 80
SERVERS_PER_COUNTRY = 3
MAX_PING_MS = 700
PING_ROUNDS = 2      # делаем два замера, берём лучший

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# ─── FSM ─────────────────────────────────────────────────────────────────────

class AddSource(StatesGroup):
    waiting_url = State()


# ─── Источники ───────────────────────────────────────────────────────────────

def validate_source(url: str) -> int:
    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            return 0
        text = resp.text.strip()
        found = RE_CONFIG_LINK.findall(text)
        if found:
            return len(found)
        # Пробуем base64-подписку
        try:
            padded = text + '=' * (-len(text) % 4)
            decoded = base64.b64decode(padded).decode('utf-8', errors='ignore')
            found = RE_CONFIG_LINK.findall(decoded)
            return len(found)
        except Exception:
            pass
        return 0
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
    total_pages = max(1, -(-len(sources) // per_page))  # ceiling division

    for url in chunk:
        short = url.split('/')[-1][:32] or url[:32]
        b.button(text=f"🗑 {short}", callback_data=f"del_{url_hash(url)}")

    nav = []
    if page > 0:
        nav.append(("◀️ Пред.", f"src_page_{page - 1}"))
    if page < total_pages - 1:
        nav.append(("▶️ След.", f"src_page_{page + 1}"))
    for text, cb in nav:
        b.button(text=text, callback_data=cb)

    b.button(text="🔙 Назад", callback_data="sources_menu")

    # Источники — по одному, навигация — в одну строку, «Назад» — отдельно
    sizes = [1] * len(chunk)
    if nav:
        sizes.append(len(nav))
    sizes.append(1)
    b.adjust(*sizes)

    return b.as_markup(), start, chunk, total_pages


def kb_cancel():
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data="sources_menu")
    return b.as_markup()


def kb_back_main():
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Главное меню", callback_data="main_menu")
    return b.as_markup()


def kb_discover_add():
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить все найденные", callback_data="discover_add_all")
    b.button(text="🔙 Главное меню", callback_data="main_menu")
    b.adjust(1)
    return b.as_markup()


# ─── Регулярки ───────────────────────────────────────────────────────────────

RE_CONFIG_LINK = re.compile(r'(?:vless|vmess|ss|trojan)://[^\s"\' <]+')
RE_COUNTRY_BRACKETS = re.compile(r'[\[\(|]([A-Za-z]{2})[\]\)|]')
RE_COUNTRY_WORDS = re.compile(r'\b([A-Z]{2})\b')


# ─── VPN логика ──────────────────────────────────────────────────────────────

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
    m = RE_COUNTRY_BRACKETS.search(remark)
    if m:
        return m.group(1).upper()

    # 4. Просто два заглавных в слове
    SKIP = {'OK', 'NO', 'IS', 'DO', 'GO', 'TO', 'BE', 'OR', 'AS', 'IN', 'ON', 'AN',
            'LT', 'LTE', 'GB', 'MB', 'IP', 'ID', 'SS', 'VL', 'VM', 'TG', 'UP'}
    m = RE_COUNTRY_WORDS.search(remark.upper())
    if m and m.group(1) not in SKIP:
        return m.group(1)

    return 'XX'


def tcp_ping(host: str, port: int) -> float:
    """Латентность TCP-соединения в мс, или inf при ошибке."""
    try:
        # Resolve IP first so DNS doesn't skew the ping measurement
        # getaddrinfo works for both IPv4 and IPv6
        info = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        if not info:
            return float('inf')
        family, type, proto, canonname, sockaddr = info[0]

        t = time.perf_counter()
        s = socket.create_connection(sockaddr, timeout=PING_TIMEOUT)
        ms = (time.perf_counter() - t) * 1000
        s.close()
        return round(ms, 1)
    except Exception:
        return float('inf')


# ─── CDN-детектор ────────────────────────────────────────────────────────────

# Официальные IP-диапазоны Cloudflare (https://www.cloudflare.com/ips/)
_CF_RANGES = [ipaddress.ip_network(r) for r in [
    '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22',
    '141.101.64.0/18', '108.162.192.0/18', '190.93.240.0/20', '188.114.96.0/20',
    '197.234.240.0/22', '198.41.128.0/17', '162.158.0.0/15',
    '104.16.0.0/13', '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22',
]]


def is_cdn_ip(host: str) -> bool:
    """True если host — IP из CDN-диапазона (Cloudflare и др.)."""
    try:
        ip = ipaddress.ip_address(host)
        return any(ip in net for net in _CF_RANGES)
    except ValueError:
        # Это доменное имя — резолвим
        try:
            resolved = socket.gethostbyname(host)
            ip = ipaddress.ip_address(resolved)
            return any(ip in net for net in _CF_RANGES)
        except Exception:
            return False


# ─── Транспортный парсер ──────────────────────────────────────────────────────

def parse_transport(config: str) -> tuple[str, str, str]:
    """Возвращает (transport, path, sni) из параметров URI."""
    transport, path, sni = 'tcp', '/', ''
    try:
        if '?' in config:
            query = config.split('?', 1)[1].split('#')[0]
            params = {}
            for p in query.split('&'):
                if '=' in p:
                    k, v = p.split('=', 1)
                    params[k.lower()] = urllib.parse.unquote(v)
            transport = params.get('type', 'tcp').lower()
            path = params.get('path', '/') or '/'
            sni = params.get('sni', params.get('host', ''))
    except Exception:
        pass
    return transport, path, sni


# ─── WS-проба ─────────────────────────────────────────────────────────────────

_TLS_PORTS = {443, 8443, 2053, 2083, 2087, 2096}


def ws_probe(host: str, port: int, path: str = '/', sni: str = '') -> bool:
    """
    Отправляет WebSocket Upgrade на конкретный path с правильным SNI.
    101 / 400 / 403 = сервер (или CDN) ответил → маршрутизация работает.
    Нет ответа / обрыв = сервер мёртв или CDN не знает куда роутить → False.
    """
    effective_host = sni or host
    request = (
        f'GET {path} HTTP/1.1\r\n'
        f'Host: {effective_host}\r\n'
        f'Upgrade: websocket\r\n'
        f'Connection: Upgrade\r\n'
        f'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n'
        f'Sec-WebSocket-Version: 13\r\n\r\n'
    ).encode()
    try:
        if port in _TLS_PORTS:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with socket.create_connection((host, port), timeout=PING_TIMEOUT) as raw:
                with ctx.wrap_socket(raw, server_hostname=effective_host) as s:
                    s.sendall(request)
                    return s.recv(16).startswith(b'HTTP/')
        else:
            with socket.create_connection((host, port), timeout=PING_TIMEOUT) as s:
                s.sendall(request)
                return s.recv(16).startswith(b'HTTP/')
    except Exception:
        return False


def fetch_one(url: str) -> list:
    """Скачать конфиги из источника. Поддерживает сырой текст и base64-подписки."""
    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            return []
        text = resp.text.strip()

        # Сначала пробуем как сырой текст
        found = RE_CONFIG_LINK.findall(text)
        if found:
            return found

        # Не нашли — пробуем расшифровать как base64-подписку
        try:
            padded = text + '=' * (-len(text) % 4)
            decoded = base64.b64decode(padded).decode('utf-8', errors='ignore')
            found = RE_CONFIG_LINK.findall(decoded)
            if found:
                return found
        except Exception:
            pass
    except Exception:
        pass
    return []


def _ping_entry(entry):
    """
    Многоуровневая проверка сервера:

    1. TCP-пинг (×2, берём лучший) — обязателен для всех.

    2. WS-проба — ТОЛЬКО для transport=ws/httpupgrade.
       Эти конфиги идут через CDN (Cloudflare), который ВСЕГДА отвечает на
       TCP. Значит TCP-пинг — ложный «живой». Нужно проверить, что CDN
       реально маршрутизирует запрос к бэкенду.
       Ответ 101/400/403 (любой HTTP) = маршрут работает → сервер жив.
       Нет ответа / обрыв = CDN не знает бэкенд → отбрасываем.

    3. Reality/TCP/gRPC — только TCP-пинг. Прямой IP, пинг надёжен.
       WS-проба для них ломалась (они не говорят WebSocket).
    """
    cfg, host, port = entry

    # 1. TCP-пинг
    results = [tcp_ping(host, port) for _ in range(PING_ROUNDS)]
    good = [r for r in results if r < float('inf')]
    if not good:
        return float('inf'), cfg
    best = min(good)
    if best > MAX_PING_MS:
        return float('inf'), cfg

    # 2. WS-проба для CDN-конфигов
    proto = cfg.split('://', 1)[0].lower()
    if proto in ('vless', 'vmess', 'trojan'):
        transport, path, sni = parse_transport(cfg)
        if transport in ('ws', 'httpupgrade'):
            if not ws_probe(host, port, path, sni):
                return float('inf'), cfg

    return best, cfg


def _finish_subscription(country_servers: dict) -> tuple:
    """Собрать b64-подписку и summary из dict {country: [(lat, cfg), ...]}."""
    if len(country_servers) > 1:
        country_servers.pop('XX', None)
    if not country_servers:
        return '', []

    best_lat = {c: min(l for l, _ in v) for c, v in country_servers.items()}
    top = sorted(best_lat, key=best_lat.get)[:MAX_COUNTRIES]

    selected, summary = [], []
    for country in top:
        servers = sorted(country_servers[country])[:SERVERS_PER_COUNTRY]
        selected.extend(cfg for _, cfg in servers)
        summary.append((country, servers[0][0]))

    b64 = base64.b64encode('\n'.join(selected).encode()).decode()
    return b64, summary


def build_best_subscription(sources: list):
    """
    Возвращает (b64, summary).

    Логика:
      1. Если в БД есть ≥15 живых конфигов младше 45 мин — отдаём из кэша
         (ответ за ~1 сек вместо 30–60 сек).
      2. Иначе: полный прогон — качаем источники, пингуем, сохраняем в БД.
    """
    # ── Шаг 1: кэш ──────────────────────────────────────────────────────────
    cached = get_alive_configs(max_age_min=45)
    if len(cached) >= 15:
        country_servers: dict[str, list] = {}
        for row in cached:
            country_servers.setdefault(row['country'], []).append(
                (row['ping_ms'], row['cfg'])
            )
        return _finish_subscription(country_servers)

    # ── Шаг 2: полный сбор ──────────────────────────────────────────────────
    all_configs: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, len(sources))) as ex:
        for batch in ex.map(fetch_one, sources):
            all_configs.extend(batch)

    if not all_configs:
        return '', []

    all_configs = list(dict.fromkeys(all_configs))

    # Парсим, группируем по стране; дедупликация по хосту
    by_country: dict[str, list] = {}
    cfg_meta: dict[str, tuple] = {}   # cfg → (host, port, country, transport)
    seen_hosts: set[str] = set()
    for cfg in all_configs:
        host, port, remark = parse_config(cfg)
        if not host or not port:
            continue
        if host in seen_hosts:
            continue
        seen_hosts.add(host)
        country = extract_country(remark)
        proto = cfg.split('://', 1)[0].lower()
        transport = 'tcp'
        if proto in ('vless', 'vmess', 'trojan'):
            transport, _, _ = parse_transport(cfg)
        cfg_meta[cfg] = (host, port, country, transport)
        by_country.setdefault(country, []).append((cfg, host, port))

    if not by_country:
        return '', []

    # Параллельный пинг
    ping_tasks = [
        entry
        for entries in by_country.values()
        for entry in entries[:MAX_PER_COUNTRY]
    ]
    cfg_lat: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=min(100, len(ping_tasks))) as ex:
        for (cfg, _, __), (lat, _) in zip(ping_tasks, ex.map(_ping_entry, ping_tasks)):
            cfg_lat[cfg] = lat

    # ── Шаг 3: сохраняем все результаты в БД ────────────────────────────────
    db_rows = []
    for cfg, host, port in ping_tasks:
        lat = cfg_lat.get(cfg, float('inf'))
        alive = 1 if 0 < lat <= MAX_PING_MS else 0
        _, _, country, transport = cfg_meta[cfg]
        db_rows.append((
            cfg, host, port, country, transport,
            lat if alive else None,
            alive, ''
        ))
    save_config_results(db_rows)

    # ── Шаг 4: собираем подписку ─────────────────────────────────────────────
    country_servers = {}
    for cfg, host, port in ping_tasks:
        lat = cfg_lat.get(cfg, float('inf'))
        if lat == float('inf') or lat > MAX_PING_MS:
            continue
        country = cfg_meta[cfg][2]
        country_servers.setdefault(country, []).append((lat, cfg))

    return _finish_subscription(country_servers)


def upload_subscription(b64: str) -> str:
    """
    Загружает подписку на хостинг и возвращает прямую ссылку на raw-контент.
    Сервисы протестированы на доступность — нерабочие убраны.
    """
    content = b64.encode()

    # 1. pastefy.app — API возвращает готовый raw_url, проверен
    try:
        r = requests.post(
            'https://pastefy.app/api/v2/paste',
            json={'content': b64, 'type': 'PASTE'},
            timeout=15,
        )
        if r.status_code == 200:
            raw_url = r.json().get('paste', {}).get('raw_url', '')
            if raw_url:
                return raw_url
    except Exception:
        pass

    # 2. catbox.moe — файл-хостинг, прямая ссылка на файл
    try:
        r = requests.post(
            'https://catbox.moe/user/api.php',
            data={'reqtype': 'fileupload', 'userhash': ''},
            files={'fileToUpload': ('sub.txt', content, 'text/plain')},
            timeout=15,
        )
        if r.status_code == 200:
            url = r.text.strip()
            if url.startswith('https://files.catbox.moe/'):
                return url
    except Exception:
        pass

    # 3. 0x0.st — запасной
    try:
        r = requests.post(
            'https://0x0.st',
            files={'file': ('sub.txt', content, 'text/plain')},
            timeout=15,
        )
        if r.status_code == 200:
            url = r.text.strip()
            if url.startswith('http'):
                return url
    except Exception:
        pass

    # 4. transfer.sh — запасной
    try:
        r = requests.put(
            'https://transfer.sh/HitRay.txt',
            data=content,
            headers={'Content-Type': 'text/plain', 'Max-Days': '3'},
            timeout=15,
        )
        if r.status_code == 200:
            url = r.text.strip()
            if url.startswith('http'):
                return url
    except Exception:
        pass

    return ''


# ─── GitHub Discovery ─────────────────────────────────────────────────────────

# Поисковые запросы к GitHub Repositories API
_GH_QUERIES = [
    'vless vmess subscription configs vpn',
    'free vless reality configs subscription',
    'vless subscription txt vpn free',
]

# Типичные имена файлов с конфигами в репозиториях
_KNOWN_PATHS = [
    'sub.txt', 'subscription.txt', 'configs.txt', 'config.txt',
    'vless.txt', 'vmess.txt', 'free.txt', 'proxy.txt', 'vpn.txt',
]


def discover_github_sources(max_results: int = 30) -> list[tuple[str, str, int]]:
    """
    Ищет источники VPN-конфигов на GitHub через Repositories Search API.

    Алгоритм:
      1. Ищем репозитории по ключевым словам.
      2. Для каждого репо получаем список файлов в корне (contents API).
      3. Валидируем .txt-файлы через validate_source().
      4. Сохраняем новые источники в таблицу discovered_sources.

    Возвращает список (raw_url, repo_name, cfg_count) — только новые.
    """
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'HitRay-Bot/1.0',
    }
    if GITHUB_TOKEN:
        headers['Authorization'] = f'Bearer {GITHUB_TOKEN}'

    found_repos: dict[str, str] = {}   # full_name → default_branch

    for query in _GH_QUERIES:
        if len(found_repos) >= 20:
            break
        try:
            r = requests.get(
                'https://api.github.com/search/repositories',
                params={'q': query, 'sort': 'updated', 'order': 'desc', 'per_page': 10},
                headers=headers,
                timeout=10,
            )
            if r.status_code == 403:
                break   # rate limit
            if r.status_code == 200:
                for repo in r.json().get('items', []):
                    name = repo['full_name']
                    branch = repo.get('default_branch', 'main')
                    found_repos[name] = branch
            time.sleep(3)   # GitHub: 10 req/min без токена
        except Exception:
            pass

    results: list[tuple[str, str, int]] = []

    for repo, branch in found_repos.items():
        if len(results) >= max_results:
            break

        # Получаем список файлов в корне репо
        candidate_paths = list(_KNOWN_PATHS)
        try:
            r = requests.get(
                f'https://api.github.com/repos/{repo}/contents/',
                headers=headers,
                timeout=8,
            )
            if r.status_code == 200:
                for item in r.json():
                    if (item.get('type') == 'file'
                            and item['name'].endswith('.txt')
                            and item['name'] not in candidate_paths):
                        candidate_paths.append(item['name'])
        except Exception:
            pass

        # Проверяем каждый кандидат
        for fname in candidate_paths:
            raw_url = f'https://raw.githubusercontent.com/{repo}/{branch}/{fname}'
            count = validate_source(raw_url)
            if count > 0:
                is_new = save_discovered_source(raw_url, repo, count)
                if is_new:
                    results.append((raw_url, repo, count))

        time.sleep(1)

    return results


# ─── Хэндлеры ────────────────────────────────────────────────────────────────

@dp.message(Command('start'))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer(
        "👋 <b>HitRay</b>\n\n"
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
        "👋 <b>HitRay</b>\n\n"
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
    # Показываем разное сообщение в зависимости от наличия кэша
    from database import get_alive_configs
    has_cache = len(get_alive_configs(max_age_min=45)) >= 15
    await cb.message.edit_text(
        "⚡ Загружаю из кэша..." if has_cache
        else "🔄 Собираю конфиги и тестирую серверы...\n<i>(занимает ~30–60 сек)</i>",
        parse_mode="HTML"
    )

    loop = asyncio.get_running_loop()
    b64, summary = await loop.run_in_executor(None, build_best_subscription, sources)

    if not b64:
        await cb.message.edit_text(
            "⚠️ Живых серверов не найдено. Попробуйте позже или добавьте новые источники.",
            reply_markup=kb_back_main()
        )
        return

    lines = [f"• {c} — {lat:.0f} мс" for c, lat in summary]
    summary_text = "\n".join(lines)

    await cb.message.edit_text(
        "✅ <b>Серверы найдены!</b> Загружаю подписку...",
        parse_mode="HTML"
    )

    loop2 = asyncio.get_running_loop()
    url = await loop2.run_in_executor(None, upload_subscription, b64)

    # Сохраняем в историю
    await loop2.run_in_executor(
        None, save_history, len(summary), len(summary) * SERVERS_PER_COUNTRY, url or ''
    )

    header = (
        f"✅ <b>Подписка готова</b> — {len(summary)} стран, "
        f"по {SERVERS_PER_COUNTRY} сервера\n\n"
        f"<b>Страны:</b>\n{summary_text}\n"
    )

    if url:
        # Основное сообщение с кратким описанием
        await cb.message.edit_text(
            header + "\n<b>Subscription URL для Happ ↓</b>\n"
            "<i>(нажми на ссылку ниже, чтобы скопировать)</i>",
            parse_mode="HTML",
            reply_markup=kb_back_main()
        )
        # Отдельное сообщение — только ссылка, plain text, легко копировать
        await cb.message.answer(url, parse_mode=None)
    else:
        # Резерв: отправить файлом если ни один сервис не ответил
        await cb.message.edit_text(
            header + "\n⚠️ Все сервисы недоступны — отправляю файлом.",
            parse_mode="HTML"
        )
        await cb.message.answer_document(
            types.BufferedInputFile(b64.encode(), filename='HitRay_subscription.txt'),
            caption="📱 <b>Happ:</b> нажми <code>+</code> → <i>Импорт из файла</i>",
            parse_mode="HTML",
            reply_markup=kb_back_main()
        )


def _is_admin(user_id: int) -> bool:
    return ADMIN_ID > 0 and user_id == ADMIN_ID


@dp.callback_query(F.data == "discover_add_all")
async def cb_discover_add_all(cb: types.CallbackQuery):
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет прав.", show_alert=True)
        return
    await cb.answer()
    pending = get_discovered_sources(only_new=True)
    added = 0
    for row in pending:
        if add_source(row['url'], row['cfg_count']):
            mark_discovered_added(row['url'])
            added += 1
    await cb.message.edit_text(
        f"✅ Добавлено <b>{added}</b> новых источников.\n"
        f"Всего источников: <b>{sources_count()}</b>",
        parse_mode="HTML",
        reply_markup=kb_back_main()
    )


# ── Статистика ────────────────────────────────────────────────────────────────

@dp.message(Command('stats'))
async def cmd_stats(m: types.Message):
    cnt = sources_count()
    alive = configs_alive_count()
    age = configs_cache_age_minutes()
    rows = last_history(5)

    cache_info = (
        f"🗄 Кэш: <b>{alive}</b> живых конфигов"
        + (f", обновлён <b>{age:.0f} мин</b> назад" if alive else " (пуст)")
    )
    lines = [f"📦 Источников: <b>{cnt}</b>", cache_info, ""]
    if rows:
        lines.append("<b>Последние подписки:</b>")
        for r in rows:
            lines.append(
                f"• {r['created_at']} — {r['country_count']} стран, "
                f"{r['server_count']} серверов"
            )
    else:
        lines.append("<i>История пуста</i>")
    await m.answer('\n'.join(lines), parse_mode="HTML")


@dp.message(Command('cache_reset'))
async def cmd_cache_reset(m: types.Message):
    """Сбросить кэш — следующий запрос выполнит полный прогон."""
    if not _is_admin(m.from_user.id):
        return
    from database import _conn, DB_FILE
    import sqlite3
    with sqlite3.connect(DB_FILE) as con:
        con.execute("UPDATE configs SET alive = 0")
    await m.answer("🗑 Кэш сброшен. Следующая подписка пересоберёт всё заново.")


@dp.message(Command('discover'))
async def cmd_discover(m: types.Message):
    """Найти новые источники на GitHub и показать список."""
    if not _is_admin(m.from_user.id):
        return
    msg = await m.answer("🔍 Ищу источники на GitHub...")
    loop = asyncio.get_running_loop()
    new = await loop.run_in_executor(None, discover_github_sources)

    if not new:
        # Покажем уже найденные ранее
        pending = get_discovered_sources(only_new=True)
        if pending:
            lines = [f"ℹ️ Новых не найдено. Ранее найденные ({len(pending)}):\n"]
            for row in pending[:10]:
                lines.append(f"• <code>{row['url']}</code>\n  {row['repo']} — {row['cfg_count']} конфигов")
            await msg.edit_text('\n'.join(lines), parse_mode="HTML",
                                reply_markup=kb_discover_add())
        else:
            await msg.edit_text("😕 Ничего не найдено. Попробуйте позже.")
        return

    lines = [f"✅ Найдено <b>{len(new)}</b> новых источников:\n"]
    for url, repo, cnt in new[:10]:
        lines.append(f"• <code>{url}</code>\n  {repo} — {cnt} конфигов")
    if len(new) > 10:
        lines.append(f"\n… и ещё {len(new) - 10}")

    await msg.edit_text('\n'.join(lines), parse_mode="HTML",
                        reply_markup=kb_discover_add())


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
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет прав.", show_alert=True)
        return
    h = cb.data[4:]  # 8-символьный хеш URL
    removed = remove_source_by_hash(h)
    if not removed:
        await cb.answer("Источник уже удалён.", show_alert=True)
        return

    short = removed.split('/')[-1][:50] or removed[:50]
    await cb.answer(f"Удалён: {short}", show_alert=True)

    sources = load_sources()
    if not sources:
        await cb.message.edit_text(
            "📋 Источников нет. Добавьте первый!",
            reply_markup=kb_sources_menu()
        )
        return

    markup, start, chunk, total_pages = kb_sources_list(sources, 0)
    lines = [f"{start + i + 1}. <code>{url}</code>" for i, url in enumerate(chunk)]
    await cb.message.edit_text(
        f"📋 <b>Источники</b> (стр. 1/{total_pages})\n\n"
        + "\n".join(lines)
        + "\n\n<i>Нажми на источник чтобы удалить его</i>",
        parse_mode="HTML",
        reply_markup=markup
    )


@dp.callback_query(F.data == "add_source")
async def cb_add_source(cb: types.CallbackQuery, state: FSMContext):
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет прав.", show_alert=True)
        return
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

    if source_exists(url):
        await msg.edit_text(
            "ℹ️ Этот источник уже есть в списке.",
            reply_markup=kb_sources_menu()
        )
        await state.clear()
        return

    add_source(url, cfg_count=count)
    await state.clear()

    await msg.edit_text(
        f"✅ <b>Источник добавлен!</b>\n\n"
        f"<code>{url}</code>\n\n"
        f"Найдено конфигов: <b>{count}</b>\n"
        f"Всего источников: <b>{sources_count()}</b>",
        parse_mode="HTML",
        reply_markup=kb_sources_menu()
    )


# ─── Запуск ───────────────────────────────────────────────────────────────────

async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
