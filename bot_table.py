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
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("BOT_TOKEN_PROGNOZ")
CHANNEL_STATS = os.getenv("CHANNEL_STATS")
CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")
LIVE_COOKIE = os.getenv("LIVE_COOKIE", "")

# ⭐ ПРАВИЛЬНАЯ ПРОВЕРКА
if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("ENV ERROR", flush=True)
    sys.exit(1)

print("ENV OK", flush=True)