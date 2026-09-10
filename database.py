import sqlite3

DB_NAME = "ledger.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT CHECK(type IN ('inflow', 'outflow')),
            amount REAL NOT NULL,
            category TEXT,
            subcategory TEXT,
            description TEXT,
            source TEXT CHECK(source IN ('text', 'photo', 'voice', 'button')),
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def add_transaction(user_id, tx_type, amount, category,
                     subcategory=None, description=None, source="text"):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO transactions (user_id, type, amount, category, subcategory, description, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, tx_type, amount, category, subcategory, description, source))
    conn.commit()
    conn.close()

def get_cash_summary(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT
            SUM(CASE WHEN type='inflow' THEN amount ELSE 0 END) as total_in,
            SUM(CASE WHEN type='outflow' THEN amount ELSE 0 END) as total_out
        FROM transactions WHERE user_id = ?
    ''', (user_id,))
    row = cursor.fetchone()
    conn.close()

    inflow = row[0] or 0.0
    outflow = row[1] or 0.0
    return inflow, outflow, (inflow - outflow)

def get_category_breakdown(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT category, type, SUM(amount) as total
        FROM transactions
        WHERE user_id = ?
        GROUP BY category, type
        ORDER BY total DESC
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_source_breakdown(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT source, COUNT(*) as count
        FROM transactions
        WHERE user_id = ?
        GROUP BY source
        ORDER BY count DESC
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows
