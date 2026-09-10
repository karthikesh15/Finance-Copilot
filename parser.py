import os
import re
import json
from groq import Groq

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

CATEGORIES = ["Sales", "Supplies", "Wages", "Rent", "Utilities", "Transport", "Food", "Others"]


def extract_json(raw_text):
    match = re.search(r'\{.*\}', raw_text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in model output: {raw_text[:200]}")
    return json.loads(match.group(0))


def safe_chat_call(messages, model, use_json_mode=True):
    try:
        kwargs = {"model": model, "messages": messages, "temperature": 0.1}
        if use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = groq_client.chat.completions.create(**kwargs)
        return json.loads(response.choices[0].message.content)
    except Exception:
        response = groq_client.chat.completions.create(
            model=model, messages=messages, temperature=0.1
        )
        return extract_json(response.choices[0].message.content)


def parse_transaction_text(text_input):
    prompt = f"""
You are a financial transaction extraction assistant for a small business ledger.
Extract the transaction details from the message below, even if it is long, indirect,
uses informal language, or mixes multiple details together.

Message: "{text_input}"

Rules:
- "type" must be exactly "inflow" (money received — sales, payments received, refunds received)
  or "outflow" (money spent — purchases, wages paid, rent, expenses).
- "amount" must be a plain number (float). Handle formats like "₹500", "500rs", "Rs 500",
  "5k" (=5000), "2.5k" (=2500), "$20", or plain numbers.
- "category" must be exactly ONE of: {", ".join(CATEGORIES)}. If nothing fits clearly, use "Others".
- "subcategory" is a short, more specific label if useful (e.g. "rice" under "Sales"), else null.
- "vendor_customer" is the person or business involved (e.g. "Ramesh", "ABC Traders").
  Use "Unknown" if not mentioned.
- "description" is a short 5-10 word plain-English summary of the transaction.
- If the message describes multiple transactions, extract only the FIRST/PRIMARY one.

Respond ONLY with raw valid JSON in this exact format, no markdown, no explanation:
{{
    "type": "inflow" or "outflow",
    "amount": 0.0,
    "category": "one of the categories above",
    "subcategory": "string or null",
    "vendor_customer": "string",
    "description": "string"
}}
"""
    messages = [{"role": "user", "content": prompt}]
    return safe_chat_call(messages, model="groq/compound-mini")


def parse_receipt_image(image_base64, known_type):
    prompt = f"""
You are an expert at reading receipts for a small business ledger — computer-printed OR handwritten,
possibly low quality, tilted, or partially unclear.

The transaction TYPE is already known: "{known_type}". Do not try to determine type — focus only on
the fields below, and use careful visual reasoning even when details aren't explicitly labeled.

Think it through like this:
- Look at item names/rows to figure out the QUANTITY and TITLE of what was bought/sold, even if there's
  no "Qty" column — e.g. "Rice 2 x 50" implies quantity 2 at unit price 50. If only a total is visible
  with no breakdown, infer the most likely single item from context (shop type, item names visible).
- Find the AMOUNT: prefer a labeled "Total"/"Grand Total"/"Net Payable" figure. If no total is labeled,
  sum the visible line items, or use the largest clearly-legible number if math isn't possible.
- Infer the CATEGORY from the item names or the store name/type (e.g. a hardware store implies
  "Supplies", a food stall implies "Food"). Choose exactly one of: {", ".join(CATEGORIES)}.
  Use "Others" only if truly nothing suggests a category.
- Infer VENDOR/CUSTOMER from any store name, letterhead, signature, or handwriting on the receipt.
  If genuinely nothing is visible, use "Unknown" — but check corners and headers carefully first.
- Write a "description" that reflects what you inferred, not just what was printed — e.g.
  "2kg rice and 1 bag flour from grocery store" even if those exact words don't appear.

This is for informal bookkeeping, not legal accuracy — always make your best reasonable estimate
rather than refusing, and always return a complete JSON object with every field filled.

Respond ONLY with raw valid JSON, no markdown, no explanation:
{{
    "amount": 0.0,
    "category": "one of the categories above",
    "subcategory": "string or null",
    "vendor_customer": "string",
    "description": "string"
}}
"""
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
        ]
    }]
    return safe_chat_call(messages, model="qwen/qwen3.6-27b")


def analyze_receipt_image_safe(image_base64, known_type):
    try:
        result = parse_receipt_image(image_base64, known_type)
        amount = float(result.get("amount", 0) or 0)
        category = result.get("category") or "Others"
        if category not in CATEGORIES:
            category = "Others"
        return {
            "type": known_type,
            "amount": amount,
            "category": category,
            "subcategory": result.get("subcategory"),
            "vendor_customer": result.get("vendor_customer") or "Unknown",
            "description": result.get("description") or "Extracted from receipt image",
            "needs_review": amount == 0.0
        }
    except Exception:
        return {
            "type": known_type,
            "amount": 0.0,
            "category": "Others",
            "subcategory": None,
            "vendor_customer": "Unknown",
            "description": "⚠️ Could not auto-read this receipt — please edit manually",
            "needs_review": True
        }


def transcribe_voice_note(file_bytes):
    temp_path = "temp_voice.ogg"
    with open(temp_path, "wb") as f:
        f.write(file_bytes)

    with open(temp_path, "rb") as audio_file:
        transcription = groq_client.audio.transcriptions.create(
            file=(temp_path, audio_file.read()),
            model="whisper-large-v3-turbo"
        )

    if os.path.exists(temp_path):
        os.remove(temp_path)

    return transcription.text
