import os
import logging
import base64
import requests
from flask import Flask, request
from dotenv import load_dotenv
from flask import send_file

from database import init_db, add_transaction, get_cash_summary
from parser import parse_transaction_text, parse_receipt_image, transcribe_voice_note

load_dotenv()
init_db()  # Initialize SQLite database schema on start

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def send_reply(chat_id, text):
    requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text})

def download_telegram_file(file_id):
    res = requests.get(f"{TELEGRAM_API}/getFile?file_id={file_id}").json()
    file_path = res["result"]["file_path"]
    download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    return requests.get(download_url).content

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if "message" not in data:
        return "OK", 200

    msg = data["message"]
    chat_id = msg["chat"]["id"]

    try:
        # Feature 1: Cash Summary Command (/summary)
        if msg.get("text") == "/summary":
            inflow, outflow, balance = get_cash_summary(chat_id)
            reply = f"📊 Cash Summary:\n• Inflow: ${inflow:.2f}\n• Outflow: ${outflow:.2f}\n• Balance: ${balance:.2f}"
            send_reply(chat_id, reply)
            return "OK", 200

        # Feature 2: Text Transaction Parsing via Groq
        if "text" in msg:
            parsed = parse_transaction_text(msg["text"])
            add_transaction(chat_id, parsed["type"], parsed["amount"], parsed["category"], parsed["vendor_customer"])
            send_reply(chat_id, f"✅ Logged {parsed['type']}: ${parsed['amount']} ({parsed['vendor_customer']})")

        # Feature 3: Receipt OCR Parsing via Groq Vision
        elif "photo" in msg:
            file_id = msg["photo"][-1]["file_id"]
            img_bytes = download_telegram_file(file_id)
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            parsed = parse_receipt_image(img_b64)
            add_transaction(chat_id, parsed["type"], parsed["amount"], parsed["category"], parsed["vendor_customer"])
            send_reply(chat_id, f"🧾 Receipt Logged: ${parsed['amount']} at {parsed['vendor_customer']}")

        # Feature 4: Voice Notes Processing via Whisper + Groq
        elif "voice" in msg:
            file_id = msg["voice"]["file_id"]
            voice_bytes = download_telegram_file(file_id)
            transcript = transcribe_voice_note(voice_bytes)
            parsed = parse_transaction_text(transcript)
            add_transaction(chat_id, parsed["type"], parsed["amount"], parsed["category"], parsed["vendor_customer"])
            send_reply(chat_id, f"🎙️ Transcribed: '{transcript}'\n✅ Logged ${parsed['amount']} ({parsed['type']})")

    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        send_reply(chat_id, "⚠️ Failed to process input. Please try again.")

    return "OK", 200

@app.route("/download-db", methods=["GET"])
def download_db():
    if os.path.exists("ledger.db"):
        return send_file("ledger.db", as_attachment=True)
    return "Database file not found on server yet.", 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
