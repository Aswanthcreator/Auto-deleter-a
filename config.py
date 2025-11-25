import os
from dotenv import load_dotenv

# Load values from .env file
load_dotenv()

# Telegram API credentials (loaded from environment variables)
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
# List of channel/group IDs to monitor
# Example in .env:  CHAT_IDS=-1001111111111,-1002222222222
CHAT_IDS = list(map(int, os.getenv("CHAT_IDS", "").split(",")))

# Duration (in seconds) for each chat_id before auto-delete
# Example in .env:  ID_DUR=-1001111111111:30,-1002222222222:180
ID_DUR = {}
raw_dur = os.getenv("ID_DUR", "")

if raw_dur:
    for pair in raw_dur.split(","):
        chat_id, duration = pair.split(":")
        ID_DUR[int(chat_id)] = int(duration)

