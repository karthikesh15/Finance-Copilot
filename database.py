import os
import sqlite3

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    if DATABASE_URL:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    else:
        conn = sqlite3.connect("finance.db")
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id BIGINT NOT NULL,
            type TEXT CHECK (type IN ('inflow', 'outflow')),
            amount NUMERIC NOT NULL,
            category TEXT,
            subcategory TEXT,
            description TEXT,
            source TEXT CHECK (source IN ('text', 'photo', 'voice', 'button')),
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    cursor.close()
    conn.close()

def add_transaction(user_id, tx_type, amount, category,
                    subcategory=None, description=None, source="text"):
    conn = get_conn()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    query = f'''
        INSERT INTO transactions (user_id, type, amount, category, subcategory, description, source)
        VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
    '''
    cursor.execute(query, (user_id, tx_type, amount, category, subcategory, description, source))
    conn.commit()
    cursor.close()
    conn.close()

def get_cash_summary(user_id):
    conn = get_conn()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    query = f'''
        SELECT
            COALESCE(SUM(CASE WHEN type='inflow' THEN amount ELSE 0 END), 0) as total_in,
            COALESCE(SUM(CASE WHEN type='outflow' THEN amount ELSE 0 END), 0) as total_out
        FROM transactions WHERE user_id = {placeholder}
    '''
    cursor.execute(query, (user_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    inflow = float(row["total_in"]) if row else 0.0
    outflow = float(row["total_out"]) if row else 0.0
    return inflow, outflow, (inflow - outflow)

def get_category_breakdown(user_id):
    conn = get_conn()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    query = f'''
        SELECT category, type, SUM(amount) as total
        FROM transactions
        WHERE user_id = {placeholder}
        GROUP BY category, type
        ORDER BY total DESC
    '''
    cursor.execute(query, (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [(r["category"], r["type"], float(r["total"])) for r in rows]

def get_source_breakdown(user_id):
    conn = get_conn()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    query = f'''
        SELECT source, COUNT(*) as count
        FROM transactions
        WHERE user_id = {placeholder}
        GROUP BY source
        ORDER BY count DESC
    '''
    cursor.execute(query, (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [(r["source"], r["count"]) for r in rows]

def get_all_transactions(user_id, limit=200):
    conn = get_conn()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    query = f'''
        SELECT * FROM transactions
        WHERE user_id = {placeholder}
        ORDER BY timestamp DESC
        LIMIT {placeholder}
    '''
    cursor.execute(query, (user_id, limit))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    result = []
    for r in rows:
        row = dict(r)
        row["amount"] = float(row["amount"])
        row["timestamp"] = str(row["timestamp"])
        result.append(row)
    return result
