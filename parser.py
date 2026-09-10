import os
import json
from groq import Groq

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

CATEGORIES = ["Sales", "Supplies", "Wages", "Rent", "Utilities", "Transport", "Food", "Others"]

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
    response = groq_client.chat.completions.create(
        model="groq/compound-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)


def parse_receipt_image(image_base64):
    prompt = f"""
You are extracting transaction data from a receipt image for a small business ledger.
The receipt may be computer-printed OR handwritten, and may be low quality, tilted, or partially unclear.

Read carefully and extract:
- "type": "inflow" (this is a sales receipt / money received) or "outflow" (this is a purchase / money spent).
  Most receipts a shopkeeper photographs are sales they made, so default to "inflow" unless it's
  clearly their own purchase receipt.
- "amount": the FINAL TOTAL amount (float). Look for words like "Total", "Grand Total", "Net Payable",
  or the largest/last amount if no total is labeled. Ignore subtotal/tax lines if a final total exists.
- "category": one of {", ".join(CATEGORIES)}. Infer from item names or store type if not obvious.
- "subcategory": short specific label (e.g. main item purchased), or null.
- "vendor_customer": the store/shop/business name printed or written on the receipt. Use "Unknown" if illegible.
- "description": short 5-10 word summary of what the receipt shows.

If the receipt is handwritten and partially illegible, make your best reasonable estimate rather than
refusing — this is for informal bookkeeping, not legal accuracy.

Respond ONLY with raw valid JSON, no markdown, no explanation:
{{
    "type": "inflow" or "outflow",
    "amount": 0.0,
    "category": "one of the categories above",
    "subcategory": "string or null",
    "vendor_customer": "string",
    "description": "string"
}}
"""
    response = groq_client.chat.completions.create(
        model="qwen/qwen3.6-27b",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
            ]
        }],
        temperature=0.1,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)


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
