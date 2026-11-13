"""Step 4: collect the comment text for the posts (raw version of Table B).

Usage (run from the project root):  python src/collect_comments.py
Input : data/processed/posts.csv
Output: data/raw/comments_raw.csv   (saved every 25 posts)
        data/raw/comments_progress.txt (posts already done, so you can safely run it again)

- Fetches the most relevant top-level comments, up to 300 per post.
- Skips comments written by the brand's own channel (pinned notes, replies to customers).
- Usernames are replaced by a one-way hash.
- Stops cleanly if the daily YouTube quota runs out. Run it again tomorrow to continue.
- Error messages are shortened on purpose, so your API key is never printed.
"""
import hashlib
import json
import os
import sys

import pandas as pd
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import BRANDS

POSTS = "data/processed/posts.csv"
OUT = "data/raw/comments_raw.csv"
PROGRESS = "data/raw/comments_progress.txt"
MAX_PER_POST = 300      # stops one giant post from dominating the dataset
MAX_REQUESTS = 3000     # safety limit per run (1 request = 1 quota unit)

load_dotenv()
KEY = os.getenv("YOUTUBE_API_KEY")
if not KEY:
    sys.exit("YOUTUBE_API_KEY not found. Check that your .env file exists in the project root.")
if not os.path.exists(POSTS):
    sys.exit("Missing data/processed/posts.csv. Run python src/build_posts.py first.")
youtube = build("youtube", "v3", developerKey=KEY)


def short_error(e):
    """Return only the reason (never the URL, which contains the API key)."""
    try:
        return json.loads(e.content.decode("utf-8"))["error"]["errors"][0]["reason"]
    except Exception:
        return f"HTTP {getattr(e.resp, 'status', '?')}"


posts = pd.read_csv(POSTS, encoding="utf-8-sig")
todo = posts[(posts["comments_count"] > 0) & (~posts["comments_off"].astype(bool))]
todo = todo.sort_values("comments_count", ascending=False)       # best posts first

done = set()
if os.path.exists(PROGRESS):
    done = set(open(PROGRESS, encoding="utf-8").read().split())
rows = (pd.read_csv(OUT, encoding="utf-8-sig").to_dict("records") if os.path.exists(OUT) else [])
todo = todo[~todo["video_id"].isin(done)]
print(f"{len(posts):,} posts in total, {len(todo):,} still to fetch (posts with at least 1 comment).")

requests_used = 0


def save():
    os.makedirs("data/raw", exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT, index=False, encoding="utf-8-sig")
    with open(PROGRESS, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(done)))


stopped = None
for n, (_, post) in enumerate(todo.iterrows(), 1):
    if requests_used >= MAX_REQUESTS:
        stopped = "safety limit reached for this run"
        break
    brand_channel = BRANDS.get(post["brand"])
    got, token = 0, None
    try:
        while got < MAX_PER_POST:
            requests_used += 1
            resp = youtube.commentThreads().list(
                part="snippet", videoId=post["video_id"], maxResults=100, order="relevance",
                textFormat="plainText", pageToken=token).execute()
            for item in resp.get("items", []):
                if got >= MAX_PER_POST:
                    break
                top = item["snippet"]["topLevelComment"]["snippet"]
                author = top.get("authorChannelId", {}).get("value")
                if brand_channel and author == brand_channel:
                    continue                                        # the brand's own comment
                rows.append({
                    "comment_id": item["id"], "post_id": post["post_id"], "video_id": post["video_id"],
                    "brand": post["brand"],
                    "author_hash": hashlib.sha256(top["authorDisplayName"].encode("utf-8")).hexdigest()[:10],
                    "comment_text": top["textDisplay"], "like_count": top["likeCount"],
                    "reply_count": item["snippet"]["totalReplyCount"], "published_at": top["publishedAt"],
                })
                got += 1
            token = resp.get("nextPageToken")
            if not token:
                break
        done.add(post["video_id"])
    except HttpError as e:
        reason = short_error(e)
        if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
            stopped = "YouTube daily quota used up. Run this script again tomorrow to continue."
            break
        done.add(post["video_id"])          # comments disabled or video gone: do not retry
        print(f"  skipped {post['post_id']} ({reason})")
    if n % 25 == 0:
        save()
        print(f"  {n:,}/{len(todo):,} posts done, {len(rows):,} comments so far, {requests_used:,} quota units used")

save()
out = pd.DataFrame(rows)
print("\n" + (f"STOPPED: {stopped}" if stopped else "Finished all posts."))
print(f"Comments saved: {len(out):,}   (file: {OUT})")
if len(out):
    print("\nComments per brand:")
    print(out["brand"].value_counts().to_string())
    print(f"\nPosts that have comments in the file: {out['post_id'].nunique():,}")
    print(f"Quota units used in this run: {requests_used:,}")
