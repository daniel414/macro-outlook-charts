"""Shared helpers: paths, SQLite storage, HTTP session, BTC keyword filter."""
import json, os, re, sqlite3, time, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("BTCSENT_DB", ROOT / "data" / "sentiment.db"))
OUT_DIR = ROOT / "out"
CONFIG = json.loads((ROOT / "config.json").read_text())

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/129.0 Safari/537.36")

# Regex used to decide whether a post in a general-crypto venue is about BTC.
BTC_RE = re.compile(r"(?i)(\bbtc\b|\$btc|bitcoin|\bsats?\b|satoshi|\bxbt\b|₿|bitcorn|\bbtcusd)")
URL_RE = re.compile(r"https?://\S+")
WS_RE = re.compile(r"\s+")

log = logging.getLogger("btcsent")


def setup_logging(level=logging.INFO):
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger("scrapling").setLevel(logging.WARNING)


def is_btc(text: str) -> bool:
    return bool(text and BTC_RE.search(text))


def clean_text(text: str) -> str:
    text = URL_RE.sub(" ", text or "")
    return WS_RE.sub(" ", text).strip()


SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    source      TEXT NOT NULL,       -- reddit / 4chan / bitcointalk / telegram / farcaster / x
    post_id     TEXT NOT NULL,       -- id unique within source
    created_utc INTEGER NOT NULL,    -- unix seconds, UTC
    venue       TEXT,                -- subreddit / board / channel / query
    kind        TEXT,                -- post / comment / reply / cast / message
    author      TEXT,
    text        TEXT NOT NULL,
    url         TEXT,
    inserted_utc INTEGER NOT NULL,
    p_neg REAL, p_neu REAL, p_pos REAL,   -- model probabilities (NULL = not scored yet)
    model TEXT,
    filter_version INTEGER,               -- version of btcsent/filters.py rules that admitted this post
    nchunks INTEGER,                      -- number of 120-token chunks averaged by the full-text scorer
    PRIMARY KEY (source, post_id)
);
CREATE INDEX IF NOT EXISTS ix_posts_time ON posts(created_utc);
CREATE INDEX IF NOT EXISTS ix_posts_unscored ON posts(model) WHERE model IS NULL;
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, started_utc INTEGER,
    finished_utc INTEGER, inserted INTEGER, status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS klines (
    symbol TEXT, interval TEXT, open_time INTEGER, open REAL, high REAL, low REAL,
    close REAL, volume REAL, exchange TEXT,
    PRIMARY KEY (symbol, interval, open_time));
"""


def connect(path=DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=120)
    con.execute("PRAGMA busy_timeout=120000")
    for i in range(30):   # several collectors may start at once
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            cols = {r[1] for r in con.execute("PRAGMA table_info(posts)")}
            for c in ("filter_version INTEGER", "nchunks INTEGER"):
                if c.split()[0] not in cols:
                    con.execute(f"ALTER TABLE posts ADD COLUMN {c}")
            break
        except sqlite3.OperationalError:
            time.sleep(1 + i)
    return con


def get_state(con, key, default=None):
    row = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_state(con, key, value):
    con.execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)))
    con.commit()


def insert_posts(con, rows) -> int:
    """rows: iterable of dicts with source, post_id, created_utc, venue, kind, author, text, url.
    Clean-data pipeline: every post is archived in data/raw.db with its filter decision and only posts that
    pass btcsent/filters.py (venue selection + quality filters) reach the production `posts` table.
    Returns the number of NEW raw posts (kept or not), so collectors' paging/progress logic is unchanged."""
    from .filters import ingest
    return ingest(con, list(rows))


def http_session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def get_with_retry(session, url, params=None, tries=5, backoff=3.0, timeout=60, ok_json_error_key=None):
    """GET with retry on network errors / 429 / 5xx. Returns requests.Response or raises."""
    last = None
    for i in range(tries):
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"HTTP {r.status_code} for {r.url}")
                time.sleep(backoff * (i + 1))
                continue
            return r
        except Exception as e:  # network error
            last = e
            time.sleep(backoff * (i + 1))
    raise last
