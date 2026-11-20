import logging
import os
import sqlite3
import threading
import time

log = logging.getLogger(__name__)


class Database:
    def __init__(self, data_dir):
        try:
            os.makedirs(data_dir, exist_ok=True)
        except OSError:
            # e.g. "/data" is not writable when running locally
            fallback = os.path.join(os.getcwd(), "data")
            log.warning("Cannot use DATA_DIR=%s; falling back to %s", data_dir, fallback)
            data_dir = fallback
            os.makedirs(data_dir, exist_ok=True)
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "assistant.db")
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'chat',
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id,id);
            CREATE TABLE IF NOT EXISTS memories(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, content TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings(
                user_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY(user_id,key)
            );
            CREATE TABLE IF NOT EXISTS translation_cache(
                cache_key TEXT PRIMARY KEY, translated TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage(
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                model TEXT NOT NULL, input_chars INTEGER NOT NULL,
                output_chars INTEGER NOT NULL, created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta(
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pending(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                text TEXT NOT NULL, mode TEXT NOT NULL,
                created_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
            );
            """)
            self.conn.commit()
        self._migrate_legacy_database(self.data_dir)

    # ---------------------------------------------------------------- meta
    def get_meta(self, key, default=""):
        with self.lock:
            row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key, value):
        self.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value))
        )

    def _migrate_legacy_database(self, data_dir):
        # Runs at most once: the flag stops old history from coming back after /reset.
        if self.get_meta("legacy_migrated") == "1":
            return
        legacy_path = os.path.join(data_dir, "chat.db")
        if not os.path.exists(legacy_path) or os.path.abspath(legacy_path) == os.path.abspath(self.path):
            self.set_meta("legacy_migrated", "1")
            return
        try:
            old = sqlite3.connect(legacy_path)
            old.row_factory = sqlite3.Row
            rows = old.execute("SELECT chat_id, role, text FROM messages ORDER BY id").fetchall()
            old.close()
            with self.lock:
                existing = self.conn.execute("SELECT COUNT(*) n FROM messages").fetchone()["n"]
                if existing == 0 and rows:
                    now = int(time.time())
                    self.conn.executemany(
                        "INSERT INTO messages(user_id,role,content,mode,created_at) VALUES(?,?,?,?,?)",
                        [(r["chat_id"], r["role"], r["text"], "chat", now) for r in rows],
                    )
                    self.conn.commit()
            self.set_meta("legacy_migrated", "1")
        except Exception:
            # Keep the legacy database untouched; migration failure must not stop startup.
            log.exception("Legacy migration failed; will retry on next start")

    # ------------------------------------------------------------ basics
    def execute(self, sql, params=()):
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def save_message(self, user_id, role, content, mode):
        self.execute(
            "INSERT INTO messages(user_id,role,content,mode,created_at) VALUES(?,?,?,?,?)",
            (user_id, role, content, mode, int(time.time())),
        )

    def save_exchange(self, user_id, user_text, model_text, mode, keep=500):
        """Store a finished turn atomically and prune old rows."""
        now = int(time.time())
        with self.lock:
            self.conn.execute(
                "INSERT INTO messages(user_id,role,content,mode,created_at) VALUES(?,?,?,?,?)",
                (user_id, "user", user_text, mode, now))
            self.conn.execute(
                "INSERT INTO messages(user_id,role,content,mode,created_at) VALUES(?,?,?,?,?)",
                (user_id, "model", model_text, mode, now))
            if keep and keep > 0:
                self.conn.execute(
                    "DELETE FROM messages WHERE user_id=? AND id NOT IN "
                    "(SELECT id FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?)",
                    (user_id, user_id, keep))
            self.conn.commit()

    def history(self, user_id, limit):
        with self.lock:
            rows = self.conn.execute(
                "SELECT role,content,mode FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return list(reversed(rows))

    def clear_history(self, user_id):
        self.execute("DELETE FROM messages WHERE user_id=?", (user_id,))

    def add_memory(self, user_id, content):
        cur = self.execute(
            "INSERT INTO memories(user_id,content,created_at) VALUES(?,?,?)",
            (user_id, content[:1500], int(time.time())),
        )
        return cur.lastrowid

    def memories(self, user_id, limit=30):
        with self.lock:
            return self.conn.execute(
                "SELECT id,content FROM memories WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()

    def forget(self, user_id, memory_id):
        return self.execute(
            "DELETE FROM memories WHERE user_id=? AND id=?", (user_id, memory_id)
        ).rowcount

    def get_setting(self, user_id, key, default=""):
        with self.lock:
            row = self.conn.execute(
                "SELECT value FROM settings WHERE user_id=? AND key=?", (user_id, key)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, user_id, key, value):
        self.execute(
            "INSERT INTO settings(user_id,key,value) VALUES(?,?,?) "
            "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
            (user_id, key, str(value)),
        )

    def get_cached_translation(self, key):
        with self.lock:
            row = self.conn.execute(
                "SELECT translated FROM translation_cache WHERE cache_key=?", (key,)
            ).fetchone()
        return row["translated"] if row else None

    def cache_translation(self, key, value):
        self.execute(
            "INSERT OR REPLACE INTO translation_cache(cache_key,translated,created_at) VALUES(?,?,?)",
            (key, value, int(time.time())),
        )

    def record_usage(self, user_id, model, input_chars, output_chars):
        self.execute(
            "INSERT INTO usage(user_id,model,input_chars,output_chars,created_at) VALUES(?,?,?,?,?)",
            (user_id, model, input_chars, output_chars, int(time.time())),
        )

    def usage_summary(self, user_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(input_chars),0) i, "
                "COALESCE(SUM(output_chars),0) o FROM usage WHERE user_id=?",
                (user_id,),
            ).fetchone()
        return row

    def message_count(self, user_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) n FROM messages WHERE user_id=?", (user_id,)
            ).fetchone()
        return row["n"]

    # ------------------------------------------------- pending (retry queue)
    def enqueue_pending(self, user_id, chat_id, text, mode):
        cur = self.execute(
            "INSERT INTO pending(user_id,chat_id,text,mode,created_at) VALUES(?,?,?,?,?)",
            (user_id, chat_id, text, mode, int(time.time())),
        )
        return cur.lastrowid

    def next_pending(self):
        with self.lock:
            return self.conn.execute("SELECT * FROM pending ORDER BY id LIMIT 1").fetchone()

    def list_pending(self, user_id):
        with self.lock:
            return self.conn.execute(
                "SELECT id,text,mode,created_at FROM pending WHERE user_id=? ORDER BY id",
                (user_id,),
            ).fetchall()

    def bump_pending(self, pending_id):
        self.execute("UPDATE pending SET attempts=attempts+1 WHERE id=?", (pending_id,))

    def delete_pending(self, pending_id):
        self.execute("DELETE FROM pending WHERE id=?", (pending_id,))

    def clear_pending(self, user_id):
        return self.execute("DELETE FROM pending WHERE user_id=?", (user_id,)).rowcount

    def purge_expired_pending(self, ttl_seconds):
        cutoff = int(time.time()) - ttl_seconds
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM pending WHERE created_at<?", (cutoff,)
            ).fetchall()
            if rows:
                self.conn.execute("DELETE FROM pending WHERE created_at<?", (cutoff,))
                self.conn.commit()
        return rows

    def pending_count(self, user_id=None):
        with self.lock:
            if user_id is None:
                row = self.conn.execute("SELECT COUNT(*) n FROM pending").fetchone()
            else:
                row = self.conn.execute(
                    "SELECT COUNT(*) n FROM pending WHERE user_id=?", (user_id,)).fetchone()
        return row["n"]
