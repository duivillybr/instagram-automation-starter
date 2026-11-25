import contextlib
import os
import sqlite3
from datetime import datetime, timezone


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                title_key TEXT NOT NULL UNIQUE, source TEXT NOT NULL, url TEXT NOT NULL,
                title TEXT NOT NULL, summary TEXT NOT NULL, published_at TEXT NOT NULL,
                collected_at TEXT NOT NULL, score INTEGER NOT NULL, topic TEXT NOT NULL,
                reason TEXT NOT NULL, status TEXT NOT NULL,
                headline TEXT, body TEXT, caption TEXT, image_path TEXT,
                image_url TEXT, container_id TEXT, media_id TEXT,
                source_image_url TEXT, video_path TEXT, audio_path TEXT, media_kind TEXT,
                publish_started_at TEXT, posted_at TEXT, error TEXT
            );
            CREATE INDEX IF NOT EXISTS articles_status ON articles(status, score DESC);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(articles)")}
        if "kind" not in columns:
            with self.conn:
                self.conn.execute("ALTER TABLE articles ADD COLUMN kind TEXT NOT NULL DEFAULT 'news'")
        for name, declaration in (
            ("source_image_url", "TEXT"), ("video_path", "TEXT"),
            ("audio_path", "TEXT"), ("media_kind", "TEXT NOT NULL DEFAULT 'image'"),
        ):
            if name not in columns:
                with self.conn:
                    self.conn.execute(f"ALTER TABLE articles ADD COLUMN {name} {declaration}")

    def close(self):
        self.conn.close()

    @contextlib.contextmanager
    def lock(self):
        """OS lock: liberado também após crash; comandos e worker usam o mesmo lock."""
        path = self.path.with_suffix(".lock")
        with path.open("a+b") as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError("Outro comando está usando a fila. Tente novamente em instantes.") from None
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def update(self, article_id, **fields):
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(articles)")}
        if not fields or not fields.keys() <= columns - {"id"}:
            raise ValueError("Campos inválidos")
        with self.conn:
            self.conn.execute(
                "UPDATE articles SET " + ", ".join(f"{key}=?" for key in fields) + " WHERE id=?",
                [*fields.values(), article_id],
            )

    def get(self, article_id):
        row = self.conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
        if row is None:
            raise ValueError(f"Content item {article_id} was not found")
        return dict(row)

    def rows(self, query, args=()):
        return [dict(row) for row in self.conn.execute(query, args).fetchall()]

    def set_meta(self, key, value):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (key, value))

    def get_meta(self, key):
        row = self.conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else None
