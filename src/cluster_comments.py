"""Step 6a: group the comments into audience segments with K-Means (clustering).

Usage (run from the project root):  python src/cluster_comments.py
Input : data/processed/comments.csv
Output: data/processed/comment_clusters.csv       cluster of every comment
        reports/comment_cluster_summary.csv       size, top words, examples per cluster
        reports/cluster_k_selection.csv           silhouette / Davies-Bouldin for each k (for your report)
        images/comment_clusters_all.png, images/comment_clusters_shopping.png, images/cluster_k_selection.png
Two clusterings are made:
  ALL      comments with real content -> what the whole audience talks about.
           Very short generic reactions ("nice", "love it") carry no distinguishing words, so they are
           kept together as ONE segment ("Short praise and reactions") and are not clustered.
  SHOPPING only comments about price / availability / discounts / delivery / quality / size / buying
           -> the shopping needs your teacher asks about (Question 4)
Method: TF-IDF word weights -> SVD (compress) -> K-Means. k is chosen with the silhouette score,
        among the k values where no cluster is a giant blob (largest <= 45%) or tiny (smallest >= 1.5%).
Names: each cluster is named after its most DISTINCTIVE topic (highest lift). You can rename clusters
       in MY_NAMES below after reading the printout (recommended, and good for your report).
"""
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics import davies_bouldin_score, silhouette_score
from sklearn.preprocessing import Normalizer

IN = "data/processed/comments.csv"
SEED = 42
SHOPPING_TOPICS = ["price", "availability", "discount_offers", "delivery_service", "fabric_quality", "sizing"]
STOP = list(ENGLISH_STOP_WORDS | {
    "hai", "hain", "hy", "ha", "ka", "ki", "ke", "ko", "se", "sy", "ye", "yeh", "wo", "woh", "ni", "nahi", "nhi",
    "bhi", "aur", "mein", "main", "plz", "pls", "please", "plzz", "gul", "ahmed", "ahmad", "gulahmed",
    "sapphire", "khaadi", "alkaram", "sana", "safinaz", "brand", "video", "ad", "add"})
GENERIC = {"song", "songs", "love", "loved", "nice", "beautiful", "good", "best", "amazing", "awesome", "wow",
           "great", "like", "lovely", "really", "just", "music", "woww", "superb", "excellent", "gorgeous",
           "perfect", "stunning", "mashallah", "masha", "allah", "very", "so", "too", "much", "one", "it", "this"}
SHORT_NAME = "Short praise and reactions"
MY_NAMES = {}        # rename after reading the printout, e.g. {("all", 3): "Full-song requesters"}
SEGMENT_NAMES = {
    "music_song": "Song seekers", "celebrity_model": "Celebrity and model fans", "design_style": "Design admirers",
    "price": "Price-conscious shoppers", "availability": "Availability seekers", "discount_offers": "Discount seekers",
    "delivery_service": "Delivery and service concerned", "fabric_quality": "Quality-conscious buyers",
    "sizing": "Size and fit questions", "complaint_criticism": "Critics", "general_praise": "General fans",
    "chit_chat": "Casual viewers", "content_request": "Content requesters", "brand_loyalty": "Brand loyalists",
    "other": "Mixed / other"}

if not os.path.exists(IN):
    sys.exit("Missing data/processed/comments.csv. Run python src/label_comments.py first.")
os.makedirs("reports", exist_ok=True)
os.makedirs("images", exist_ok=True)

df = pd.read_csv(IN, encoding="utf-8-sig")
df["text_clean"] = df["text_clean"].fillna("").astype(str)
df = df[(~df["is_spam"]) & (~df["topic"].isin(["emoji_only", "noise", "spam"])) & (df["word_count"] >= 1)].copy()
stop_set = set(STOP) | GENERIC
df["n_content"] = df["text_clean"].apply(lambda t: sum(1 for w in t.split() if w not in stop_set and len(w) > 1))
short = df[df["n_content"] < 2]
core = df[df["n_content"] >= 2].copy()
print(f"{len(df):,} real comments: {len(core):,} have real content (clustered) and {len(short):,} "
      f"({len(short) / len(df) * 100:.0f}%) are short generic reactions (one segment).\n")
TOPIC_COLS = [c for c in df.columns if c.startswith("topic_")]


def run(data, group, k_range, min_df, svd_dim, extra_stop=()):
    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=min_df, max_df=0.5, stop_words=STOP + list(extra_stop),
                          sublinear_tf=True)
    X = vec.fit_transform(data["text_clean"])
    dim = max(2, min(svd_dim, X.shape[1] - 1, X.shape[0] - 1))
    Z = Normalizer().fit_transform(TruncatedSVD(dim, random_state=SEED).fit_transform(X))
    rows = []
    for k in k_range:
        labels = KMeans(k, n_init=10, random_state=SEED).fit_predict(Z)
        rows.append({"group": group, "k": k, "silhouette": silhouette_score(Z, labels),
                     "davies_bouldin": davies_bouldin_score(Z, labels),
                     "smallest_cluster_pct": np.bincount(labels).min() / len(Z) * 100,
                     "largest_cluster_pct": np.bincount(labels).max() / len(Z) * 100})
    sel = pd.DataFrame(rows)
    ok = sel[(sel["smallest_cluster_pct"] >= 1.5) & (sel["largest_cluster_pct"] <= 45)]
    if len(ok):
        best_k = int(ok.sort_values("silhouette", ascending=False).iloc[0]["k"])
    else:                                          # no k avoids a blob: take the most balanced one
        best_k = int(sel.sort_values("largest_cluster_pct").iloc[0]["k"])
    km = KMeans(best_k, n_init=10, random_state=SEED).fit(Z)
    terms = vec.get_feature_names_out()
    out, names = [], {}
    for c in range(best_k):
        idx = np.where(km.labels_ == c)[0]
        sub = data.iloc[idx]
        means = np.asarray(X[idx].mean(axis=0)).ravel()
        top_terms = [terms[i] for i in np.argsort(means)[::-1][:8]]
        in_cluster = sub[TOPIC_COLS].mean()                       # share of the cluster mentioning each topic
        lift = in_cluster / data[TOPIC_COLS].mean().replace(0, np.nan)   # how much more than average
        cand = lift[in_cluster >= 0.25].dropna()
        dom = cand.idxmax().replace("topic_", "") if len(cand) else "other"
        share = pd.Series({dom: float(in_cluster.get(f"topic_{dom}", 0))})
        base = MY_NAMES.get((group, c)) or SEGMENT_NAMES.get(dom, "Mixed / other")
        names[c] = base
        near = np.argsort(np.linalg.norm(Z[idx] - km.cluster_centers_[c], axis=1))[:4]
        out.append({"group": group, "cluster": c, "base_name": base, "size": len(idx),
                    "share_pct": round(len(idx) / len(data) * 100, 1), "top_terms": ", ".join(top_terms),
                    "dominant_topic": dom, "dominant_topic_pct": round(float(share.get(dom, 0)) * 100, 1),
                    "pct_positive": round((sub["sentiment"] == "positive").mean() * 100, 1),
                    "pct_negative": round((sub["sentiment"] == "negative").mean() * 100, 1),
                    "pct_buying_intent": round((sub["purchase_intent"] != "none").mean() * 100, 1),
                    "mean_likes": round(sub["like_count"].mean(), 2),
                    "examples": " || ".join(sub["comment_text"].iloc[near].str.slice(0, 90).str.replace("\n", " "))})
    summary = pd.DataFrame(out)
    dup = summary["base_name"].duplicated(keep=False)          # make duplicate names unique
    summary["name"] = [f"{b} ({t.split(', ')[0]})" if d and (group, c) not in MY_NAMES else b
                       for b, t, d, c in zip(summary["base_name"], summary["top_terms"], dup, summary["cluster"])]
    plt.figure(figsize=(7, 5))
    plt.scatter(Z[:, 0], Z[:, 1], c=km.labels_, cmap="tab10", s=10, alpha=0.7)
    plt.title(f"Comment clusters ({group}), k = {best_k}"); plt.xlabel("component 1"); plt.ylabel("component 2")
    plt.tight_layout(); plt.savefig(f"images/comment_clusters_{group}.png", dpi=150); plt.close()
    return sel, summary, pd.Series(km.labels_, index=data.index)


sel_all, sum_all, lab_all = run(core, "all", range(6, 11), min_df=3, svd_dim=100, extra_stop=GENERIC)
shop = df[df["topic"].isin(SHOPPING_TOPICS) | (df["purchase_intent"] != "none")]
print(f"{len(shop):,} comments are about shopping needs.\n")
if len(shop) >= 60:
    sel_shop, sum_shop, lab_shop = run(shop, "shopping", range(3, 7), min_df=2, svd_dim=30)
else:
    sel_shop, sum_shop, lab_shop = pd.DataFrame(), pd.DataFrame(), pd.Series(dtype=int)

short_row = pd.DataFrame([{"group": "all", "cluster": -1, "base_name": SHORT_NAME, "name": SHORT_NAME, "size": len(short),
                           "share_pct": round(len(short) / len(df) * 100, 1), "top_terms": "(generic words: nice, love, song ...)",
                           "dominant_topic": "general_praise", "dominant_topic_pct": round(float(short["topic_general_praise"].mean() * 100), 1),
                           "pct_positive": round((short["sentiment"] == "positive").mean() * 100, 1),
                           "pct_negative": round((short["sentiment"] == "negative").mean() * 100, 1),
                           "pct_buying_intent": round((short["purchase_intent"] != "none").mean() * 100, 1),
                           "mean_likes": round(short["like_count"].mean(), 2),
                           "examples": " || ".join(short["comment_text"].str.slice(0, 60).head(3))}])
sum_all = pd.concat([short_row, sum_all], ignore_index=True)
pd.concat([sel_all, sel_shop]).to_csv("reports/cluster_k_selection.csv", index=False)
pd.concat([sum_all, sum_shop]).to_csv("reports/comment_cluster_summary.csv", index=False, encoding="utf-8-sig")
res = df[["comment_id"]].copy()
res["cluster_all"] = lab_all.reindex(df.index).fillna(-1).astype(int)       # -1 = short generic reactions
res["segment_all"] = res["cluster_all"].map({**dict(zip(sum_all["cluster"], sum_all["name"])), -1: SHORT_NAME})
res["cluster_shopping"] = lab_shop.reindex(df.index)
if len(sum_shop):
    res["segment_shopping"] = res["cluster_shopping"].map(dict(zip(sum_shop["cluster"], sum_shop["name"])))
res.to_csv("data/processed/comment_clusters.csv", index=False, encoding="utf-8-sig")

plt.figure(figsize=(6, 4))
for sel, lab in ((sel_all, "all comments"), (sel_shop, "shopping comments")):
    if len(sel):
        plt.plot(sel["k"], sel["silhouette"], marker="o", label=lab)
plt.xlabel("k (number of clusters)"); plt.ylabel("silhouette score"); plt.title("Choosing k"); plt.legend()
plt.tight_layout(); plt.savefig("images/cluster_k_selection.png", dpi=150); plt.close()

sum_all.loc[sum_all["cluster"] >= 0, "share_pct"] = (sum_all.loc[sum_all["cluster"] >= 0, "size"] / len(df) * 100).round(1)
for title, sel, summ in (("ALL COMMENTS", sel_all, sum_all), ("SHOPPING-NEED COMMENTS", sel_shop, sum_shop)):
    if not len(summ):
        continue
    print("=" * 78); print(title)
    print(sel.round(3).to_string(index=False))
    print(f"-> chosen k = {int((summ['cluster'] >= 0).sum())}\n")
    for _, r in summ.iterrows():
        print(f"[{r['cluster']}] {r['name']}  |  {r['size']} comments ({r['share_pct']}%)")
        print(f"    top words : {r['top_terms']}")
        print(f"    positive {r['pct_positive']}%  negative {r['pct_negative']}%  buying intent {r['pct_buying_intent']}%")
        for ex in r["examples"].split(" || ")[:3]:
            print(f"    e.g. {ex}")
        print()
print("Saved: data/processed/comment_clusters.csv, reports/comment_cluster_summary.csv, images/*.png")
