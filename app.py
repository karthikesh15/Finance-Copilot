import os
import logging
import base64
import requests
from flask import Flask, request
from dotenv import load_dotenv

from database import init_db, add_transaction, get_cash_summary
from parser import parse_transaction_text, parse_receipt_image, transcribe_voice_note, CATEGORIES

load_dotenv()
init_db()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

SESSIONS = {}

def send_reply(chat_id, text):
    requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text})

def send_keyboard(chat_id, text, buttons, row_size=2):
    rows = [buttons[i:i + row_size] for i in range(0, len(buttons), row_size)]
    keyboard = [[{"text": label, "callback_data": data} for label, data in row] for row in rows]
    requests.post(f"{TELEGRAM_API}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": keyboard}
    })

def answer_callback(callback_query_id):
    requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": callback_query_id})

def download_telegram_file(file_id):
    res = requests.get(f"{TELEGRAM_API}/getFile?file_id={file_id}").json()
    file_path = res["result"]["file_path"]
    download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    return requests.get(download_url).content

def start_category_flow(chat_id):
    SESSIONS[chat_id] = {"step": "category"}
    buttons = [(cat, f"cat_{cat}") for cat in CATEGORIES]
    send_keyboard(chat_id, "Select a category:", buttons)

def log_final_transaction(chat_id, category, amount, tx_type, source="button", description=None, vendor_customer="Unknown"):
    add_transaction(chat_id, tx_type, amount, category, vendor_customer,
                     description=description, source=source)
    send_reply(chat_id, f"✅ Logged {tx_type}: {amount} ({category})")
    SESSIONS.pop(chat_id, None)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    if "callback_query" in data:
        return handle_callback(data["callback_query"])

    if "message" not in data:
        return "OK", 200

    msg = data["message"]
    chat_id = msg["chat"]["id"]
    session = SESSIONS.get(chat_id)

    try:
        if msg.get("text") == "/start":
            SESSIONS.pop(chat_id, None)
            send_reply(chat_id, "👋 Welcome! Use /add to log a transaction with buttons, "
                                 "or just type/send a message, photo, or voice note directly.")
            return "OK", 200

        if msg.get("text") == "/summary":
            inflow, outflow, balance = get_cash_summary(chat_id)
            reply = f"📊 Cash Summary:\n• Inflow: {inflow:.2f}\n• Outflow: {outflow:.2f}\n• Balance: {balance:.2f}"
            send_reply(chat_id, reply)
            return "OK", 200

        if msg.get("text") == "/add":
            start_category_flow(chat_id)
            return "OK", 200

        if session and session["step"] == "amount" and "text" in msg:
            try:
                amount = float(msg["text"].replace(",", "").strip())
            except ValueError:
                send_reply(chat_id, "⚠️ Please enter a valid number for the amount.")
                return "OK", 200
            session["amount"] = amount
            session["step"] = "type"
            send_keyboard(chat_id, "Inflow or outflow?", [("Inflow", "type_inflow"), ("Outflow", "type_outflow")])
            return "OK", 200

        if session and session["step"] == "others_text" and "text" in msg:
            parsed = parse_transaction_text(msg["text"])
            log_final_transaction(
                chat_id, parsed["category"], parsed["amount"], parsed["type"],
                source="text", description=parsed.get("description"),
                vendor_customer=parsed.get("vendor_customer", "Unknown")
            )
            return "OK", 200

        if "text" in msg:
            parsed = parse_transaction_text(msg["text"])
            add_transaction(chat_id, parsed["type"], parsed["amount"], parsed["category"],
                             parsed.get("vendor_customer", "Unknown"),
                             description=parsed.get("description"), source="text")
            send_reply(chat_id, f"✅ Logged {parsed['type']}: {parsed['amount']} ({parsed['vendor_customer']})")

        elif "photo" in msg:
            file_id = msg["photo"][-1]["file_id"]
            img_bytes = download_telegram_file(file_id)
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            parsed = parse_receipt_image(img_b64)
            add_transaction(chat_id, parsed["type"], parsed["amount"], parsed["category"],
                             parsed.get("vendor_customer", "Unknown"),
                             description=parsed.get("description"), source="photo")
            send_reply(chat_id, f"🧾 Receipt Logged: {parsed['amount']} at {parsed['vendor_customer']}")

        elif "voice" in msg:
            file_id = msg["voice"]["file_id"]
            voice_bytes = download_telegram_file(file_id)
            transcript = transcribe_voice_note(voice_bytes)
            parsed = parse_transaction_text(transcript)
            add_transaction(chat_id, parsed["type"], parsed["amount"], parsed["category"],
                             parsed.get("vendor_customer", "Unknown"),
                             description=parsed.get("description"), source="voice")
            send_reply(chat_id, f"🎙️ Transcribed: '{transcript}'\n✅ Logged {parsed['amount']} ({parsed['type']})")

    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        send_reply(chat_id, "⚠️ Failed to process input. Please try again.")

    return "OK", 200


def handle_callback(query):
    callback_id = query["id"]
    chat_id = query["message"]["chat"]["id"]
    data = query["data"]
    answer_callback(callback_id)

    session = SESSIONS.get(chat_id)
    if not session:
        return "OK", 200

    if data.startswith("cat_") and session["step"] == "category":
        category = data[len("cat_"):]
        session["category"] = category

        if category == "Others":
            session["step"] = "others_text"
            send_reply(chat_id, "Type your transaction as a full sentence:")
        else:
            session["step"] = "amount"
            send_reply(chat_id, f"Category: {category}\nNow enter the amount:")
        return "OK", 200

    if data.startswith("type_") and session["step"] == "type":
        tx_type = data[len("type_"):]
        log_final_transaction(chat_id, session["category"], session["amount"], tx_type, source="button")
        return "OK", 200

    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
