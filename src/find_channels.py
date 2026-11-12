"""Find a brand's official YouTube channel ID.

Usage (run from the project root):
    python src/find_channels.py "Sapphire Pakistan"   search by name (about 101 quota units)
    python src/find_channels.py @sapphireonline       look up by @handle (1 quota unit)

Prints: channelId | title | @handle | subscribers | videos | country
All results are also saved to data/raw/channel_candidates.txt
"""
import html
import os
import sys

from dotenv import load_dotenv
from googleapiclient.discovery import build

load_dotenv()
KEY = os.getenv("YOUTUBE_API_KEY")
if not KEY:
    sys.exit("YOUTUBE_API_KEY not found. Check that your .env file exists in the project root.")

query = " ".join(sys.argv[1:]).strip()
if not query:
    sys.exit('Usage: python src/find_channels.py "Brand name"   or   python src/find_channels.py @handle')

youtube = build("youtube", "v3", developerKey=KEY)

if query.startswith("@"):
    resp = youtube.channels().list(part="snippet,statistics", forHandle=query).execute()
else:
    search = youtube.search().list(q=query, part="snippet", type="channel", maxResults=6).execute()
    ids = [item["snippet"]["channelId"] for item in search.get("items", [])]
    resp = (youtube.channels().list(part="snippet,statistics", id=",".join(ids)).execute()
            if ids else {"items": []})

rows = []
for ch in resp.get("items", []):
    stats = ch.get("statistics", {})
    subs = "hidden" if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount", 0))
    rows.append((
        subs if isinstance(subs, int) else -1,
        f"{ch['id']} | {html.unescape(ch['snippet']['title'])} | {ch['snippet'].get('customUrl', '-')} | "
        f"{subs if subs == 'hidden' else format(subs, ',')} subscribers | "
        f"{int(stats.get('videoCount', 0)):,} videos | {ch['snippet'].get('country', '-')}",
    ))
rows.sort(reverse=True)

if not rows:
    print("No channel found. Try a different spelling, or add 'official' or 'Pakistan' to the name.")

os.makedirs("data/raw", exist_ok=True)
with open("data/raw/channel_candidates.txt", "a", encoding="utf-8") as f:
    f.write(f"\n=== {query} ===\n")
    for _, line in rows:
        print(line)
        f.write(line + "\n")
