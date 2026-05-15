"""
База данных — SQLite через стандартный sqlite3
"""

import sqlite3
import logging
from datetime import datetime

log = logging.getLogger(__name__)


class Database:
    def __init__(self, path: str):
        self.path = path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Инициализация схемы ───────────────────────────────────────────────────

    def init(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    tg_id       INTEGER PRIMARY KEY,
                    phone       TEXT    UNIQUE NOT NULL,
                    username    TEXT    DEFAULT '',
                    created_at  TEXT    DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS submissions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id       INTEGER NOT NULL,
                    photo_hash  TEXT    NOT NULL,
                    promo_code  TEXT    UNIQUE NOT NULL,
                    created_at  TEXT    DEFAULT (datetime('now')),
                    FOREIGN KEY(tg_id) REFERENCES users(tg_id)
                );

                CREATE INDEX IF NOT EXISTS idx_submissions_hash
                    ON submissions(photo_hash);
            """)
        log.info(f"БД инициализирована: {self.path}")

    # ── Пользователи ─────────────────────────────────────────────────────────

    def is_verified(self, tg_id: int) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE tg_id = ?", (tg_id,)
            ).fetchone()
            return row is not None

    def phone_exists(self, phone: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE phone = ?", (phone,)
            ).fetchone()
            return row is not None

    def register_user(self, tg_id: int, phone: str, username: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (tg_id, phone, username) VALUES (?, ?, ?)",
                (tg_id, phone, username),
            )

    # ── Фото и промокоды ─────────────────────────────────────────────────────

    def get_all_hashes(self) -> list[str]:
        """Все хэши фото для антиплагиата"""
        with self._conn() as conn:
            rows = conn.execute("SELECT photo_hash FROM submissions").fetchall()
            return [r["photo_hash"] for r in rows]

    def save_submission(self, tg_id: int, photo_hash: str, promo_code: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO submissions (tg_id, photo_hash, promo_code) VALUES (?, ?, ?)",
                (tg_id, photo_hash, promo_code),
            )

    def get_user_promo_count(self, tg_id: int) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM submissions WHERE tg_id = ?", (tg_id,)
            ).fetchone()
            return row["cnt"]

    def get_user_promos(self, tg_id: int) -> list[tuple]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT promo_code, created_at FROM submissions WHERE tg_id = ? ORDER BY created_at DESC",
                (tg_id,),
            ).fetchall()
            return [(r["promo_code"], r["created_at"]) for r in rows]

    # ── Статистика ────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        with self._conn() as conn:
            users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
            subs  = conn.execute("SELECT COUNT(*) as c FROM submissions").fetchone()["c"]
            return {
                "users":       users,
                "submissions": subs,
                "promos":      subs,
            }
