"""Step 3: turn the channel census into the posts table (Table A of the brief).

Usage (run from the project root):  python src/build_posts.py
Input : data/raw/channel_census.csv   (made by census_channels.py)
Output: data/processed/posts.csv              Table A + provenance columns
        data/processed/posts_review_sample.csv  50 random posts to check BY HAND
Uses NO API quota.
"""
import os
import re
import sys

import pandas as pd

CENSUS = "data/raw/channel_census.csv"
OUT = "data/processed/posts.csv"
REVIEW = "data/processed/posts_review_sample.csv"

if not os.path.exists(CENSUS):
    sys.exit("Missing data/raw/channel_census.csv. Run python src/census_channels.py first.")

df = pd.read_csv(CENSUS, encoding="utf-8-sig")
title = df["title"].fillna("").str.lower()

# ---- 1. Keep women's lawn related posts only ------------------------------------------
NOT_WOMENS_LAWN = re.compile(r"\bmen(?:'s|s)?\b|menswear|\bman\b|\bkids?\b|\bboys?\b|bridal|wedding|groom|winter|"
                             r"perfume|fragrance|shoes|footwear|"
                             r"\bwest\b|saree|\bhome\b")   # added after the hand-check: Sapphire WEST, Man, Saree, Home
STRICT = re.compile(r"\blawn|unstitched")                      # title says lawn / unstitched
SEASONAL = re.compile(r"summer|spring|\beid\b|festive|11\.11|12\.12")   # lawn season campaigns

df["lawn_match"] = None
df.loc[title.str.contains(SEASONAL), "lawn_match"] = "seasonal_campaign"
df.loc[title.str.contains(STRICT), "lawn_match"] = "title_lawn"      # strict wins
keep = df["lawn_match"].notna() & ~title.str.contains(NOT_WOMENS_LAWN)
total_videos = len(df)
df = df[keep].copy()
title = df["title"].str.lower()
print(f"{total_videos:,} channel videos -> {len(df):,} women's lawn related posts kept")


# ---- 2. Campaign (rule-based, first match wins) -----------------------------------------
def campaign(t):
    if re.search(r"11\.11", t): return "11.11 Sale"
    if re.search(r"12\.12", t): return "12.12 Sale"
    if re.search(r"\beid\b", t): return "Eid Collection"
    if "festive" in t: return "Festive Collection"
    if re.search(r"\b(sale|discount|off|clearance)\b", t): return "Sale"
    if "spring" in t and "summer" in t: return "Spring/Summer Lawn"
    if "summer" in t: return "Summer Lawn"
    if "spring" in t: return "Spring Lawn"
    return "General Lawn Collection"


# ---- 3. Content type (rule-based, first match wins) --------------------------------------
def content_type(t, seconds):
    if seconds <= 60: return "Short video (Reel-style)"
    if re.search(r"\bbts\b|behind the scenes|making of", t): return "Behind the scenes"
    if re.search(r"\sx\s|\bft\.?\s|\bfeat|featuring|ambassador", t): return "Influencer / celebrity"
    if re.search(r"\b(sale|discount|off|clearance)\b", t): return "Sale announcement"
    if re.search(r"campaign|\btvc\b|commercial|\bfilm\b|\bad\b", t): return "Campaign film"
    if re.search(r"styling|style|how to|tips|tutorial", t): return "Styling video"
    if re.search(r"lookbook|collection|\bvol|volume|launch|unstitched", t): return "Collection showcase"
    return "Other brand video"


df["campaign"] = title.apply(campaign)
df["content_type"] = [content_type(t, s) for t, s in zip(title, df["duration_sec"])]

# ---- 4. Build Table A -----------------------------------------------------------------------
today = pd.Timestamp.now(tz="UTC").normalize()
published = pd.to_datetime(df["published_at"], utc=True)
df = df.assign(published=published).sort_values(["brand", "published"]).reset_index(drop=True)

posts = pd.DataFrame({
    "post_id": [f"P{i:04d}" for i in range(1, len(df) + 1)],
    "brand": df["brand"],
    "platform": "YouTube",
    "campaign": df["campaign"],
    "content_type": df["content_type"],
    "product_category": "Lawn suit",
    "likes": df["likes"],
    "comments_count": df["comment_count"],       # YouTube's count usually includes replies
    "shares": pd.NA,                             # not available through the YouTube API
    "views": df["views"],
    "days_since_post": (today - df["published"]).dt.days,
    # provenance (the brief asks for source, date and real vs simulated data)
    "video_id": df["video_id"],
    "video_title": df["title"],
    "source_url": "https://www.youtube.com/watch?v=" + df["video_id"],
    "published_at": df["published_at"],
    "duration_sec": df["duration_sec"],
    "comments_off": df["comments_off"],
    "lawn_match": df["lawn_match"],
    "collection_date": today.date().isoformat(),
    "data_origin": "real (YouTube Data API v3, public data)",
})
os.makedirs("data/processed", exist_ok=True)
posts.to_csv(OUT, index=False, encoding="utf-8-sig")

# a sample YOU must check by hand (accuracy of the filter and labels goes in your report)
posts.sample(min(50, len(posts)), random_state=1)[
    ["post_id", "brand", "video_title", "lawn_match", "campaign", "content_type"]
].assign(correct_lawn_post="").to_csv(REVIEW, index=False, encoding="utf-8-sig")

# ---- 5. Summary --------------------------------------------------------------------------------
print(f"\nSaved {len(posts):,} posts to {OUT}\n")
print("Posts per brand:");            print(posts["brand"].value_counts().to_string())
print("\nLawn match type:");          print(posts["lawn_match"].value_counts().to_string())
print("\nContent types:");            print(posts["content_type"].value_counts().to_string())
print("\nCampaigns:");                print(posts["campaign"].value_counts().to_string())
print(f"\nComments on these posts (YouTube count): {int(posts['comments_count'].sum()):,}")
print(f"Posts with 10+ comments: {int((posts['comments_count'] >= 10).sum())}")
print(f"\nNow open {REVIEW} and mark 50 rows correct / wrong (see the guide).")
