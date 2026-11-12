"""Measure how many comments each brand channel REALLY has across its whole history.

Usage (run from the project root):  python src/census_channels.py
Prints a summary per brand and saves data/raw/channel_census.csv (one row per video).
That file is also the starting point for Step 3 (post records).
Cost: about 2 quota units per 50 videos, roughly 300 units for five brands.
"""
import html
import os
import re
import sys

import pandas as pd
from dotenv import load_dotenv
from googleapiclient.discovery import build

from config import BRANDS, LAWN_KEYWORDS

load_dotenv()
KEY = os.getenv("YOUTUBE_API_KEY")
if not KEY:
    sys.exit("YOUTUBE_API_KEY not found. Check that your .env file exists in the project root.")
youtube = build("youtube", "v3", developerKey=KEY)


def seconds(iso):
    """Convert a YouTube duration like PT1M30S into seconds."""
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def all_video_ids(channel_id):
    ch = youtube.channels().list(part="contentDetails", id=channel_id).execute()
    if not ch.get("items"):
        return []
    playlist = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None
    while True:
        resp = youtube.playlistItems().list(part="contentDetails", playlistId=playlist,
                                            maxResults=50, pageToken=token).execute()
        ids += [x["contentDetails"]["videoId"] for x in resp.get("items", [])]
        token = resp.get("nextPageToken")
        if not token:
            return ids


def video_rows(brand, ids):
    rows = []
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        resp = youtube.videos().list(part="snippet,statistics,contentDetails", id=",".join(batch)).execute()
        for v in resp.get("items", []):
            st, title = v["statistics"], html.unescape(v["snippet"]["title"])
            rows.append({
                "brand": brand, "video_id": v["id"], "title": title,
                "published_at": v["snippet"]["publishedAt"],
                "duration_sec": seconds(v["contentDetails"].get("duration")),
                "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                "comment_count": int(st["commentCount"]) if "commentCount" in st else None,
                "comments_off": "commentCount" not in st,
                "is_lawn_title": any(k in title.lower() for k in LAWN_KEYWORDS),
            })
    return rows


all_rows = []
for brand, cid in BRANDS.items():
    if cid.startswith("PASTE"):
        continue
    print(f"Reading {brand} ...")
    ids = all_video_ids(cid)
    all_rows += video_rows(brand, ids)

if not all_rows:
    sys.exit("No videos found. Check the channel IDs in src/config.py")

df = pd.DataFrame(all_rows)
df["comment_count"] = df["comment_count"].fillna(0).astype(int)
os.makedirs("data/raw", exist_ok=True)
df.to_csv("data/raw/channel_census.csv", index=False, encoding="utf-8-sig")

summary = df.groupby("brand").apply(lambda g: pd.Series({
    "videos": len(g),
    "comments_off": int(g["comments_off"].sum()),
    "total_comments": int(g["comment_count"].sum()),
    "videos_10plus_comments": int((g["comment_count"] >= 10).sum()),
    "lawn_title_videos": int(g["is_lawn_title"].sum()),
    "lawn_comments": int(g.loc[g["is_lawn_title"], "comment_count"].sum()),
    "max_on_one_video": int(g["comment_count"].max()),
}), include_groups=False)
print("\n" + summary.to_string())
print(f"\nALL BRANDS: {len(df):,} videos, {int(df['comment_count'].sum()):,} comments in total, "
      f"{int(df.loc[df['is_lawn_title'], 'comment_count'].sum()):,} of them on lawn-looking titles.")
print("\nTop 10 most-commented videos:")
top = df.sort_values("comment_count", ascending=False).head(10)
for _, r in top.iterrows():
    print(f"  {r['comment_count']:>6,} | {r['brand']} | {r['title'][:70]}")
print("\nSaved to data/raw/channel_census.csv")
