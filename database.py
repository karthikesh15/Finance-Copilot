import os
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def init_db():
    pass


def add_transaction(user_id, tx_type, amount, category,
                     subcategory=None, description=None, source="text"):
    supabase.table("transactions").insert({
        "user_id": user_id,
        "type": tx_type,
        "amount": amount,
        "category": category,
        "subcategory": subcategory,
        "description": description,
        "source": source
    }).execute()


def get_cash_summary(user_id):
    response = supabase.table("transactions").select("type, amount").eq("user_id", user_id).execute()
    rows = response.data
    inflow = sum(r["amount"] for r in rows if r["type"] == "inflow")
    outflow = sum(r["amount"] for r in rows if r["type"] == "outflow")
    return inflow, outflow, (inflow - outflow)


def get_category_breakdown(user_id):
    response = supabase.table("transactions").select("category, type, amount").eq("user_id", user_id).execute()
    rows = response.data
    breakdown = {}
    for r in rows:
        key = (r["category"], r["type"])
        breakdown[key] = breakdown.get(key, 0) + r["amount"]
    result = [(k[0], k[1], v) for k, v in breakdown.items()]
    result.sort(key=lambda x: x[2], reverse=True)
    return result


def get_source_breakdown(user_id):
    response = supabase.table("transactions").select("source").eq("user_id", user_id).execute()
    rows = response.data
    counts = {}
    for r in rows:
        counts[r["source"]] = counts.get(r["source"], 0) + 1
    return sorted(counts.items(), key=lambda x: x[1], reverse=True)


def get_all_transactions(user_id, limit=200):
    response = (
        supabase.table("transactions")
        .select("*")
        .eq("user_id", user_id)
        .order("timestamp", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data
