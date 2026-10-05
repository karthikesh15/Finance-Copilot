import os
import logging
import base64
import requests
from functools import wraps

from flask import (
    Flask,
    request,
    render_template,
    jsonify,
    session,
    redirect,
    url_for
)

from dotenv import load_dotenv

from database import (
    init_db,
    add_transaction,
    get_cash_summary,
    get_category_breakdown,
    get_source_breakdown,
    get_all_transactions
)

from parser import (
    parse_transaction_text,
    analyze_receipt_image_safe,
    transcribe_voice_note
)


# ============================================================
# SETUP
# ============================================================

load_dotenv()

init_db()

app = Flask(__name__)

# ------------------------------------------------------------
# SECURITY CONFIGURATION
# ------------------------------------------------------------

app.secret_key = os.getenv("FLASK_SECRET_KEY")

app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax"
)

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")

# ------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

SESSIONS = {}


# ============================================================
# CATEGORY CONFIGURATION
# ============================================================

CATEGORY_CONFIG = {
    "Sales":     ("🛒 Sales", "inflow"),
    "Supplies":  ("📦 Supplies", "outflow"),
    "Wages":     ("👷 Wages", "outflow"),
    "Rent":      ("🏠 Rent", "outflow"),
    "Utilities": ("💡 Utilities", "outflow"),
    "Transport": ("🚗 Transport", "outflow"),
    "Food":      ("🍔 Food", "outflow"),
    "Others":    ("📝 Others", None),
}


# ============================================================
# TELEGRAM HELPERS
# ============================================================

def send_reply(chat_id, text):
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text
            },
            timeout=10
        )
    except Exception as e:
        logger.error(f"send_reply error: {e}")


def send_dashboard_button(chat_id):
    dashboard_url = (
        f"https://finance-copilot-4.onrender.com/"
        f"dashboard/{chat_id}"
    )

    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "📊 Your Finance Dashboard\n\n"
                        "🔐 Password protection is enabled.",
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {
                                "text": "📊 Open Dashboard",
                                "url": dashboard_url
                            }
                        ]
                    ]
                }
            },
            timeout=10
        )
    except Exception as e:
        logger.error(f"send_dashboard_button error: {e}")


def send_keyboard(chat_id, text, keyboard):
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "reply_markup": {
                    "inline_keyboard": keyboard
                }
            },
            timeout=10
        )
    except Exception as e:
        logger.error(f"send_keyboard error: {e}")


def answer_callback(callback_query):
    try:
        requests.post(
            f"{TELEGRAM_API}/answerCallbackQuery",
            json={
                "callback_query_id": callback_query["id"]
            },
            timeout=10
        )
    except Exception as e:
        logger.error(f"answer_callback error: {e}")


def download_telegram_file(file_id):
    try:
        response = requests.get(
            f"{TELEGRAM_API}/getFile",
            params={"file_id": file_id},
            timeout=10
        )

        response.raise_for_status()

        file_path = response.json()["result"]["file_path"]

        file_url = (
            f"https://api.telegram.org/file/bot"
            f"{BOT_TOKEN}/{file_path}"
        )

        file_response = requests.get(
            file_url,
            timeout=30
        )

        file_response.raise_for_status()

        return file_response.content

    except Exception as e:
        logger.error(f"download_telegram_file error: {e}")
        return None


# ============================================================
# TELEGRAM MENUS
# ============================================================

def show_main_menu(chat_id):
    keyboard = [
        [
            {
                "text": "📝 Choose Category",
                "callback_data": "menu_options"
            }
        ],
        [
            {
                "text": "📸 Receipt Photo",
                "callback_data": "menu_photo"
            }
        ],
        [
            {
                "text": "🎤 Voice Message",
                "callback_data": "menu_voice"
            }
        ]
    ]

    send_keyboard(
        chat_id,
        "📌 Choose how you want to record your transaction:",
        keyboard
    )


def show_category_menu(chat_id):
    keyboard = []

    for key, (label, _) in CATEGORY_CONFIG.items():
        keyboard.append([
            {
                "text": label,
                "callback_data": f"category_{key}"
            }
        ])

    send_keyboard(
        chat_id,
        "📝 Choose a transaction category:\n\n"
        "🛒 Sales → Money you received\n"
        "📦 Supplies → Materials/items you bought\n"
        "👷 Wages → Employee payments\n"
        "🏠 Rent → Shop/office rent\n"
        "💡 Utilities → Electricity, water, internet\n"
        "🚗 Transport → Travel/delivery expenses\n"
        "🍔 Food → Food expenses\n"
        "📝 Others → Anything that doesn't fit above",
        keyboard
    )


def show_photo_type_menu(chat_id):
    keyboard = [
        [
            {
                "text": "📈 Money Received",
                "callback_data": "phototype_inflow"
            }
        ],
        [
            {
                "text": "📉 Money Spent",
                "callback_data": "phototype_outflow"
            }
        ]
    ]

    send_keyboard(
        chat_id,
        "📸 What does the receipt represent?",
        keyboard
    )


# ============================================================
# DASHBOARD SECURITY
# ============================================================

def dashboard_authenticated(chat_id):
    return session.get("dashboard_chat_id") == chat_id


# ============================================================
# DASHBOARD LOGIN
# ============================================================

@app.route(
    "/dashboard/login/<int:chat_id>",
    methods=["GET", "POST"]
)
def dashboard_login(chat_id):

    error = None

    if request.method == "POST":

        password = request.form.get("password", "")

        if DASHBOARD_PASSWORD and password == DASHBOARD_PASSWORD:

            session["dashboard_chat_id"] = chat_id

            return redirect(
                url_for(
                    "dashboard",
                    chat_id=chat_id
                )
            )

        error = "❌ Incorrect password."

    return render_template(
        "dashboard_login.html",
        chat_id=chat_id,
        error=error
    )


# ============================================================
# DASHBOARD LOGOUT
# ============================================================

@app.route("/dashboard/logout/<int:chat_id>")
def dashboard_logout(chat_id):

    session.pop("dashboard_chat_id", None)

    return redirect(
        url_for(
            "dashboard_login",
            chat_id=chat_id
        )
    )


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/dashboard/<int:chat_id>")
def dashboard(chat_id):

    if not dashboard_authenticated(chat_id):
        return redirect(
            url_for(
                "dashboard_login",
                chat_id=chat_id
            )
        )

    return render_template(
        "dashboard.html",
        chat_id=chat_id
    )


# ============================================================
# DASHBOARD API
# ============================================================

@app.route("/api/summary/<int:chat_id>")
def api_summary(chat_id):

    if not dashboard_authenticated(chat_id):
        return jsonify({
            "error": "Unauthorized"
        }), 401

    inflow, outflow, balance = get_cash_summary(chat_id)

    breakdown = get_category_breakdown(chat_id)

    source_breakdown = get_source_breakdown(chat_id)

    transactions = get_all_transactions(chat_id)

    return jsonify({
        "inflow": inflow,
        "outflow": outflow,
        "balance": balance,
        "category_breakdown": breakdown,
        "source_breakdown": source_breakdown,
        "transactions": transactions
    })


# ============================================================
# TELEGRAM CALLBACK HANDLER
# ============================================================

def handle_callback(callback_query):

    chat_id = callback_query["message"]["chat"]["id"]
    data = callback_query.get("data", "")

    answer_callback(callback_query)

    # --------------------------------------------------------
    # MAIN MENU
    # --------------------------------------------------------

    if data == "menu_options":
        show_category_menu(chat_id)
        return "OK"

    if data == "menu_photo":
        show_photo_type_menu(chat_id)
        return "OK"

    if data == "menu_voice":

        SESSIONS[chat_id] = {
            "mode": "voice"
        }

        send_reply(
            chat_id,
            "🎤 Send your voice message describing the transaction.\n\n"
            "Example:\n"
            "“Spent ₹500 on office supplies.”"
        )

        return "OK"

    # --------------------------------------------------------
    # PHOTO TYPE
    # --------------------------------------------------------

    if data == "phototype_inflow":

        SESSIONS[chat_id] = {
            "mode": "photo",
            "type": "inflow"
        }

        send_reply(
            chat_id,
            "📈 Send the receipt photo.\n\n"
            "I'll try to identify the money received."
        )

        return "OK"

    if data == "phototype_outflow":

        SESSIONS[chat_id] = {
            "mode": "photo",
            "type": "outflow"
        }

        send_reply(
            chat_id,
            "📉 Send the receipt photo.\n\n"
            "I'll try to identify the money spent."
        )

        return "OK"

    # --------------------------------------------------------
    # CATEGORY
    # --------------------------------------------------------

    if data.startswith("category_"):

        category = data.replace(
            "category_",
            "",
            1
        )

        if category not in CATEGORY_CONFIG:
            return "OK"

        label, transaction_type = CATEGORY_CONFIG[category]

        SESSIONS[chat_id] = {
            "mode": "category",
            "category": category,
            "type": transaction_type
        }

        if category == "Others":

            send_reply(
                chat_id,
                "📝 Describe the transaction.\n\n"
                "Example:\n"
                "“Spent ₹800 on miscellaneous expenses.”"
            )

        else:

            send_reply(
                chat_id,
                f"{label}\n\n"
                "💬 Now describe the transaction.\n\n"
                "Example:\n"
                "“Spent ₹500 on office supplies.”"
            )

        return "OK"

    return "OK"


# ============================================================
# SEND RECENT TRANSACTIONS
# ============================================================

def send_recent_rows(chat_id):

    try:

        transactions = get_all_transactions(chat_id)

        if not transactions:

            send_reply(
                chat_id,
                "📭 No transactions found."
            )

            return

        rows = transactions[-10:]

        message = "📋 Recent Transactions\n\n"

        for row in rows:

            message += (
                f"• {row}\n"
            )

        send_reply(
            chat_id,
            message
        )

    except Exception as e:

        logger.error(
            f"send_recent_rows error: {e}"
        )

        send_reply(
            chat_id,
            "❌ Unable to load transactions."
        )


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

@app.route("/webhook", methods=["POST"])
def webhook():

    data = request.get_json()

    logger.info(
        f"RAW INCOMING: {data}"
    )

    if "callback_query" in data:

        return handle_callback(
            data["callback_query"]
        )

    if "message" not in data:

        return "OK", 200

    msg = data["message"]

    chat_id = msg["chat"]["id"]

    text = msg.get(
        "text",
        ""
    ).strip()

    try:

        # ----------------------------------------------------
        # START
        # ----------------------------------------------------

        if text.startswith("/start"):

            SESSIONS.pop(
                chat_id,
                None
            )

            send_reply(
                chat_id,
                "👋 Welcome to Finance Copilot!\n\n"
                "💰 Track your business income and expenses "
                "using text, photos or voice."
            )

            show_main_menu(chat_id)

            return "OK", 200

        # ----------------------------------------------------
        # DASHBOARD
        # ----------------------------------------------------

        if text.startswith("/dashboard"):

            send_dashboard_button(
                chat_id
            )

            return "OK", 200

        # ----------------------------------------------------
        # TRANSACTIONS
        # ----------------------------------------------------

        if (
            text.startswith("/transactions")
            or text.startswith("/rows")
        ):

            send_recent_rows(
                chat_id
            )

            return "OK", 200

        # ----------------------------------------------------
        # PHOTO
        # ----------------------------------------------------

        if "photo" in msg:

            session_data = SESSIONS.get(
                chat_id,
                {}
            )

            transaction_type = session_data.get(
                "type"
            )

            if not transaction_type:

                send_reply(
                    chat_id,
                    "📸 Please choose whether the receipt "
                    "is money received or money spent."
                )

                show_photo_type_menu(
                    chat_id
                )

                return "OK", 200

            photo = msg["photo"][-1]

            image_bytes = download_telegram_file(
                photo["file_id"]
            )

            if image_bytes:

                result = analyze_receipt_image_safe(
                    image_bytes
                )

                if result:

                    result["type"] = transaction_type

                    add_transaction(
                        chat_id,
                        result
                    )

                    send_reply(
                        chat_id,
                        "✅ Receipt processed successfully."
                    )

                else:

                    send_reply(
                        chat_id,
                        "⚠️ I couldn't read the receipt."
                    )

            return "OK", 200

        # ----------------------------------------------------
        # VOICE
        # ----------------------------------------------------

        if "voice" in msg:

            voice = msg["voice"]

            audio_bytes = download_telegram_file(
                voice["file_id"]
            )

            if audio_bytes:

                result = transcribe_voice_note(
                    audio_bytes
                )

                if result:

                    parsed = parse_transaction_text(
                        result
                    )

                    if parsed:

                        add_transaction(
                            chat_id,
                            parsed
                        )

                        send_reply(
                            chat_id,
                            "✅ Voice transaction recorded."
                        )

                    else:

                        send_reply(
                            chat_id,
                            "⚠️ I couldn't understand the transaction."
                        )

            return "OK", 200

        # ----------------------------------------------------
        # NORMAL TEXT
        # ----------------------------------------------------

        if text:

            current_session = SESSIONS.get(
                chat_id,
                {}
            )

            parsed = parse_transaction_text(
                text
            )

            if parsed:

                if current_session.get("category"):

                    parsed["category"] = current_session[
                        "category"
                    ]

                if current_session.get("type"):

                    parsed["type"] = current_session[
                        "type"
                    ]

                add_transaction(
                    chat_id,
                    parsed
                )

                SESSIONS.pop(
                    chat_id,
                    None
                )

                send_reply(
                    chat_id,
                    "✅ Transaction recorded successfully."
                )

                show_main_menu(
                    chat_id
                )

                return "OK", 200

            send_reply(
                chat_id,
                "⚠️ I couldn't understand that transaction.\n\n"
                "Example:\n"
                "Spent ₹500 on supplies."
            )

            return "OK", 200

    except Exception as e:

        logger.error(
            f"Webhook error: {e}",
            exc_info=True
        )

        send_reply(
            chat_id,
            "⚠️ Something went wrong, but nothing was lost. "
            "Please try again."
        )

        show_main_menu(
            chat_id
        )

    return "OK", 200


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/")
def home():
    return "Finance Copilot is running."


# ============================================================
# SERVER
# ============================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
