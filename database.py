"""
SQLite-база для HitRay.

Таблицы:
  sources  — URL-источники конфигов
  history  — история генерации подписок
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
        ''')

    # Однократная миграция из sources.json → SQLite
    if os.path.exists(SOURCES_FILE):
        try:
            with open(SOURCES_FILE, encoding='utf-8') as f:
                urls: list = json.load(f)
            with _conn() as con:
                for url in urls:
                    con.execute(
                        'INSERT OR IGNORE INTO sources (url) VALUES (?)', (url,)
                    )
            # Переименовываем, чтобы не мигрировать повторно
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
            con.execute(
                'INSERT INTO sources (url, cfg_count) VALUES (?, ?)',
                (url, cfg_count)
            )
        return True
    except sqlite3.IntegrityError:
        return False


def remove_source_by_hash(h: str) -> str:
    """Удалить источник по хешу. Возвращает удалённый URL или ''."""
    sources = load_sources()
    for url in sources:
        if url_hash(url) == h:
            with _conn() as con:
                con.execute('DELETE FROM sources WHERE url = ?', (url,))
            return url
    return ''


def source_exists(url: str) -> bool:
    with _conn() as con:
        row = con.execute(
            'SELECT 1 FROM sources WHERE url = ?', (url,)
        ).fetchone()
        return row is not None


def sources_count() -> int:
    with _conn() as con:
        return con.execute('SELECT COUNT(*) FROM sources').fetchone()[0]


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
