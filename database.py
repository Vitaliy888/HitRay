"""
SQLite-база для HitRay.

Таблицы:
  sources            — URL-источники конфигов (добавляются вручную)
  history            — история генерации подписок
  configs            — кэш проверенных конфигов (alive/dead + пинг)
  discovered_sources — источники, найденные автоматически на GitHub
"""

import hashlib
import json
import os
import sqlite3

DB_FILE = os.path.join(os.path.dirname(__file__), 'hitray.db')
SOURCES_FILE = os.path.join(os.path.dirname(__file__), 'sources.json')


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    """Создаёт таблицы и мигрирует данные из sources.json."""
    with _conn() as con:
        con.executescript('''
            CREATE TABLE IF NOT EXISTS sources (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT    UNIQUE NOT NULL,
                cfg_count   INTEGER DEFAULT 0,
                added_at    TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS history (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at    TEXT    DEFAULT (datetime('now','localtime')),
                country_count INTEGER,
                server_count  INTEGER,
                sub_url       TEXT
            );

            -- Кэш проверенных конфигов.
            -- Пополняется после каждого полного прогона.
            CREATE TABLE IF NOT EXISTS configs (
                cfg        TEXT    PRIMARY KEY,
                host       TEXT    NOT NULL,
                port       INTEGER NOT NULL,
                country    TEXT    DEFAULT 'XX',
                transport  TEXT    DEFAULT 'tcp',
                ping_ms    REAL,                       -- NULL если мёртв
                alive      INTEGER DEFAULT 0,          -- 1 = жив
                checked_at TEXT    DEFAULT (datetime('now','localtime')),
                source_url TEXT    DEFAULT ''
            );

            -- Источники, найденные автоматически (GitHub Discovery).
            -- Не добавляются в sources автоматически — требуют одобрения.
            CREATE TABLE IF NOT EXISTS discovered_sources (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                url          TEXT    UNIQUE NOT NULL,
                repo         TEXT    DEFAULT '',
                cfg_count    INTEGER DEFAULT 0,
                discovered_at TEXT   DEFAULT (datetime('now','localtime')),
                added        INTEGER DEFAULT 0   -- 1 = уже добавлен в sources
            );
        ''')

    # Однократная миграция из sources.json → SQLite
    if os.path.exists(SOURCES_FILE):
        try:
            with open(SOURCES_FILE, encoding='utf-8') as f:
                urls: list = json.load(f)
            with _conn() as con:
                for url in urls:
                    con.execute('INSERT OR IGNORE INTO sources (url) VALUES (?)', (url,))
            os.rename(SOURCES_FILE, SOURCES_FILE + '.migrated')
        except Exception:
            pass


# ─── Источники ────────────────────────────────────────────────────────────────

def url_hash(url: str) -> str:
    """8-символьный hex-хеш URL — используется в callback_data кнопок."""
    return hashlib.md5(url.encode()).hexdigest()[:8]


def load_sources() -> list[str]:
    try:
        with _conn() as con:
            rows = con.execute('SELECT url FROM sources ORDER BY id').fetchall()
            return [r['url'] for r in rows]
    except Exception:
        return []


def add_source(url: str, cfg_count: int = 0) -> bool:
    """Добавить источник. Возвращает True если добавлен, False если уже есть."""
    try:
        with _conn() as con:
            con.execute('INSERT INTO sources (url, cfg_count) VALUES (?, ?)', (url, cfg_count))
        return True
    except sqlite3.IntegrityError:
        return False


def remove_source_by_hash(h: str) -> str:
    """Удалить источник по хешу. Возвращает удалённый URL или ''."""
    for url in load_sources():
        if url_hash(url) == h:
            with _conn() as con:
                con.execute('DELETE FROM sources WHERE url = ?', (url,))
            return url
    return ''


def source_exists(url: str) -> bool:
    with _conn() as con:
        return con.execute('SELECT 1 FROM sources WHERE url = ?', (url,)).fetchone() is not None


def sources_count() -> int:
    with _conn() as con:
        return con.execute('SELECT COUNT(*) FROM sources').fetchone()[0]


# ─── Кэш конфигов ─────────────────────────────────────────────────────────────

def get_alive_configs(max_age_min: int = 45) -> list[sqlite3.Row]:
    """Живые конфиги, проверенные не позднее max_age_min минут назад."""
    with _conn() as con:
        return con.execute(
            "SELECT cfg, country, ping_ms FROM configs "
            "WHERE alive = 1 "
            "  AND checked_at > datetime('now', ?, 'localtime') "
            "ORDER BY ping_ms ASC",
            (f'-{max_age_min} minutes',)
        ).fetchall()


def save_config_results(rows) -> None:
    """
    Сохранить результаты проверки пачкой.
    rows: iterable of (cfg, host, port, country, transport, ping_ms_or_None, alive, source_url)
    """
    with _conn() as con:
        con.executemany(
            "INSERT OR REPLACE INTO configs "
            "(cfg, host, port, country, transport, ping_ms, alive, checked_at, source_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'), ?)",
            rows
        )


def configs_alive_count() -> int:
    with _conn() as con:
        return con.execute('SELECT COUNT(*) FROM configs WHERE alive = 1').fetchone()[0]


def configs_cache_age_minutes() -> float:
    """Сколько минут назад был последний успешный прогон (0 если нет данных)."""
    with _conn() as con:
        row = con.execute(
            "SELECT (julianday('now','localtime') - julianday(MAX(checked_at))) * 1440 "
            "FROM configs WHERE alive = 1"
        ).fetchone()
        val = row[0]
        return round(val, 1) if val is not None else 0.0


# ─── История ─────────────────────────────────────────────────────────────────

def save_history(country_count: int, server_count: int, sub_url: str) -> None:
    with _conn() as con:
        con.execute(
            'INSERT INTO history (country_count, server_count, sub_url) VALUES (?, ?, ?)',
            (country_count, server_count, sub_url)
        )


def last_history(n: int = 5) -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute(
            'SELECT created_at, country_count, server_count, sub_url '
            'FROM history ORDER BY id DESC LIMIT ?', (n,)
        ).fetchall()


# ─── GitHub Discovery ─────────────────────────────────────────────────────────

def save_discovered_source(url: str, repo: str, cfg_count: int) -> bool:
    """Сохранить найденный источник. True = новый, False = уже был."""
    try:
        with _conn() as con:
            con.execute(
                'INSERT INTO discovered_sources (url, repo, cfg_count) VALUES (?, ?, ?)',
                (url, repo, cfg_count)
            )
        return True
    except sqlite3.IntegrityError:
        return False


def get_discovered_sources(only_new: bool = True) -> list[sqlite3.Row]:
    """Найденные источники. only_new=True — только ещё не добавленные в sources."""
    with _conn() as con:
        if only_new:
            return con.execute(
                'SELECT url, repo, cfg_count FROM discovered_sources '
                'WHERE added = 0 ORDER BY cfg_count DESC'
            ).fetchall()
        return con.execute(
            'SELECT url, repo, cfg_count, added FROM discovered_sources ORDER BY id DESC'
        ).fetchall()


def mark_discovered_added(url: str) -> None:
    with _conn() as con:
        con.execute('UPDATE discovered_sources SET added = 1 WHERE url = ?', (url,))
