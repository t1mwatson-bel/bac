import os
import sys
import pytz


# =====================================================================
# ENV
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv("BOT_TOKEN_PROGNOZ")

CHANNEL_PROGNOZ = os.getenv("CHAT_ID_21")
if not CHANNEL_PROGNOZ:
    CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

CHANNEL_STATS = os.getenv("CHANNEL_STATS")


def validate_env():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не задан", flush=True)
        sys.exit(1)
    if not CHANNEL_PROGNOZ:
        print("❌ CHANNEL_PROGNOZ не задан", flush=True)
        sys.exit(1)
    if not CHANNEL_STATS:
        print("❌ CHANNEL_STATS не задан", flush=True)
        sys.exit(1)


# =====================================================================
# TIME
# =====================================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")


# =====================================================================
# FILES
# =====================================================================

PREDICTIONS_FILE = "twentyone_predictions.json"
OFFSET_FILE = "telegram_offset.txt"
STATS_HTML_FILE = "stats.html"
BANK_FILE = "bank_state.json"


# =====================================================================
# BRANDING
# =====================================================================

LOGO_URL = (
    "https://raw.githubusercontent.com/t1mwatson-bel/bac/refs/heads/main/"
    "ChatGPT%20Image%205%20окт.%202026%20г.%2C%2022_04_47.png"
)

BOT_LINK = "https://t.me/+sNplytDID-NjYmEy"

BRAND_NAME = "OLD_GROUP"


# =====================================================================
# POLLING
# =====================================================================

POLL_INTERVAL = 2.0

FINALIZE_WAIT_SECONDS = 30

GAME_CYCLE = 720


# =====================================================================
# BANK / BETS
# =====================================================================

START_BALANCE = 20000

DOGON_MULT = 2.7

WIN_COEF = 1.6

DOGON_GAMES = 3

LOW_BALANCE_THRESHOLD = 5000


# =====================================================================
# SLEEP
# =====================================================================

SLEEP_HOUR = 23
SLEEP_MINUTE = 59

WAKE_HOUR = 9
WAKE_MINUTE = 0


# =====================================================================
# NIGHT CLEANUP
# =====================================================================

CLEANUP_HOUR = 3
CLEANUP_MINUTE = 0