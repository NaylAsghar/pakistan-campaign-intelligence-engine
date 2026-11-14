"""Step 5: clean the comments and label them (Table B of the brief).

Usage (run from the project root):  python src/label_comments.py
Input : data/raw/comments_raw.csv   (+ data/processed/posts.csv to link comments to posts)
Output: data/processed/comments.csv            Table B + extra columns
        data/processed/comments_review_sample.csv   100 comments to check BY HAND
Uses NO API quota.

Labels added: language_type, topic (main topic), topics_all, sentiment, purchase_intent,
              is_spam, and one True/False column per topic.
"""
import os
import re
import sys

import pandas as pd
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from text_utils import (NEUTRALIZED_WORDS, SAD_EMOJIS, SENTIMENT_EXTRAS, TOPICS, clean_text,
                        detect_language, is_spam, mask_private, primary_topic, purchase_intent,
                        topic_hits)

RAW = "data/raw/comments_raw.csv"
POSTS = "data/processed/posts.csv"
OUT = "data/processed/comments.csv"
REVIEW = "data/processed/comments_review_sample.csv"

if not os.path.exists(RAW):
    sys.exit("Missing data/raw/comments_raw.csv. Run python src/collect_comments.py first.")

df = pd.read_csv(RAW, encoding="utf-8-sig")
n_raw = len(df)

# ---- 1. cleaning steps (each one is counted for the report) --------------------------------
steps = [("Raw comments collected", n_raw)]
df = df.drop_duplicates("comment_id")
df["comment_text"] = df["comment_text"].fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
df = df[df["comment_text"].str.len() > 0]
steps.append(("After removing duplicate IDs and empty comments", len(df)))
df = df.drop_duplicates(["video_id", "comment_text"])      # copy-paste spam within one post
steps.append(("After removing repeated identical comments on the same post", len(df)))

if os.path.exists(POSTS):                                     # keep comments of current posts only
    posts = pd.read_csv(POSTS, encoding="utf-8-sig")[["video_id", "post_id"]].rename(columns={"post_id": "post_id_new"})
    df = df.merge(posts, on="video_id", how="inner")
    df["post_id"] = df["post_id_new"]
    df = df.drop(columns="post_id_new")
    steps.append(("After keeping only posts in posts.csv", len(df)))

df["comment_text"] = df["comment_text"].apply(mask_private)   # emails, phones, @handles
df["text_clean"] = df["comment_text"].apply(clean_text)
df["word_count"] = df["text_clean"].str.split().str.len().fillna(0).astype(int)

# ---- 2. labels -----------------------------------------------------------------------------------
df["language_type"] = df["comment_text"].apply(detect_language)
df["is_spam"] = df["text_clean"].apply(is_spam)
hits = df["text_clean"].apply(topic_hits)
has_letters = df["comment_text"].apply(lambda t: any(c.isalpha() for c in t))
df["topic"] = [("spam" if sp else primary_topic(h, hl, tc))
               for h, hl, sp, tc in zip(hits, has_letters, df["is_spam"], df["text_clean"])]
df["topics_all"] = hits.apply(lambda h: "|".join(t for t, n in h.items() if n > 0))
for t in TOPICS:
    df[f"topic_{t}"] = hits.apply(lambda h, t=t: h[t] > 0)
df["purchase_intent"] = [purchase_intent(tc, h["music_song"] > 0) for tc, h in zip(df["text_clean"], hits)]
df.loc[df["is_spam"], "purchase_intent"] = "none"

analyzer = SentimentIntensityAnalyzer()
analyzer.lexicon.update(SENTIMENT_EXTRAS)
for w in NEUTRALIZED_WORDS:
    analyzer.lexicon[w] = 0.0
scored = df["comment_text"].apply(lambda t: re.sub(f"[{SAD_EMOJIS}]", " ", t))
df["compound"] = scored.apply(lambda t: analyzer.polarity_scores(t)["compound"])
df["sentiment"] = df["compound"].apply(lambda c: "positive" if c >= 0.05 else ("negative" if c <= -0.05 else "neutral"))
df["data_origin"] = "real (YouTube Data API v3, public comments, usernames hashed)"

# ---- 3. save -----------------------------------------------------------------------------------------
cols = (["comment_id", "post_id", "brand", "comment_text", "language_type", "topic", "sentiment",
         "purchase_intent", "topics_all", "compound", "is_spam", "like_count", "reply_count", "published_at",
         "word_count", "text_clean", "author_hash", "video_id", "data_origin"] + [f"topic_{t}" for t in TOPICS])
os.makedirs("data/processed", exist_ok=True)
df[cols].to_csv(OUT, index=False, encoding="utf-8-sig")
df.sample(min(100, len(df)), random_state=3)[
    ["comment_id", "comment_text", "language_type", "topic", "sentiment", "purchase_intent"]
].assign(topic_correct="", sentiment_correct="", intent_correct="").to_csv(REVIEW, index=False, encoding="utf-8-sig")

# ---- 4. summary ------------------------------------------------------------------------------------------
def pct(s):
    return (s.value_counts(normalize=True) * 100).round(1).to_string()

print("CLEANING STEPS")
for label, n in steps:
    print(f"  {n:>6,}  {label}")
print(f"\nSaved {len(df):,} comments to {OUT}  ({int(df['is_spam'].sum())} flagged as spam)\n")
real = df[~df["is_spam"]]
print("LANGUAGE (%):");        print(pct(real["language_type"]))
print("\nMAIN TOPIC (%):");    print(pct(real["topic"]))
print("\nSENTIMENT (%):");     print(pct(real["sentiment"]))
print("\nPURCHASE INTENT (%):"); print(pct(real["purchase_intent"]))
print("\nCOMMENTS PER BRAND:"); print(real["brand"].value_counts().to_string())
print(f"\nNow open {REVIEW} and check 100 comments by hand (see the guide).")
