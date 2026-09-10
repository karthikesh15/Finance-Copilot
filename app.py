import os
import logging
import base64
import requests
from flask import Flask, request
from dotenv import load_dotenv

from database import init_db, add_transaction, get_cash_summary, get_category_breakdown, get_source_breakdown
from parser import parse_transaction_text, analyze_receipt_image_safe, transcribe_voice_note

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

def show_main_menu(chat_id):
    SESSIONS[chat_id] = {"step": "main_menu"}
    buttons = [("📝 Options", "menu_options"), ("📷 Photo", "menu_photo"), ("🎙️ Voice", "menu_voice")]
    send_keyboard(chat_id, "How would you like to log this transaction?", buttons, row_size=3)

def show_category_menu(chat_id):
    SESSIONS[chat_id] = {"step": "category"}
    buttons = [(label, f"cat_{key}") for key, (label, _) in CATEGORY_CONFIG.items()]
    send_keyboard(chat_id, "Select a category:", buttons)

def show_photo_type_menu(chat_id):
    SESSIONS[chat_id] = {"step": "photo_type"}
    send_keyboard(chat_id, "Is this receipt an inflow or outflow?",
                  [("📈 Inflow", "phototype_inflow"), ("📉 Outflow", "phototype_outflow")])

def format_structured_reply(parsed, source_label):
    arrow = "📈" if parsed["type"] == "inflow" else "📉"
    lines = [
        f"{source_label} — Transaction Logged",
        f"{arrow} Type: {parsed['type'].capitalize()}",
        f"💵 Amount: {parsed['amount']}",
        f"📁 Category: {parsed['category']}",
        f"📝 Note: {parsed.get('description', '-')}"
    ]
    if parsed.get("needs_review"):
        lines.append("")
        lines.append("⚠️ Amount could not be confidently read — please verify and correct if needed.")
    return "\n".join(lines)

def log_and_continue(chat_id, category, amount, tx_type, source, description=None):
    add_transaction(chat_id, tx_type, amount, category,
                     description=description, source=source)
    show_main_menu(chat_id)

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
            send_reply(chat_id, "👋 Welcome! Choose how you'd like to log a transaction.")
            show_main_menu(chat_id)
            return "OK", 200

        if msg.get("text") == "/summary":
            send_detailed_summary(chat_id)
            return "OK", 200

        if session and session.get("step") == "amount" and "text" in msg:
            try:
                amount = float(msg["text"].replace(",", "").strip())
            except ValueError:
                send_reply(chat_id, "⚠️ Please enter a valid number for the amount.")
                return "OK", 200
            parsed = {
                "type": session["type"], "amount": amount, "category": session["category"], "description": None
            }
            send_reply(chat_id, format_structured_reply(parsed, "📝 Options"))
            log_and_continue(chat_id, session["category"], amount, session["type"], source="button")
            return "OK", 200

        if session and session.get("step") == "others_text" and "text" in msg:
            parsed = parse_transaction_text(msg["text"])
            send_reply(chat_id, format_structured_reply(parsed, "📝 Options (Others)"))
            log_and_continue(
                chat_id, parsed["category"], parsed["amount"], parsed["type"],
                source="text", description=parsed.get("description"),
            )
            return "OK", 200

        if session and session.get("step") == "awaiting_photo" and "photo" in msg:
            known_type = session.get("photo_type", "outflow")
            file_id = msg["photo"][-1]["file_id"]
            img_bytes = download_telegram_file(file_id)
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            parsed = analyze_receipt_image_safe(img_b64, known_type)
            send_reply(chat_id, format_structured_reply(parsed, "📷 Photo"))
            log_and_continue(
                chat_id, parsed["category"], parsed["amount"], parsed["type"],
                source="photo", description=parsed.get("description"),
            )
            return "OK", 200

        if session and session.get("step") == "awaiting_voice" and "voice" in msg:
            file_id = msg["voice"]["file_id"]
            voice_bytes = download_telegram_file(file_id)
            transcript = transcribe_voice_note(voice_bytes)
            parsed = parse_transaction_text(transcript)
            send_reply(chat_id, f"🎙️ Heard: \"{transcript}\"")
            send_reply(chat_id, format_structured_reply(parsed, "🎙️ Voice"))
            log_and_continue(
                chat_id, parsed["category"], parsed["amount"], parsed["type"],
                source="voice", description=parsed.get("description"),
            )
            return "OK", 200

        if "text" in msg:
            parsed = parse_transaction_text(msg["text"])
            send_reply(chat_id, format_structured_reply(parsed, "📝 Text"))
            log_and_continue(
                chat_id, parsed["category"], parsed["amount"], parsed["type"],
                source="text", description=parsed.get("description"),
            )
            return "OK", 200

        if "photo" in msg:
            show_photo_type_menu(chat_id)
            send_reply(chat_id, "📷 Got your photo — first tell me: inflow or outflow? Then resend the photo.")
            return "OK", 200

        if "voice" in msg:
            file_id = msg["voice"]["file_id"]
            voice_bytes = download_telegram_file(file_id)
            transcript = transcribe_voice_note(voice_bytes)
            parsed = parse_transaction_text(transcript)
            send_reply(chat_id, f"🎙️ Heard: \"{transcript}\"")
            send_reply(chat_id, format_structured_reply(parsed, "🎙️ Voice"))
            log_and_continue(
                chat_id, parsed["category"], parsed["amount"], parsed["type"],
                source="voice", description=parsed.get("description"),
            )
            return "OK", 200

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        send_reply(chat_id, "⚠️ Something went wrong, but nothing was lost. Please try again.")
        show_main_menu(chat_id)

    return "OK", 200


def handle_callback(query):
    callback_id = query["id"]
    chat_id = query["message"]["chat"]["id"]
    data = query["data"]
    answer_callback(callback_id)

    if data == "menu_options":
        show_category_menu(chat_id)
        return "OK", 200

    if data == "menu_photo":
        show_photo_type_menu(chat_id)
        return "OK", 200

    if data == "menu_voice":
        SESSIONS[chat_id] = {"step": "awaiting_voice"}
        send_reply(chat_id, "🎙️ Send a voice note describing the transaction.")
        return "OK", 200

    if data in ("phototype_inflow", "phototype_outflow"):
        tx_type = "inflow" if data == "phototype_inflow" else "outflow"
        SESSIONS[chat_id] = {"step": "awaiting_photo", "photo_type": tx_type}
        send_reply(chat_id, f"📷 Got it — {tx_type}. Now send the receipt photo.")
        return "OK", 200

    session = SESSIONS.get(chat_id)
    if not session:
        return "OK", 200

    if data.startswith("cat_") and session.get("step") == "category":
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
    source_breakdown = get_source_breakdown(chat_id)

    lines = [
        "📊 Cash Summary",
        f"• Total Inflow: {inflow:.2f}",
        f"• Total Outflow: {outflow:.2f}",
        f"• Net Balance: {balance:.2f}",
        "",
        "📁 Category Breakdown"
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
        lines.append("🏆 Highlights")
        if top_inflow:
            lines.append(f"Top income source: {top_inflow[0]} ({top_inflow[2]:.2f})")
        if top_outflow:
            lines.append(f"Top expense: {top_outflow[0]} ({top_outflow[2]:.2f})")

    lines.append("")
    lines.append("📡 Entries by Source")
    if not source_breakdown:
        lines.append("No entries yet.")
    else:
        source_labels = {"button": "📝 Options", "text": "💬 Text", "photo": "📷 Photo", "voice": "🎙️ Voice"}
        for source, count in source_breakdown:
            lines.append(f"{source_labels.get(source, source)}: {count} entries")

    send_reply(chat_id, "\n".join(lines))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
