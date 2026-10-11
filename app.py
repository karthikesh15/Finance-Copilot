import os
import re
import math
import time
import base64
import logging
import requests
import hmac
from datetime import timedelta
from functools import wraps

from flask import (Flask, request, render_template, jsonify,
                   session, redirect, url_for, send_file)
from itsdangerous import URLSafeTimedSerializer, BadSignature
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

from database import (
    init_db, add_transaction, get_cash_summary, get_category_breakdown,
    get_source_breakdown, get_all_transactions,
    get_user, get_user_by_username, set_language, set_credentials
)
from parser import parse_transaction_text, analyze_receipt_image_safe, transcribe_voice_note
from i18n import (
    LANGS, LANG_PROMPT, CATEGORY_CONFIG, t, cat_name, cat_label, category_prompt
)

init_db()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    if os.getenv("RENDER"):
        raise RuntimeError("SECRET_KEY environment variable is required in production")
    SECRET_KEY = "dev-only-secret"

app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.getenv("RENDER")),
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

SESSIONS = {}      # per-chat conversation state (in memory)
LANG_CACHE = {}    # chat_id -> language code (source of truth is the DB)

serializer = URLSafeTimedSerializer(SECRET_KEY, salt="login-setup")
TOKEN_MAX_AGE = 1800  # 30 minutes
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")

LOGIN_ATTEMPTS = {}
MAX_ATTEMPTS, LOCK_SECONDS = 5, 300

SRC_ICON = {"button": "📝", "text": "💬", "photo": "📸", "voice": "🎤"}


# ============================================================
# AUTH HELPERS
# ============================================================

def client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    return xff.split(",")[-1].strip() if xff else (request.remote_addr or "")


def is_locked(key):
    count, ts = LOGIN_ATTEMPTS.get(key, (0, 0))
    if time.time() - ts >= LOCK_SECONDS:
        LOGIN_ATTEMPTS.pop(key, None)
        return False
    return count >= MAX_ATTEMPTS


def record_failure(key):
    count, ts = LOGIN_ATTEMPTS.get(key, (0, time.time()))
    LOGIN_ATTEMPTS[key] = (count + 1, ts)


def _fingerprint(user):
    return ((user or {}).get("password_hash") or "")[-10:]


def make_setup_token(chat_id):
    # Fingerprint changes once a password is set, so each link works only once.
    return serializer.dumps([chat_id, _fingerprint(get_user(chat_id))])


def read_setup_token(token):
    try:
        chat_id, fp = serializer.loads(token, max_age=TOKEN_MAX_AGE)
    except (BadSignature, ValueError, TypeError):
        return None
    return chat_id if fp == _fingerprint(get_user(chat_id)) else None


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "chat_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


def base_url():
    env = os.getenv("BASE_URL")
    if env:
        return env.rstrip("/")
    url = request.host_url.rstrip("/")
    if "localhost" not in url and "127.0.0.1" not in url:
        url = url.replace("http://", "https://")
    return url


# ============================================================
# WEB ROUTES
# ============================================================

@app.route("/health")
def health_check():
    return "OK", 200


@app.route("/")
def index():
    return redirect(url_for("dashboard" if "chat_id" in session else "login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    key = client_ip()
    if is_locked(key):
        return render_template(
            "login.html", error="Too many failed attempts. Try again in a few minutes."
        ), 429

    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    user = get_user_by_username(username)

    if not user or not check_password_hash(user["password_hash"], password):
        record_failure(key)
        return render_template("login.html", error="Invalid username or password."), 401

    LOGIN_ATTEMPTS.pop(key, None)
    session.clear()
    session.permanent = True
    session["chat_id"] = user["chat_id"]
    return redirect(url_for("dashboard"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/setup/<token>", methods=["GET", "POST"])
def setup(token):
    chat_id = read_setup_token(token)
    if chat_id is None:
        return render_template("setup.html", invalid=True), 400

    if request.method == "GET":
        return render_template("setup.html")

    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")

    error = None
    if not USERNAME_RE.match(username):
        error = "Username must be 3–20 characters: letters, numbers or underscore."
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    elif password != confirm:
        error = "Passwords do not match."
    else:
        existing = get_user_by_username(username)
        if existing and existing["chat_id"] != chat_id:
            error = "That username is taken. Please choose another."

    if error:
        return render_template("setup.html", error=error, username=username), 400

    set_credentials(chat_id, username, generate_password_hash(password))
    session.clear()
    session.permanent = True
    session["chat_id"] = chat_id
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = get_user(session["chat_id"])
    if not user or not user.get("username"):
        session.clear()
        return redirect(url_for("login"))
    return render_template("dashboard.html", username=user["username"])


@app.route("/dashboard/<int:chat_id>")
def dashboard_legacy(chat_id):
    # Old links from earlier bot messages: send everyone through the login.
    return redirect(url_for("dashboard"))


@app.route("/api/summary")
@login_required
def api_summary():
    chat_id = session["chat_id"]
    inflow, outflow, balance = get_cash_summary(chat_id)
    return jsonify({
        "inflow": inflow,
        "outflow": outflow,
        "balance": balance,
        "category_breakdown": [
            {"category": c, "type": ty, "total": tot}
            for c, ty, tot in get_category_breakdown(chat_id)
        ],
        "source_breakdown": [
            {"source": s, "count": c} for s, c in get_source_breakdown(chat_id)
        ],
        "transactions": get_all_transactions(chat_id),
    })


# ============================================================
# TELEGRAM HELPERS
# ============================================================

def send_reply(chat_id, text):
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )


def send_keyboard(chat_id, text, buttons, row_size=2):
    rows = [buttons[i:i + row_size] for i in range(0, len(buttons), row_size)]
    keyboard = [[{"text": label, "callback_data": data} for label, data in row] for row in rows]
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": keyboard}},
        timeout=10,
    )


def send_url_button(chat_id, text, label, link):
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "reply_markup": {"inline_keyboard": [[{"text": label, "url": link}]]},
        },
        timeout=10,
    )


def answer_callback(callback_query_id):
    requests.post(
        f"{TELEGRAM_API}/answerCallbackQuery",
        json={"callback_query_id": callback_query_id},
        timeout=5,
    )


def download_telegram_file(file_id):
    res = requests.get(f"{TELEGRAM_API}/getFile?file_id={file_id}", timeout=10).json()
    file_path = res["result"]["file_path"]
    return requests.get(
        f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}", timeout=15
    ).content


# ============================================================
# LANGUAGE
# ============================================================

def get_lang(chat_id):
    if chat_id in LANG_CACHE:
        return LANG_CACHE[chat_id]
    user = get_user(chat_id)
    lang = user.get("language") if user else None
    if lang in LANGS:
        LANG_CACHE[chat_id] = lang
        return lang
    return None


def show_language_picker(chat_id):
    SESSIONS.pop(chat_id, None)
    buttons = [(name, f"lang_{code}") for code, name in LANGS.items()]
    send_keyboard(chat_id, LANG_PROMPT, buttons, row_size=2)


# ============================================================
# MENUS
# ============================================================

def show_main_menu(chat_id, lang):
    SESSIONS[chat_id] = {"step": "main_menu"}
    buttons = [
        (t(lang, "btn_category"), "menu_options"),
        (t(lang, "btn_photo"), "menu_photo"),
        (t(lang, "btn_voice"), "menu_voice"),
    ]
    send_keyboard(chat_id, t(lang, "main_prompt"), buttons, row_size=1)


def show_category_menu(chat_id, lang):
    SESSIONS[chat_id] = {"step": "category"}
    buttons = [(cat_label(lang, key), f"cat_{key}") for key in CATEGORY_CONFIG]
    send_keyboard(chat_id, category_prompt(lang), buttons, row_size=2)


def show_photo_type_menu(chat_id, lang):
    SESSIONS[chat_id] = {"step": "photo_type"}
    send_keyboard(
        chat_id,
        t(lang, "photo_type_prompt"),
        [(t(lang, "btn_received"), "phototype_inflow"),
         (t(lang, "btn_spent"), "phototype_outflow")],
        row_size=1,
    )


def send_dashboard_link(chat_id, lang, reset=False):
    user = get_user(chat_id)
    has_login = bool(user and user.get("password_hash"))

    if has_login and not reset:
        send_url_button(chat_id, t(lang, "dash_login"), t(lang, "btn_dash"),
                        f"{base_url()}/login")
        return

    link = f"{base_url()}/setup/{make_setup_token(chat_id)}"
    if has_login:
        send_url_button(chat_id, t(lang, "reset_msg"), t(lang, "btn_reset"), link)
    else:
        send_url_button(chat_id, t(lang, "dash_setup"), t(lang, "btn_setup"), link)


# ============================================================
# FORMATTING
# ============================================================

def format_short_reply(parsed, lang):
    arrow = "📈" if parsed["type"] == "inflow" else "📉"
    return t(
        lang, "logged",
        arrow=arrow,
        type=t(lang, f"type_{parsed['type']}"),
        amount=parsed["amount"],
        category=cat_name(lang, parsed["category"]),
    )


def format_structured_reply(parsed, source_label, lang):
    arrow = "📈" if parsed["type"] == "inflow" else "📉"
    lines = [
        t(lang, "struct_title", source=source_label),
        f"{arrow} {t(lang, 'lbl_type')}: {t(lang, 'type_' + parsed['type'])}",
        f"{t(lang, 'lbl_amount')}: ₹{parsed['amount']}",
        f"{t(lang, 'lbl_category')}: {cat_name(lang, parsed['category'])}",
        f"{t(lang, 'lbl_note')}: {parsed.get('description') or '-'}",
    ]
    if parsed.get("needs_review"):
        lines.append("\n" + t(lang, "needs_review"))
    return "\n".join(lines)


def send_recent_rows(chat_id, lang, limit=10):
    rows = get_all_transactions(chat_id, limit=limit)
    if not rows:
        send_reply(chat_id, t(lang, "no_rows"))
        return

    lines = [t(lang, "recent_header")]
    for row in rows:
        arrow = "📈" if row["type"] == "inflow" else "📉"
        desc = f" ({row['description']})" if row.get("description") else ""
        lines.append(
            f"• #{row['id']} {arrow} ₹{row['amount']} | "
            f"{cat_name(lang, row['category'])} | {SRC_ICON.get(row['source'], '')}{desc}"
        )
    lines.append(t(lang, "recent_footer"))
    send_reply(chat_id, "\n".join(lines))


# ============================================================
# TRANSACTION HANDLERS
# ============================================================

def log_and_continue(chat_id, lang, category, amount, tx_type, source, description=None):
    if category not in CATEGORY_CONFIG:
        category = "Others"
    add_transaction(chat_id, tx_type, amount, category,
                    description=description, source=source)
    show_main_menu(chat_id, lang)


def handle_text_transaction(chat_id, lang, text):
    parsed = parse_transaction_text(text, lang)
    send_reply(chat_id, format_short_reply(parsed, lang))
    log_and_continue(chat_id, lang, parsed["category"], parsed["amount"], parsed["type"],
                     "text", parsed.get("description"))


def handle_photo(chat_id, lang, msg, known_type):
    img_bytes = download_telegram_file(msg["photo"][-1]["file_id"])
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")
    parsed = analyze_receipt_image_safe(img_b64, known_type, lang)
    send_reply(chat_id, format_structured_reply(parsed, t(lang, "src_photo"), lang))
    log_and_continue(chat_id, lang, parsed["category"], parsed["amount"], parsed["type"],
                     "photo", parsed.get("description"))


def handle_voice(chat_id, lang, msg):
    voice_bytes = download_telegram_file(msg["voice"]["file_id"])
    transcript = transcribe_voice_note(voice_bytes, lang)
    parsed = parse_transaction_text(transcript, lang)
    send_reply(chat_id, t(lang, "heard", text=transcript))
    send_reply(chat_id, format_structured_reply(parsed, t(lang, "src_voice"), lang))
    log_and_continue(chat_id, lang, parsed["category"], parsed["amount"], parsed["type"],
                     "voice", parsed.get("description"))


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    logger.info(f"RAW INCOMING: {data}")

    if "callback_query" in data:
        return handle_callback(data["callback_query"])

    msg = data.get("message")
    if not msg:
        return "OK", 200

    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

    try:
        # Language is always asked first
        if text.startswith(("/start", "/language")):
            show_language_picker(chat_id)
            return "OK", 200

        lang = get_lang(chat_id)
        if not lang:
            show_language_picker(chat_id)
            return "OK", 200

        # Commands are checked before any conversation state
        if text.startswith("/dashboard"):
            send_dashboard_link(chat_id, lang)
            return "OK", 200

        if text.startswith("/resetpassword"):
            send_dashboard_link(chat_id, lang, reset=True)
            return "OK", 200

        if text.startswith(("/transactions", "/rows")):
            send_recent_rows(chat_id, lang)
            return "OK", 200

        state = SESSIONS.get(chat_id) or {}
        step = state.get("step")

        if step == "amount" and "text" in msg:
            try:
                amount = float(text.replace(",", ""))
                if not math.isfinite(amount) or amount <= 0:
                    raise ValueError
            except ValueError:
                send_reply(chat_id, t(lang, "invalid_amount"))
                return "OK", 200

            parsed = {"type": state["type"], "amount": amount,
                      "category": state["category"], "description": None}
            send_reply(chat_id, format_short_reply(parsed, lang))
            log_and_continue(chat_id, lang, state["category"], amount, state["type"], "button")
            return "OK", 200

        if step == "others_text" and "text" in msg:
            handle_text_transaction(chat_id, lang, text)
            return "OK", 200

        if step == "awaiting_photo" and "photo" in msg:
            handle_photo(chat_id, lang, msg, state.get("photo_type", "outflow"))
            return "OK", 200

        if step == "awaiting_voice" and "voice" in msg:
            handle_voice(chat_id, lang, msg)
            return "OK", 200

        if "text" in msg:
            handle_text_transaction(chat_id, lang, text)
            return "OK", 200

        if "photo" in msg:
            show_photo_type_menu(chat_id, lang)
            send_reply(chat_id, t(lang, "photo_first"))
            return "OK", 200

        if "voice" in msg:
            handle_voice(chat_id, lang, msg)
            return "OK", 200

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        lang = LANG_CACHE.get(chat_id, "en")
        send_reply(chat_id, t(lang, "error"))
        show_main_menu(chat_id, lang)

    return "OK", 200


def handle_callback(query):
    callback_id = query["id"]
    chat_id = query["message"]["chat"]["id"]
    data = query["data"]
    answer_callback(callback_id)

    if data.startswith("lang_"):
        code = data[len("lang_"):]
        if code in LANGS:
            set_language(chat_id, code)
            LANG_CACHE[chat_id] = code
            send_reply(chat_id, t(code, "lang_set"))
            send_reply(chat_id, t(code, "welcome"))
            show_main_menu(chat_id, code)
        return "OK", 200

    lang = get_lang(chat_id)
    if not lang:
        show_language_picker(chat_id)
        return "OK", 200

    if data == "menu_options":
        show_category_menu(chat_id, lang)
        return "OK", 200

    if data == "menu_photo":
        show_photo_type_menu(chat_id, lang)
        return "OK", 200

    if data == "menu_voice":
        SESSIONS[chat_id] = {"step": "awaiting_voice"}
        send_reply(chat_id, t(lang, "voice_prompt"))
        return "OK", 200

    if data in ("phototype_inflow", "phototype_outflow"):
        tx_type = "inflow" if data == "phototype_inflow" else "outflow"
        SESSIONS[chat_id] = {"step": "awaiting_photo", "photo_type": tx_type}
        send_reply(chat_id, t(lang, "photo_in" if tx_type == "inflow" else "photo_out"))
        return "OK", 200

    state = SESSIONS.get(chat_id)
    if not state:
        return "OK", 200

    if data.startswith("cat_") and state.get("step") == "category":
        category = data[len("cat_"):]
        if category not in CATEGORY_CONFIG:
            return "OK", 200
        _, tx_type = CATEGORY_CONFIG[category]

        if category == "Others":
            state["step"] = "others_text"
            send_reply(chat_id, t(lang, "others_prompt"))
        else:
            state.update(step="amount", category=category, type=tx_type)
            send_reply(chat_id, t(
                lang, "amount_prompt",
                label=cat_label(lang, category),
                direction=t(lang, "dir_in" if tx_type == "inflow" else "dir_out"),
            ))
        return "OK", 200

    return "OK", 200

ADMIN_KEY = os.getenv("ADMIN_KEY")

@app.route("/admin/download-db")
def admin_download_db():
    key = request.args.get("key", "")
    if not ADMIN_KEY or not hmac.compare_digest(key.encode(), ADMIN_KEY.encode()):
        return "Not found", 404
    if os.getenv("DATABASE_URL") or not os.path.exists("finance.db"):
        return "No local SQLite file", 404
    return send_file("finance.db", as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
