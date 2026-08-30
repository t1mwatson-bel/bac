import os
import sys
import requests
import json
import re
import time
from datetime import datetime, timedelta
import pytz

print("START", flush=True)

# ========== ENV ==========
TOKEN = os.getenv("BOT_TOKEN") or os.getenv("BOT_TOKEN_PROGNOZ")
CH_STATS = os.getenv("CHANNEL_STATS")
CH_PROG = os.getenv("CHANNEL_PROGNOZ")

if not TOKEN or not CH_STATS or not CH_PROG:
    print("ENV ERROR", flush=True)
    sys.exit(1)

print("ENV OK", flush=True)

# ========== SETTINGS ==========
OFFSET = 1
DOGON = 4
MIN_CONF = 28.0

# ========== RANK TABLE ==========
RANKS = {
    "93-95": {"10":18,"J":16,"Q":15,"K":14,"A":13,"9":12,"8":12,"7":0,"6":0},
    "95-97": {"J":18,"Q":16,"K":15,"A":14,"10":13,"9":12,"8":12,"7":0,"6":0},
    "97-99": {"K":18,"A":17,"Q":16,"J":15,"10":14,"9":10,"8":10,"7":0,"6":0},
    "99-101": {"A":19,"K":18,"Q":16,"J":15,"10":14,"9":10,"8":8,"7":0,"6":0},
    "101-103": {"Q":18,"K":17,"A":16,"J":15,"10":14,"9":10,"8":10,"7":0,"6":0},
    "103-105": {"K":19,"Q":18,"A":17,"J":16,"10":15,"9":8,"8":7,"7":0,"6":0},
    "105+": {"A":20,"K":19,"Q":18,"J":17,"10":16,"9":5,"8":5,"7":0,"6":0},
}

print("SETUP OK", flush=True)

# ========== TELEGRAM ==========
def send(txt):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CH_PROG, "text": txt, "parse_mode": "HTML"},
            timeout=10
        )
        if r.status_code == 200:
            return r.json()["result"]["message_id"]
    except:
        pass
    return None

def edit(mid, txt):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/editMessageText",
            json={"chat_id": CH_PROG, "message_id": mid, "text": txt, "parse_mode": "HTML"},
            timeout=10
        )
        return r.status_code == 200
    except:
        return False

def get_updates(offset):
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35
        )
        return r.json() if r.status_code == 200 else {}
    except:
        return {}

print("TELEGRAM OK", flush=True)

# ========== MAIN ==========
def main():
    print("BOT STARTED", flush=True)
    send("✅ BOT STARTED")
    while True:
        time.sleep(5)
        print("WORKING", flush=True)

if __name__ == "__main__":
    main()