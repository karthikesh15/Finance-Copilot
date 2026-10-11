import os
import sqlite3

DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    if DATABASE_URL:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn = sqlite3.connect("finance.db")
    conn.row_factory = sqlite3.Row
    return conn


def _exec(query, params=(), fetch=None):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(query.replace("?", "%s") if DATABASE_URL else query, params)
        result = None
        if fetch == "one":
            result = cur.fetchone()
        elif fetch == "all":
            result = cur.fetchall()
        conn.commit()
        cur.close()
        return result
    finally:
        conn.close()


def init_db():
    if DATABASE_URL:
        _exec('''
            CREATE TABLE IF NOT EXISTS transactions (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                type TEXT CHECK (type IN ('inflow', 'outflow')),
                amount NUMERIC NOT NULL,
                category TEXT,
                subcategory TEXT,
                description TEXT,
                source TEXT CHECK (source IN ('text', 'photo', 'voice', 'button')),
                timestamp TIMESTAMPTZ DEFAULT NOW()
            )
        ''')
        _exec('''
            CREATE TABLE IF NOT EXISTS users (
                chat_id BIGINT PRIMARY KEY,
                language TEXT,
                username TEXT UNIQUE,
                password_hash TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')
    else:
        _exec('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT CHECK (type IN ('inflow', 'outflow')),
                amount REAL NOT NULL,
                category TEXT,
                subcategory TEXT,
                description TEXT,
                source TEXT CHECK (source IN ('text', 'photo', 'voice', 'button')),
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        _exec('''
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                language TEXT,
                username TEXT UNIQUE,
                password_hash TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')


# ---------- users ----------

def get_user(chat_id):
    row = _exec("SELECT * FROM users WHERE chat_id = ?", (chat_id,), "one")
    return dict(row) if row else None


def get_user_by_username(username):
    row = _exec("SELECT * FROM users WHERE username = ?", (username,), "one")
    return dict(row) if row else None


def set_language(chat_id, language):
    _exec('''
        INSERT INTO users (chat_id, language) VALUES (?, ?)
        ON CONFLICT (chat_id) DO UPDATE SET language = EXCLUDED.language
    ''', (chat_id, language))


def set_credentials(chat_id, username, password_hash):
    _exec('''
        INSERT INTO users (chat_id, username, password_hash) VALUES (?, ?, ?)
        ON CONFLICT (chat_id) DO UPDATE
        SET username = EXCLUDED.username, password_hash = EXCLUDED.password_hash
    ''', (chat_id, username, password_hash))


# ---------- transactions ----------

def add_transaction(user_id, tx_type, amount, category,
                    subcategory=None, description=None, source="text"):
    _exec('''
        INSERT INTO transactions (user_id, type, amount, category, subcategory, description, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, tx_type, amount, category, subcategory, description, source))


def get_cash_summary(user_id):
    row = _exec('''
        SELECT
            COALESCE(SUM(CASE WHEN type='inflow' THEN amount ELSE 0 END), 0) AS total_in,
            COALESCE(SUM(CASE WHEN type='outflow' THEN amount ELSE 0 END), 0) AS total_out
        FROM transactions WHERE user_id = ?
    ''', (user_id,), "one")
    inflow = float(row["total_in"]) if row else 0.0
    outflow = float(row["total_out"]) if row else 0.0
    return inflow, outflow, (inflow - outflow)


def get_category_breakdown(user_id):
    rows = _exec('''
        SELECT category, type, SUM(amount) AS total
        FROM transactions WHERE user_id = ?
        GROUP BY category, type ORDER BY total DESC
    ''', (user_id,), "all")
    return [(r["category"], r["type"], float(r["total"])) for r in rows]


def get_source_breakdown(user_id):
    rows = _exec('''
        SELECT source, COUNT(*) AS count
        FROM transactions WHERE user_id = ?
        GROUP BY source ORDER BY count DESC
    ''', (user_id,), "all")
    return [(r["source"], r["count"]) for r in rows]


def get_all_transactions(user_id, limit=200):
    rows = _exec('''
        SELECT * FROM transactions WHERE user_id = ?
        ORDER BY timestamp DESC LIMIT ?
    ''', (user_id, limit), "all")
    result = []
    for r in rows:
        row = dict(r)
        row["amount"] = float(row["amount"])
        row["timestamp"] = str(row["timestamp"])
        result.append(row)
    return result
