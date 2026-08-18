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
            vendor_customer TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def add_transaction(user_id, tx_type, amount, category, vendor_customer):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO transactions (user_id, type, amount, category, vendor_customer)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, tx_type, amount, category, vendor_customer))
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