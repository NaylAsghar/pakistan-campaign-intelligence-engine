"""Check every channel in src/config.py BEFORE collecting data.

Usage (run from the project root):  python src/check_channels.py
For each brand it looks at the 50 most recent videos and reports:
  - how many have comments turned ON
  - how many comments they hold in total
  - how many titles look like lawn / seasonal collection content
Costs about 3 quota units per brand.
"""
import html
import os
import sys

from dotenv import load_dotenv
from googleapiclient.discovery import build

from config import BRANDS, LAWN_KEYWORDS

load_dotenv()
KEY = os.getenv("YOUTUBE_API_KEY")
if not KEY:
    sys.exit("YOUTUBE_API_KEY not found. Check that your .env file exists in the project root.")
youtube = build("youtube", "v3", developerKey=KEY)


def check(brand, channel_id):
    if channel_id.startswith("PASTE"):
        print(f"[ -- ] {brand}: channel ID not filled in yet")
        return
    if not (channel_id.startswith("UC") and len(channel_id) == 24):
        print(f"[FAIL] {brand}: '{channel_id}' is not a channel ID (they start with UC and have 24 characters)")
        return

    resp = youtube.channels().list(part="snippet,statistics,contentDetails", id=channel_id).execute()
    if not resp.get("items"):
        print(f"[FAIL] {brand}: channel '{channel_id}' NOT FOUND")
        return
    ch = resp["items"][0]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    title = html.unescape(ch["snippet"]["title"])
    total_videos = int(ch["statistics"].get("videoCount", 0))
    subs = ch["statistics"].get("subscriberCount", "hidden")

    pl = youtube.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50).execute()
    video_ids = [x["contentDetails"]["videoId"] for x in pl.get("items", [])]
    if not video_ids:
        print(f"[FAIL] {brand}: channel '{title}' has no public videos")
        return

    videos = youtube.videos().list(part="snippet,statistics", id=",".join(video_ids)).execute()["items"]
    n = len(videos)
    with_comments = [v for v in videos if "commentCount" in v["statistics"]]   # missing = comments off
    total_comments = sum(int(v["statistics"]["commentCount"]) for v in with_comments)
    lawn = [v for v in videos if any(k in v["snippet"]["title"].lower() for k in LAWN_KEYWORDS)]
    share = len(with_comments) / n * 100

    problems = []
    if share < 60:
        problems.append("comments are OFF on many videos")
    if total_comments < 100:
        problems.append("very few comments")
    tag = "[ OK ]" if not problems else "[WARN]"
    print(f"{tag} {brand}: '{title}' | {int(subs):,} subscribers | {total_videos:,} videos in total"
          if str(subs).isdigit() else f"{tag} {brand}: '{title}' | {total_videos:,} videos in total")
    print(f"         last {n} videos: {len(with_comments)} have comments on ({share:.0f}%), "
          f"{total_comments:,} comments in total, {len(lawn)} titles look like lawn/collection content")
    if problems:
        print(f"         problem: {', '.join(problems)}")


for brand, cid in BRANDS.items():
    check(brand, cid)
