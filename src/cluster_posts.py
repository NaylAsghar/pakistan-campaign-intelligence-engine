"""Step 6b: which content works, post groups (clustering) and unusual posts (anomaly detection).

Usage (run from the project root):  python src/cluster_posts.py
Input : data/processed/posts.csv
Output: data/processed/posts_clustered.csv        every post with its group and anomaly flag
        reports/unusual_posts.csv                 the most unusual posts, with the reason
        reports/post_cluster_profiles.csv         what each post group looks like
        reports/content_performance_*.csv         engagement by content type, campaign and brand (Question 1)
        images/post_clusters.png, images/engagement_by_campaign.png, images/short_vs_long.png

Engagement measures (fair across old and new videos, small and big channels):
  views_per_day = views / days since posting      like_rate = likes per 100 views
  comment_rate  = comments per 1,000 views        comments_count
"""
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

IN = "data/processed/posts.csv"
SEED = 42
if not os.path.exists(IN):
    sys.exit("Missing data/processed/posts.csv. Run python src/build_posts.py first.")
for d in ("reports", "images", "data/processed"):
    os.makedirs(d, exist_ok=True)

p = pd.read_csv(IN, encoding="utf-8-sig")
for c in ("views", "likes", "comments_count", "days_since_post", "duration_sec"):
    p[c] = pd.to_numeric(p[c], errors="coerce").fillna(0)
days = p["days_since_post"].clip(lower=1)
p["views_per_day"] = p["views"] / days
p["like_rate"] = np.where(p["views"] > 0, p["likes"] / p["views"] * 100, 0.0)
p["comment_rate"] = np.where(p["views"] > 0, p["comments_count"] / p["views"] * 1000, 0.0)
p["is_short"] = p["content_type"].str.startswith("Short")
p["format"] = np.where(p["is_short"], "Short video", "Longer video")
print(f"{len(p):,} posts from {p['brand'].nunique()} brands.\n")

# ======================================================================= QUESTION 1: what works
def table(by):
    g = p.groupby(by).agg(posts=("post_id", "size"), median_views_per_day=("views_per_day", "median"),
                          median_like_rate=("like_rate", "median"), mean_comments=("comments_count", "mean"),
                          pct_with_comments=("comments_count", lambda s: (s > 0).mean() * 100)).round(2)
    return g.sort_values("median_like_rate", ascending=False)

by_type, by_campaign, by_brand, by_format = table("content_type"), table("campaign"), table("brand"), table("format")
by_type.to_csv("reports/content_performance_by_type.csv")
by_campaign.to_csv("reports/content_performance_by_campaign.csv")
by_brand.to_csv("reports/content_performance_by_brand.csv")
by_format.to_csv("reports/content_performance_by_format.csv")

print("=" * 78); print("QUESTION 1: WHICH CONTENT ATTRACTS ATTENTION?")
for title, t in (("By format", by_format), ("By campaign", by_campaign), ("By brand", by_brand)):
    print(f"\n{title}:"); print(t.to_string())
tests = []
sh, lo = p[p["is_short"]], p[~p["is_short"]]
if len(sh) >= 10 and len(lo) >= 10:
    for m in ("like_rate", "views_per_day", "comment_rate"):
        pv = stats.mannwhitneyu(sh[m], lo[m]).pvalue
        tests.append(f"Short vs longer videos, {m}: Mann-Whitney p = {pv:.4f} "
                     f"({'significant' if pv < 0.05 else 'not significant'} at 5%)")
big = by_campaign[by_campaign["posts"] >= 15].index
if len(big) >= 2:
    for m in ("like_rate", "views_per_day"):
        pv = stats.kruskal(*[p.loc[p["campaign"] == c, m] for c in big]).pvalue
        tests.append(f"Campaigns ({len(big)} with 15+ posts), {m}: Kruskal-Wallis p = {pv:.4f} "
                     f"({'significant' if pv < 0.05 else 'not significant'} at 5%)")
print("\nSTATISTICAL TESTS:"); [print("  " + t) for t in tests]

plt.figure(figsize=(7, 4))
c = by_campaign[by_campaign["posts"] >= 10].sort_values("median_like_rate")
plt.barh([f"{i} (n={int(n)})" for i, n in zip(c.index, c["posts"])], c["median_like_rate"], color="#3b6fd4")
plt.xlabel("median likes per 100 views"); plt.title("Engagement by campaign"); plt.tight_layout()
plt.savefig("images/engagement_by_campaign.png", dpi=150); plt.close()
plt.figure(figsize=(5, 4))
plt.bar(by_format.index, by_format["median_like_rate"], color=["#3b6fd4", "#8fb0ee"][: len(by_format)])
for i, (f, r) in enumerate(by_format.iterrows()):
    plt.text(i, r["median_like_rate"], f"n={int(r['posts'])}", ha="center", va="bottom")
plt.ylabel("median likes per 100 views"); plt.title("Short vs longer videos"); plt.tight_layout()
plt.savefig("images/short_vs_long.png", dpi=150); plt.close()

# ======================================================================= post clusters
feat = pd.DataFrame({"log_views_per_day": np.log1p(p["views_per_day"]),
                     "like_rate": p["like_rate"].clip(upper=p["like_rate"].quantile(0.99)),
                     "log_comment_rate": np.log1p(p["comment_rate"]),
                     "log_comments": np.log1p(p["comments_count"])})
Z = StandardScaler().fit_transform(feat)
rows = []
for k in range(3, 9):
    lab = KMeans(k, n_init=10, random_state=SEED).fit_predict(Z)
    sz = np.bincount(lab) / len(Z) * 100
    rows.append({"k": k, "silhouette": silhouette_score(Z, lab), "smallest_pct": sz.min(), "largest_pct": sz.max()})
sel = pd.DataFrame(rows)
ok = sel[(sel["smallest_pct"] >= 2) & (sel["largest_pct"] <= 60)]
best_k = int((ok if len(ok) else sel).sort_values("silhouette", ascending=False).iloc[0]["k"])
p["cluster"] = KMeans(best_k, n_init=10, random_state=SEED).fit_predict(Z)
sel.to_csv("reports/post_cluster_k_selection.csv", index=False)

overall = p[["views_per_day", "like_rate", "comments_count"]].median()
prof, names = [], {}
for c in range(best_k):
    g = p[p["cluster"] == c]
    reach = g["views_per_day"].median() / max(overall["views_per_day"], 1e-9)
    rate = g["like_rate"].median() / max(overall["like_rate"], 1e-9)
    talk = g["comments_count"].mean() > p["comments_count"].quantile(0.9)
    parts = ["high reach" if reach > 1.5 else "low reach" if reach < 0.67 else "typical reach",
             "high like rate" if rate > 1.3 else "low like rate" if rate < 0.75 else "typical like rate"]
    names[c] = ", ".join(parts) + (", talked-about" if talk else "")
    prof.append({"cluster": c, "name": names[c], "posts": len(g), "share_pct": round(len(g) / len(p) * 100, 1),
                 "median_views_per_day": round(g["views_per_day"].median(), 1), "median_like_rate": round(g["like_rate"].median(), 2),
                 "mean_comments": round(g["comments_count"].mean(), 2), "median_days_old": int(g["days_since_post"].median()),
                 "pct_short": round(g["is_short"].mean() * 100, 1), "top_brand": g["brand"].mode().iloc[0],
                 "top_campaign": g["campaign"].mode().iloc[0]})
prof = pd.DataFrame(prof)
prof.to_csv("reports/post_cluster_profiles.csv", index=False)
p["cluster_name"] = p["cluster"].map(names)
print("\n" + "=" * 78); print(f"POST GROUPS (K-Means, k = {best_k})")
print(sel.round(3).to_string(index=False)); print()
print(prof.to_string(index=False))

# ======================================================================= unusual posts
A = pd.DataFrame({"views per day": np.log1p(p["views_per_day"]), "like rate": p["like_rate"],
                  "comments": np.log1p(p["comments_count"]), "comment rate": np.log1p(p["comment_rate"])})
zs = A.copy()
for b, idx in p.groupby("brand").groups.items():     # compare each post with its OWN brand's usual level
    sub = A.loc[idx]
    ref = sub if len(sub) >= 20 else A
    zs.loc[idx] = ((sub - ref.mean()) / ref.std().replace(0, 1e-6)).values
iso = IsolationForest(n_estimators=300, contamination=0.05, random_state=SEED).fit(zs)
p["anomaly_score"] = -iso.score_samples(zs)
p["anomaly_flag"] = iso.predict(zs) == -1


def reason(i):
    z = zs.loc[i]
    big = z[z.abs() >= 2]
    return "; ".join(f"{n} {v:+.1f} SD vs brand norm" for n, v in big.sort_values(key=abs, ascending=False).items())


p["anomaly_reason"] = [reason(i) if f else "" for i, f in zip(p.index, p["anomaly_flag"])]
p["anomaly_direction"] = ["" if not f else ("unusually high" if zs.loc[i].mean() > 0.3 else
                          "unusually low" if zs.loc[i].mean() < -0.3 else "mixed") for i, f in zip(p.index, p["anomaly_flag"])]
keep = ["post_id", "brand", "campaign", "content_type", "views", "likes", "comments_count", "days_since_post",
        "views_per_day", "like_rate", "comment_rate", "cluster", "cluster_name", "anomaly_flag", "anomaly_score",
        "anomaly_direction", "anomaly_reason", "video_title", "source_url"]
p[keep].round(3).to_csv("data/processed/posts_clustered.csv", index=False, encoding="utf-8-sig")
unusual = p[p["anomaly_flag"]].sort_values("anomaly_score", ascending=False)
unusual[keep].round(3).head(30).to_csv("reports/unusual_posts.csv", index=False, encoding="utf-8-sig")
print("\n" + "=" * 78); print(f"UNUSUAL POSTS: {len(unusual)} flagged ({len(unusual) / len(p) * 100:.1f}%)")
print(unusual["anomaly_direction"].value_counts().to_string())
for _, r in unusual.head(10).iterrows():
    print(f"\n  {r['post_id']} | {r['brand']} | {r['anomaly_direction']} | {r['video_title'][:70]}")
    print(f"      views {int(r['views']):,}, likes {int(r['likes']):,}, comments {int(r['comments_count']):,} -> {r['anomaly_reason']}")

plt.figure(figsize=(7, 5))
plt.scatter(feat["log_views_per_day"], feat["like_rate"], c=p["cluster"], cmap="tab10", s=10, alpha=0.6)
a = p[p["anomaly_flag"]]
plt.scatter(np.log1p(a["views_per_day"]), a["like_rate"].clip(upper=feat["like_rate"].max()), facecolors="none",
            edgecolors="red", s=45, label="unusual post")
plt.xlabel("log(1 + views per day)"); plt.ylabel("likes per 100 views"); plt.legend()
plt.title(f"Post groups (k = {best_k}) and unusual posts"); plt.tight_layout()
plt.savefig("images/post_clusters.png", dpi=150); plt.close()
print("\nSaved: data/processed/posts_clustered.csv, reports/*.csv, images/*.png")
