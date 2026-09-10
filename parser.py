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
- "description" is a short 5-10 word plain-English summary of the transaction.
- If the message describes multiple transactions, extract only the FIRST/PRIMARY one.

Respond ONLY with raw valid JSON in this exact format, no markdown, no explanation:
{{
    "type": "inflow" or "outflow",
    "amount": 0.0,
    "category": "one of the categories above",
    "subcategory": "string or null",
    "description": "string"
}}
"""
    messages = [{"role": "user", "content": prompt}]
    return safe_chat_call(messages, model="groq/compound-mini")

def parse_receipt_image(image_base64, known_type):
    prompt = f"""
You are an expert at reading receipts and handwritten bills for a small business ledger.
The image may contain a computer-printed OR handwritten bill, possibly with MULTIPLE line items,
a quantity column, a tax line, and a final total. The image may ALSO contain unrelated handwritten
content nearby (math notes, doodles, diagrams, equations, scribbles) — you must IGNORE anything that
is not clearly part of the itemized bill/receipt.

The transaction TYPE is already known: "{known_type}". Do not try to determine type.

Read the bill carefully and reason step by step internally (but output only the final JSON):

1. Identify ONLY the rows that belong to the itemized bill — usually a list of item names, each with
   a quantity and a price, possibly followed by a tax percentage and a final total line
   (labels like "Total", "Rounded off Invoice Amount", "Net Payable", "Grand Total").
2. Ignore any numbers, letters, diagrams, or notes elsewhere in the image that are not part of this
   itemized list — e.g. unrelated math problems, geometry sketches, random calculations, doodles.
3. For "amount": if a final total/invoice-amount line is present, use that exact figure — it already
   includes tax. If no final total is given, sum the item prices (accounting for quantity if shown)
   and add any tax percentage shown. Do not confuse an item's price or a tax percentage with the total.
4. For "category": infer from the item names as a group (e.g. drinks/cake/biscuits → "Food";
   hardware/materials → "Supplies"; etc). Choose exactly one of: {", ".join(CATEGORIES)}.
5. For "description": summarize the actual items bought/sold in a few words, e.g.
   "Sparkling cold brew, banana cake, almond biscuit" — list the real item names you read, not generic terms.
6. For "subcategory": one short label representing the main item type, or null.

If handwriting is partially illegible, make your best reasonable estimate rather than refusing —
this is for informal bookkeeping, not legal accuracy. Always return a complete JSON object.

Respond ONLY with raw valid JSON, no markdown, no explanation:
{{
    "amount": 0.0,
    "category": "one of the categories above",
    "subcategory": "string or null",
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
    return safe_chat_call(messages, model="qwen/qwen3.8-27b")


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
            "description": result.get("description") or "Extracted from receipt image",
            "needs_review": amount == 0.0
        }
    except Exception:
        return {
            "type": known_type,
            "amount": 0.0,
            "category": "Others",
            "subcategory": None,
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
