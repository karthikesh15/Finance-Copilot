import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('''
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
    conn.commit()
    cursor.close()
    conn.close()


def add_transaction(user_id, tx_type, amount, category,
                     subcategory=None, description=None, source="text"):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO transactions (user_id, type, amount, category, subcategory, description, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    ''', (user_id, tx_type, amount, category, subcategory, description, source))
    conn.commit()
    cursor.close()
    conn.close()


def get_cash_summary(user_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT
            COALESCE(SUM(CASE WHEN type='inflow' THEN amount ELSE 0 END), 0) as total_in,
            COALESCE(SUM(CASE WHEN type='outflow' THEN amount ELSE 0 END), 0) as total_out
        FROM transactions WHERE user_id = %s
    ''', (user_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    inflow = float(row["total_in"])
    outflow = float(row["total_out"])
    return inflow, outflow, (inflow - outflow)


def get_category_breakdown(user_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT category, type, SUM(amount) as total
        FROM transactions
        WHERE user_id = %s
        GROUP BY category, type
        ORDER BY total DESC
    ''', (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [(r["category"], r["type"], float(r["total"])) for r in rows]


def get_source_breakdown(user_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT source, COUNT(*) as count
        FROM transactions
        WHERE user_id = %s
        GROUP BY source
        ORDER BY count DESC
    ''', (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [(r["source"], r["count"]) for r in rows]


def get_all_transactions(user_id, limit=200):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM transactions
        WHERE user_id = %s
        ORDER BY timestamp DESC
        LIMIT %s
    ''', (user_id, limit))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    result = []
    for r in rows:
        row = dict(r)
        row["amount"] = float(row["amount"])
        row["timestamp"] = row["timestamp"].isoformat()
        result.append(row)
    return result
