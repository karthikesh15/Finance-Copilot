import os
import logging
import base64
import requests
from flask import Flask, request
from dotenv import load_dotenv

from database import init_db, add_transaction, get_cash_summary, get_category_breakdown
from parser import parse_transaction_text, parse_receipt_image, transcribe_voice_note

load_dotenv()
init_db()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

SESSIONS = {}

CATEGORY_CONFIG = {
    "Sales":     ("💰 Sales",     "inflow"),
    "Supplies":  ("📦 Supplies",  "outflow"),
    "Wages":     ("👷 Wages",     "outflow"),
    "Rent":      ("🏠 Rent",      "outflow"),
    "Utilities": ("⚡ Utilities", "outflow"),
    "Transport": ("🚚 Transport", "outflow"),
    "Food":      ("🍔 Food",      "outflow"),
    "Others":    ("✏️ Others",    None),
}

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

def show_category_menu(chat_id):
    SESSIONS[chat_id] = {"step": "category"}
    buttons = [(label, f"cat_{key}") for key, (label, _) in CATEGORY_CONFIG.items()]
    send_keyboard(chat_id, "What would you like to log?", buttons)

def log_and_continue(chat_id, category, amount, tx_type, source, description=None, vendor_customer="Unknown"):
    add_transaction(chat_id, tx_type, amount, category, vendor_customer,
                     description=description, source=source)
    send_reply(chat_id, f"✅ Logged {tx_type}: {amount} ({category})")
    show_category_menu(chat_id)

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
            send_reply(chat_id, "👋 Welcome! Pick a category below to log a transaction, "
                                 "or send a photo/voice note anytime.")
            show_category_menu(chat_id)
            return "OK", 200

        if msg.get("text") == "/summary":
            send_detailed_summary(chat_id)
            return "OK", 200

        if session and session["step"] == "amount" and "text" in msg:
            try:
                amount = float(msg["text"].replace(",", "").strip())
            except ValueError:
                send_reply(chat_id, "⚠️ Please enter a valid number for the amount.")
                return "OK", 200
            log_and_continue(chat_id, session["category"], amount, session["type"], source="button")
            return "OK", 200

        if session and session["step"] == "others_text" and "text" in msg:
            parsed = parse_transaction_text(msg["text"])
            log_and_continue(
                chat_id, parsed["category"], parsed["amount"], parsed["type"],
                source="text", description=parsed.get("description"),
                vendor_customer=parsed.get("vendor_customer", "Unknown")
            )
            return "OK", 200

        if "text" in msg:
            parsed = parse_transaction_text(msg["text"])
            log_and_continue(
                chat_id, parsed["category"], parsed["amount"], parsed["type"],
                source="text", description=parsed.get("description"),
                vendor_customer=parsed.get("vendor_customer", "Unknown")
            )

        elif "photo" in msg:
            file_id = msg["photo"][-1]["file_id"]
            img_bytes = download_telegram_file(file_id)
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            parsed = parse_receipt_image(img_b64)
            log_and_continue(
                chat_id, parsed["category"], parsed["amount"], parsed["type"],
                source="photo", description=parsed.get("description"),
                vendor_customer=parsed.get("vendor_customer", "Unknown")
            )

        elif "voice" in msg:
            file_id = msg["voice"]["file_id"]
            voice_bytes = download_telegram_file(file_id)
            transcript = transcribe_voice_note(voice_bytes)
            parsed = parse_transaction_text(transcript)
            send_reply(chat_id, f"🎙️ Heard: '{transcript}'")
            log_and_continue(
                chat_id, parsed["category"], parsed["amount"], parsed["type"],
                source="voice", description=parsed.get("description"),
                vendor_customer=parsed.get("vendor_customer", "Unknown")
            )

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        send_reply(chat_id, "⚠️ Failed to process input. Please try again.")
        show_category_menu(chat_id)

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
        label, tx_type = CATEGORY_CONFIG[category]

        if category == "Others":
            session["step"] = "others_text"
            send_reply(chat_id, "Type your transaction as a full sentence:")
        else:
            session["step"] = "amount"
            session["category"] = category
            session["type"] = tx_type
            arrow = "📈 in" if tx_type == "inflow" else "📉 out"
            send_reply(chat_id, f"{label} ({arrow}) — enter the amount:")
        return "OK", 200

    return "OK", 200


def send_detailed_summary(chat_id):
    inflow, outflow, balance = get_cash_summary(chat_id)
    breakdown = get_category_breakdown(chat_id)

    lines = [
        "📊 *Cash Summary*",
        f"• Total Inflow: {inflow:.2f}",
        f"• Total Outflow: {outflow:.2f}",
        f"• Net Balance: {balance:.2f}",
        "",
        "📁 *Category Breakdown*"
    ]

    if not breakdown:
        lines.append("No transactions yet.")
    else:
        for category, tx_type, total in breakdown:
            arrow = "📈" if tx_type == "inflow" else "📉"
            lines.append(f"{arrow} {category}: {total:.2f}")

        top_inflow = max((r for r in breakdown if r[1] == "inflow"), key=lambda r: r[2], default=None)
        top_outflow = max((r for r in breakdown if r[1] == "outflow"), key=lambda r: r[2], default=None)

        lines.append("")
        lines.append("🏆 *Highlights*")
        if top_inflow:
            lines.append(f"Top income source: {top_inflow[0]} ({top_inflow[2]:.2f})")
        if top_outflow:
            lines.append(f"Top expense: {top_outflow[0]} ({top_outflow[2]:.2f})")

    send_reply(chat_id, "\n".join(lines))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
