import os
import sqlite3
import threading
import time


class Database:
    def __init__(self, data_dir):
        os.makedirs(data_dir, exist_ok=True)
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
            """)
            self.conn.commit()
        self._migrate_legacy_database(data_dir)

    def _migrate_legacy_database(self, data_dir):
        legacy_path = os.path.join(data_dir, "chat.db")
        if not os.path.exists(legacy_path) or os.path.abspath(legacy_path) == os.path.abspath(self.path):
            return
        try:
            old = sqlite3.connect(legacy_path)
            old.row_factory = sqlite3.Row
            rows = old.execute(
                "SELECT chat_id, role, text FROM messages ORDER BY id"
            ).fetchall()
            old.close()
            with self.lock:
                existing = self.conn.execute("SELECT COUNT(*) n FROM messages").fetchone()["n"]
                if existing == 0 and rows:
                    import time
                    now = int(time.time())
                    self.conn.executemany(
                        "INSERT INTO messages(user_id,role,content,mode,created_at) VALUES(?,?,?,?,?)",
                        [(r["chat_id"], r["role"], r["text"], "chat", now) for r in rows]
                    )
                    self.conn.commit()
        except Exception:
            # Keep the legacy database untouched; migration failure must not stop startup.
            pass

    def execute(self, sql, params=()):
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def save_message(self, user_id, role, content, mode):
        self.execute(
            "INSERT INTO messages(user_id,role,content,mode,created_at) VALUES(?,?,?,?,?)",
            (user_id, role, content, mode, int(time.time()))
        )

    def history(self, user_id, limit):
        with self.lock:
            rows = self.conn.execute(
                "SELECT role,content,mode FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
        return list(reversed(rows))

    def clear_history(self, user_id):
        self.execute("DELETE FROM messages WHERE user_id=?", (user_id,))

    def add_memory(self, user_id, content):
        cur = self.execute(
            "INSERT INTO memories(user_id,content,created_at) VALUES(?,?,?)",
            (user_id, content[:1500], int(time.time()))
        )
        return cur.lastrowid

    def memories(self, user_id):
        with self.lock:
            return self.conn.execute(
                "SELECT id,content FROM memories WHERE user_id=? ORDER BY id DESC LIMIT 30",
                (user_id,)
            ).fetchall()

    def forget(self, user_id, memory_id):
        return self.execute(
            "DELETE FROM memories WHERE user_id=? AND id=?", (user_id, memory_id)
        ).rowcount

    def get_setting(self, user_id, key, default=""):
        with self.lock:
            row = self.conn.execute(
                "SELECT value FROM settings WHERE user_id=? AND key=?", (user_id,key)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, user_id, key, value):
        self.execute(
            "INSERT INTO settings(user_id,key,value) VALUES(?,?,?) "
            "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
            (user_id,key,str(value))
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
            (key,value,int(time.time()))
        )

    def record_usage(self, user_id, model, input_chars, output_chars):
        self.execute(
            "INSERT INTO usage(user_id,model,input_chars,output_chars,created_at) VALUES(?,?,?,?,?)",
            (user_id,model,input_chars,output_chars,int(time.time()))
        )

    def usage_summary(self, user_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(input_chars),0) i, "
                "COALESCE(SUM(output_chars),0) o FROM usage WHERE user_id=?",
                (user_id,)
            ).fetchone()
        return row

    def message_count(self, user_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) n FROM messages WHERE user_id=?", (user_id,)
            ).fetchone()
        return row["n"]
