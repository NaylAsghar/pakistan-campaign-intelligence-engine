"""Pakistan Social Audience and Campaign Intelligence Engine: Streamlit web app.

Run from the project root:   streamlit run app/app.py
Reads the CSV files made by the pipeline scripts (build_posts, collect_comments, label_comments,
cluster_comments, cluster_posts). Nothing is downloaded here: all data is already in your repo.
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
PROC, REP = ROOT / "data" / "processed", ROOT / "reports"

# ---- FILL THESE IN after your hand-checks (they appear on the "Data and method" page) ----------
VALIDATION = {
    "posts": "e.g. 35 of 50 sampled posts clearly correct (70%); 88% of the 40 decidable ones",
    "comments_topic": None,        # e.g. "41 of 50 correct (82%)"
    "comments_sentiment": None,    # e.g. "44 of 50 correct (88%)"
}

st.set_page_config(page_title="Pakistan Campaign Intelligence Engine", page_icon="📊", layout="wide")

SHOPPING = ["price", "availability", "discount_offers", "delivery_service", "fabric_quality", "sizing"]
REACTIONS = ["music_song", "celebrity_model", "design_style", "general_praise", "brand_loyalty",
             "content_request", "chit_chat"]
NICE = {"music_song": "Song and music", "celebrity_model": "Celebrity / model", "design_style": "Design and style",
        "general_praise": "General praise", "brand_loyalty": "Brand loyalty", "content_request": "Content requests",
        "chit_chat": "Chit-chat", "price": "Price", "availability": "Availability", "discount_offers": "Discounts and sale",
        "delivery_service": "Delivery and service", "fabric_quality": "Fabric quality", "sizing": "Sizing",
        "complaint_criticism": "Complaints and criticism", "other": "Other", "noise": "Noise", "emoji_only": "Emoji only",
        "spam": "Spam"}


def group_of(topic):
    return ("Shopping needs" if topic in SHOPPING else "Campaign reactions" if topic in REACTIONS
            else "Complaints and criticism" if topic == "complaint_criticism" else "Other / noise")


# ------------------------------------------------------------------------------------------ data
@st.cache_data
def load():
    def read(path):
        return pd.read_csv(path, encoding="utf-8-sig") if path.exists() else None
    posts, com = read(PROC / "posts_clustered.csv"), read(PROC / "comments.csv")
    clusters, csum = read(PROC / "comment_clusters.csv"), read(REP / "comment_cluster_summary.csv")
    raw_posts = read(PROC / "posts.csv")
    if com is not None and clusters is not None:
        com = com.merge(clusters.drop(columns=[c for c in clusters.columns if c in com.columns and c != "comment_id"]),
                        on="comment_id", how="left")
    return posts, com, csum, raw_posts


posts, com, csum, raw_posts = load()
missing = [n for n, d in (("data/processed/posts_clustered.csv  (run src/cluster_posts.py)", posts),
                          ("data/processed/comments.csv  (run src/label_comments.py)", com)) if d is None]
if missing:
    st.error("Some data files are missing. Run the pipeline scripts first:\n\n- " + "\n- ".join(missing))
    st.stop()

real = com[~com["is_spam"]].copy()
real["group"] = real["topic"].apply(group_of)
n_posts, n_real = len(posts), len(real)
shop_share = real["topic"].isin(SHOPPING).mean() * 100


def pct(x):
    return f"{x:.1f}%"


# ------------------------------------------------------------------------------------------ sidebar
st.sidebar.title("📊 Campaign Intelligence Engine")
st.sidebar.caption("Pakistani women's lawn brands on YouTube")
PAGES = ["Overview", "1. What content attracts attention", "2. What the audience wants",
         "3. Unusual patterns", "4. Audience segments", "5. Recommendations", "Data and method"]
page = st.sidebar.radio("Go to", PAGES)
st.sidebar.markdown("---")
st.sidebar.caption(f"{n_posts:,} posts · {n_real:,} comments · {posts['brand'].nunique()} brands")

# ------------------------------------------------------------------------------------------ OVERVIEW
if page == "Overview":
    st.title("Pakistan Social Audience and Campaign Intelligence Engine")
    st.markdown("**Question:** what type of content gets Pakistani audiences to engage, what do they want, "
                "and what should a lawn brand post next?  \nClustering and anomaly detection on public "
                "YouTube posts and comments of five Pakistani lawn brands.")
    c = st.columns(5)
    c[0].metric("Posts analysed", f"{n_posts:,}")
    c[1].metric("Comments analysed", f"{n_real:,}")
    c[2].metric("Brands", posts["brand"].nunique())
    c[3].metric("Positive comments", pct((real['sentiment'] == 'positive').mean() * 100))
    c[4].metric("Shopping-need comments", pct(shop_share))
    top = real["topic"].value_counts()
    top = top[~top.index.isin(["other", "noise", "emoji_only"])]
    st.subheader("Headline findings")
    st.markdown(
        f"- The biggest topic in comments is **{NICE.get(top.index[0], top.index[0])}** "
        f"({pct(top.iloc[0] / n_real * 100)} of comments).\n"
        f"- Only **{pct(shop_share)}** of comments talk about shopping needs (price, availability, discounts, "
        f"delivery, quality, size).\n"
        f"- **{pct((posts['comments_count'] > 0).mean() * 100)}** of posts have any comments at all: "
        f"conversation is concentrated on a few posts.\n"
        f"- **{int(posts['anomaly_flag'].sum())}** posts are flagged as unusual compared with their own brand.")
    st.subheader("The five questions")
    st.table(pd.DataFrame({"Question": ["1. Which content attracts attention?", "2. What does the audience want?",
                                        "3. Are there unusual patterns?", "4. What audience needs and segments exist?",
                                        "5. What should we do next?"],
                           "Method": ["Engagement per view by format, campaign and brand + statistical tests",
                                      "Rule-based topics, sentiment and purchase intent on every comment",
                                      "Isolation Forest on each post versus its own brand",
                                      "TF-IDF + K-Means clustering of comments, K-Means on posts",
                                      "Rule-based engine that turns the evidence into actions"]}))
    st.info("Use the sidebar to open each question. All data is public YouTube data, usernames are hashed.")

# ------------------------------------------------------------------------------------------ Q1
elif page == PAGES[1]:
    st.title("1. Which content attracts attention?")
    st.caption("Engagement is compared per view, so old and new videos and big and small channels are fair: "
               "like rate = likes per 100 views, comment rate = comments per 1,000 views.")
    brands = st.multiselect("Brands", sorted(posts["brand"].unique()), default=sorted(posts["brand"].unique()))
    p = posts[posts["brand"].isin(brands)].copy()
    p["format"] = np.where(p["content_type"].str.startswith("Short"), "Short video", "Longer video")
    metric = st.radio("Measure", ["like_rate", "views_per_day", "comment_rate"], horizontal=True,
                      format_func={"like_rate": "Likes per 100 views", "views_per_day": "Views per day",
                                   "comment_rate": "Comments per 1,000 views"}.get)

    def bar(by, title, min_n=10):
        g = p.groupby(by).agg(posts=("post_id", "size"), value=(metric, "median")).reset_index()
        g = g[g["posts"] >= min_n].sort_values("value")
        g["label"] = g[by] + " (n=" + g["posts"].astype(str) + ")"
        fig = px.bar(g, x="value", y="label", orientation="h", title=title, text=g["value"].round(2))
        fig.update_layout(yaxis_title="", xaxis_title=f"median {metric.replace('_', ' ')}", height=380)
        return fig

    a, b = st.columns(2)
    a.plotly_chart(bar("campaign", "By campaign (median)"))
    b.plotly_chart(bar("format", "Short vs longer videos (median)", 1))
    st.plotly_chart(bar("brand", "By brand (median)"))
    sh, lo = p[p["format"] == "Short video"], p[p["format"] == "Longer video"]
    st.subheader("Statistical tests")
    lines = []
    if len(sh) >= 10 and len(lo) >= 10:
        pv = stats.mannwhitneyu(sh[metric], lo[metric]).pvalue
        lines.append(f"Short vs longer videos: Mann-Whitney p = {pv:.4f} → "
                     f"{'**significant**' if pv < 0.05 else 'not significant'} at 5%. "
                     f"Median {sh[metric].median():.2f} (short) vs {lo[metric].median():.2f} (longer).")
    big = p["campaign"].value_counts()
    big = big[big >= 15].index
    if len(big) >= 2:
        pv = stats.kruskal(*[p.loc[p["campaign"] == c, metric] for c in big]).pvalue
        lines.append(f"Campaigns ({len(big)} with 15+ posts): Kruskal-Wallis p = {pv:.4f} → "
                     f"{'**significant**' if pv < 0.05 else 'not significant'} at 5%.")
    st.markdown("\n\n".join(lines) if lines else "Not enough posts for a test with this selection.")
    st.caption("A p-value below 0.05 means the difference is unlikely to be chance. 'Not significant' means "
               "we cannot claim one type works better.")
    with st.expander("Top 20 posts by this measure"):
        st.dataframe(p.sort_values(metric, ascending=False).head(20)[
            ["brand", "campaign", "content_type", "views", "likes", "comments_count", metric, "video_title", "source_url"]],
            column_config={"source_url": st.column_config.LinkColumn("video")})

# ------------------------------------------------------------------------------------------ Q2
elif page == PAGES[2]:
    st.title("2. What does the audience want?")
    show_noise = st.checkbox("Include emoji-only and noise comments", value=False)
    d = real if show_noise else real[~real["topic"].isin(["emoji_only", "noise"])]
    t = d["topic"].value_counts(normalize=True).mul(100).rename_axis("topic").reset_index(name="pct")
    t["group"], t["name"] = t["topic"].apply(group_of), t["topic"].map(NICE)
    fig = px.bar(t.sort_values("pct"), x="pct", y="name", color="group", orientation="h",
                 title=f"Main topic of {len(d):,} comments (%)", text=t.sort_values("pct")["pct"].round(1))
    fig.update_layout(yaxis_title="", xaxis_title="% of comments", height=520)
    st.plotly_chart(fig)
    grp = d["group"].value_counts(normalize=True).mul(100).round(1)
    st.markdown(" · ".join(f"**{k}**: {v}%" for k, v in grp.items()))
    st.subheader("Topics by brand")
    ct = pd.crosstab(d["brand"], d["topic"].map(NICE), normalize="index").mul(100).round(1)
    st.plotly_chart(px.imshow(ct, text_auto=True, aspect="auto", color_continuous_scale="Blues",
                              title="% of each brand's comments by topic"))
    a, b = st.columns(2)
    sen = pd.crosstab(d["topic"].map(NICE), d["sentiment"], normalize="index").mul(100).round(1)
    a.plotly_chart(px.bar(sen, barmode="stack", title="Sentiment within each topic (%)",
                          color_discrete_map={"positive": "#2e9e5b", "neutral": "#9aa0a6", "negative": "#d64545"}))
    b.plotly_chart(px.pie(d["language_type"].value_counts().reset_index(), names="language_type", values="count",
                          title="Language of comments"))
    inten = d["purchase_intent"].value_counts(normalize=True).mul(100).round(1)
    st.markdown(f"**Purchase intent:** high {inten.get('high', 0)}% · medium {inten.get('medium', 0)}% · "
                f"none {inten.get('none', 0)}%")
    st.subheader("Read real comments")
    c1, c2, c3 = st.columns(3)
    topic_pick = c1.selectbox("Topic", ["(all)"] + sorted(d["topic"].unique()), format_func=lambda x: NICE.get(x, x))
    sent_pick = c2.selectbox("Sentiment", ["(all)", "positive", "neutral", "negative"])
    lang_pick = c3.selectbox("Language", ["(all)"] + sorted(d["language_type"].unique()))
    e = d
    if topic_pick != "(all)": e = e[e["topic"] == topic_pick]
    if sent_pick != "(all)": e = e[e["sentiment"] == sent_pick]
    if lang_pick != "(all)": e = e[e["language_type"] == lang_pick]
    st.caption(f"{len(e):,} matching comments (showing 15 random ones)")
    st.dataframe(e.sample(min(15, len(e)), random_state=int(len(e)))[
        ["brand", "comment_text", "topic", "sentiment", "purchase_intent", "like_count"]] if len(e) else e)

# ------------------------------------------------------------------------------------------ Q3
elif page == PAGES[3]:
    st.title("3. Are there unusual patterns?")
    st.markdown("Each post is compared with **its own brand's** usual level of views per day, like rate, comments "
                "and comment rate. An **Isolation Forest** (an anomaly-detection method) flags the 5% most "
                "different posts. 'Unusually high' means far above the brand norm, 'unusually low' far below.")
    flagged = posts[posts["anomaly_flag"]].copy()
    c1, c2 = st.columns(2)
    dirs = c1.multiselect("Direction", sorted(flagged["anomaly_direction"].dropna().unique()),
                          default=sorted(flagged["anomaly_direction"].dropna().unique()))
    brs = c2.multiselect("Brand", sorted(posts["brand"].unique()), default=sorted(posts["brand"].unique()))
    f = flagged[flagged["anomaly_direction"].isin(dirs) & flagged["brand"].isin(brs)]
    allp = posts[posts["brand"].isin(brs)].copy()
    allp["status"] = np.where(allp["anomaly_flag"], "unusual", "normal")
    fig = px.scatter(allp, x="views_per_day", y="like_rate", color="status", log_x=True, hover_name="video_title",
                     hover_data=["brand", "comments_count"], opacity=0.6,
                     color_discrete_map={"normal": "#9aa0a6", "unusual": "#d64545"},
                     title="Views per day vs likes per 100 views")
    st.plotly_chart(fig)
    st.markdown(f"**{len(f)} unusual posts** match your filters (of {int(posts['anomaly_flag'].sum())} flagged in total).")
    st.dataframe(f.sort_values("anomaly_score", ascending=False)[
        ["brand", "campaign", "anomaly_direction", "views", "likes", "comments_count", "anomaly_reason", "video_title",
         "source_url"]].head(40), column_config={"source_url": st.column_config.LinkColumn("video")})
    st.caption("Open the video link and look at the post: why did it behave differently (a giveaway, a celebrity, "
               "a different format)? Describe two or three of them in your report.")

# ------------------------------------------------------------------------------------------ Q4
elif page == PAGES[4]:
    st.title("4. What audience needs and segments exist?")
    if "segment_all" not in real.columns:
        st.warning("Run python src/cluster_comments.py first to create the segments.")
    else:
        st.markdown("**Audience map.** Comments with real content were grouped by K-Means (a clustering method); "
                    "very short reactions such as 'nice' form one segment.")
        s = real["segment_all"].value_counts().reset_index()
        s.columns = ["segment", "comments"]
        st.plotly_chart(px.bar(s.sort_values("comments"), x="comments", y="segment", orientation="h", text="comments"))
        pick = st.selectbox("Look inside a segment", s["segment"])
        sub = real[real["segment_all"] == pick]
        c = st.columns(4)
        c[0].metric("Comments", f"{len(sub):,}")
        c[1].metric("Positive", pct((sub["sentiment"] == "positive").mean() * 100))
        c[2].metric("Negative", pct((sub["sentiment"] == "negative").mean() * 100))
        c[3].metric("Buying intent", pct((sub["purchase_intent"] != "none").mean() * 100))
        if csum is not None:
            row = csum[(csum["group"] == "all") & (csum["name"] == pick)]
            if len(row):
                st.markdown(f"**Top words:** {row.iloc[0]['top_terms']}")
        st.dataframe(sub.sample(min(10, len(sub)), random_state=1)[["brand", "comment_text", "sentiment"]])
        st.markdown("---")
        st.markdown("**Shopping needs.** Only comments about price, availability, discounts, delivery, quality, "
                    "size or buying were clustered again.")
        if "segment_shopping" in real.columns and real["segment_shopping"].notna().any():
            s2 = real["segment_shopping"].value_counts().reset_index()
            s2.columns = ["segment", "comments"]
            st.plotly_chart(px.bar(s2.sort_values("comments"), x="comments", y="segment", orientation="h", text="comments"))
            pick2 = st.selectbox("Look inside a shopping segment", s2["segment"])
            sub2 = real[real["segment_shopping"] == pick2]
            st.markdown(f"**{len(sub2)} comments** · buying intent {pct((sub2['purchase_intent'] != 'none').mean() * 100)} · "
                        f"negative {pct((sub2['sentiment'] == 'negative').mean() * 100)}")
            st.dataframe(sub2.sample(min(10, len(sub2)), random_state=1)[["brand", "comment_text", "sentiment"]])
    st.markdown("---")
    st.markdown("**Post groups.** Posts were clustered on views per day, like rate and comments.")
    prof = posts.groupby("cluster_name").agg(posts=("post_id", "size"), median_views_per_day=("views_per_day", "median"),
                                             median_like_rate=("like_rate", "median"),
                                             mean_comments=("comments_count", "mean")).round(2).reset_index()
    st.dataframe(prof.sort_values("posts", ascending=False))

# ------------------------------------------------------------------------------------------ Q5
elif page == PAGES[5]:
    st.title("5. What should the brand do next?")
    st.caption("Every recommendation below is generated from the data in this app. It shows evidence, an action, "
               "and a way to test it. Evidence shows association, not proof of cause.")

    def has(pattern):
        return int(real["comment_text"].str.lower().str.contains(pattern, regex=True).sum())

    recs = []

    def add(area, title, evidence, action, test, strength):
        prio = "High" if strength >= 100 else "Medium" if strength >= 30 else "Low"
        recs.append((area, prio, title, evidence, action, test))

    # content: songs
    music = int(real["topic_music_song"].sum())
    asks = has(r"song name|name of (?:the )?song|singer|full (?:song|version)|release|lyrics|ganey ka naam|gane ka naam")
    add("Content", "Credit the song and singer, and release the full version",
        [f"{pct(music / n_real * 100)} of comments are about the song or music ({music:,} comments).",
         f"{asks:,} comments ask for the song name, the singer, or the full version."],
        "Put the song and singer name in every video description and pinned comment. Release a full-length version.",
        "Compare comment rate and like rate on videos with and without song credit.", music)
    # content: celebrities
    names = ["yumna", "ayeza", "aiza", "iqra", "mahira", "hania", "sajal", "ramsha", "kinza", "mawra", "kubra", "saba", "shuja"]
    cnt = {nm: int(real["text_clean"].str.contains(rf"\b{nm}\b", regex=True).sum()) for nm in names}
    topn = sorted(cnt.items(), key=lambda x: -x[1])[:3]
    celeb = int(real["topic_celebrity_model"].sum())
    add("Content", "Keep featuring the celebrities the audience names",
        [f"{celeb:,} comments mention models or celebrities ({pct(celeb / n_real * 100)}).",
         "Most named: " + ", ".join(f"{n.title()} ({c})" for n, c in topn)],
        "Name the featured celebrity in the title and thumbnail of the next campaign video.",
        "Compare views per day for videos that name a celebrity in the title versus those that do not.", celeb)
    # content: format
    p = posts.copy()
    p["short"] = p["content_type"].str.startswith("Short")
    if p["short"].sum() >= 10 and (~p["short"]).sum() >= 10:
        a, b = p.loc[p["short"], "like_rate"], p.loc[~p["short"], "like_rate"]
        pv = stats.mannwhitneyu(a, b).pvalue
        better = "longer videos" if b.median() > a.median() else "Short videos"
        add("Content", f"Format: {better} earn the higher like rate",
            [f"Median likes per 100 views: Short {a.median():.2f} vs longer {b.median():.2f} "
             f"({'significant' if pv < 0.05 else 'not significant'}, p = {pv:.3f}).",
             f"{pct(p['short'].mean() * 100)} of all posts are Shorts."],
            f"Keep the main reach on Shorts but add some {better} for the campaigns that matter." if pv < 0.05
            else "No clear winner, so keep both formats and test further.",
            "Publish the same campaign in both formats and compare like rate and comment rate.", 100 if pv < 0.05 else 30)
    # campaign
    cg = p.groupby("campaign").agg(n=("post_id", "size"), m=("like_rate", "median"))
    cg = cg[cg["n"] >= 15]
    if len(cg) >= 2:
        top_c = cg["m"].idxmax()
        pv = stats.kruskal(*[p.loc[p["campaign"] == c, "like_rate"] for c in cg.index]).pvalue
        add("Content", f"Campaign: '{top_c}' posts have the highest like rate",
            [f"Median {cg.loc[top_c, 'm']:.2f} likes per 100 views over {int(cg.loc[top_c, 'n'])} posts.",
             f"Differences between campaigns are {'significant' if pv < 0.05 else 'not significant'} (p = {pv:.3f})."],
            "Plan more of this campaign type." if pv < 0.05 else "Campaign type alone does not decide engagement; test creative ideas inside each campaign.",
            "Run two creative versions inside the same campaign.", 100 if pv < 0.05 else 30)
    # promotions: price
    price = int(real["topic_price"].sum())
    ask_price = has(r"\bprice\b|\bprices\b|kitne ka|kitni ka|\brate\b|qeemat")
    add("Promotions", "Show the price in the description",
        [f"{price:,} comments are about price; {ask_price:,} simply ask 'price?'.",
         f"Price is {pct(price / max(1, int(real['topic'].isin(SHOPPING).sum())) * 100)} of all shopping-need comments."],
        "Add the starting price (or price range) to every video description and pinned comment.",
        "Compare the number of 'price?' comments per 1,000 views before and after.", price)
    # promotions: discounts
    disc = int(real["topic_discount_offers"].sum())
    ask_sale = has(r"sale kab|sale kb|sell kb|sell kab|when .{0,20}sale|sale (?:lagy|lage|lagi)")
    add("Promotions", "Announce sale dates in advance",
        [f"{disc:,} comments are about discounts or sales; {ask_sale} ask when the next sale is."],
        "Post sale dates in the description and pinned comment, and tease them in Shorts.",
        "Track comment rate and views on sale posts that announce dates versus those that do not.", disc)
    # customer service
    ds = real[real["topic_delivery_service"]]
    neg = (ds["sentiment"] == "negative").mean() * 100 if len(ds) else 0
    bc = real.groupby("brand").agg(n=("comment_id", "size"), d=("topic_delivery_service", "sum"))
    bc = bc[bc["n"] >= 100]
    worst = (bc["d"] / bc["n"] * 1000).idxmax() if len(bc) else None
    add("Customer service", "Answer delivery, return and order comments quickly",
        [f"{len(ds)} comments are about delivery, returns, orders or customer service; {neg:.0f}% are negative.",
         (f"Highest rate: {worst} ({(bc.loc[worst, 'd'] / bc.loc[worst, 'n'] * 1000):.1f} per 1,000 comments)." if worst else "")],
        "Assign someone to reply within 24 hours and pin a comment with the order-tracking and complaint contact.",
        "Measure the share of delivery comments that receive a brand reply and their later sentiment.", len(ds) * 3)
    avail = int(real["topic_availability"].sum())
    ask_av = has(r"is this available|available hai|where can i (?:buy|get)|kahan se mil|not available|website")
    add("Customer service", "Make availability obvious",
        [f"{avail:,} comments are about availability, stores or the website; {ask_av} ask where to buy or whether it is available."],
        "Add direct product or collection links and store availability to descriptions.",
        "Compare 'is this available?' comments per 1,000 views before and after adding links.", avail)
    fq = real[real["topic_fabric_quality"]]
    if len(fq):
        add("Customer service", "Address fabric-quality concerns openly",
            [f"{len(fq)} comments discuss fabric or quality; {(fq['sentiment'] == 'negative').mean() * 100:.0f}% are negative, "
             f"{(fq['sentiment'] == 'positive').mean() * 100:.0f}% positive."],
            "Show fabric close-ups and care details in videos; answer quality complaints publicly.",
            "Track the share of negative quality comments per collection.", len(fq))
    # campaign testing
    add("Campaign testing", "Run three simple A/B tests next season",
        ["Short vs longer cut of the same campaign.", "Price in the description vs no price.",
         "Song credit and full-version link vs no song credit."],
        "Change one thing at a time and keep the posting day and brand the same.",
        "Primary measures: likes per 100 views, comments per 1,000 views, and the share of comments asking about price or the song.", 30)

    area = st.multiselect("Show areas", ["Content", "Promotions", "Customer service", "Campaign testing"],
                          default=["Content", "Promotions", "Customer service", "Campaign testing"])
    for ar, prio, title, ev, act, test in recs:
        if ar not in area:
            continue
        with st.container(border=True):
            st.markdown(f"**{ar}** · priority **{prio}**")
            st.subheader(title)
            st.markdown("**Evidence**\n" + "\n".join(f"- {x}" for x in ev if x))
            st.markdown(f"**Action:** {act}")
            st.markdown(f"**How to test:** {test}")
    st.warning("Limitations: public YouTube data only, five brands, comments are concentrated on a few posts, "
               "and Roman Urdu sentiment is less accurate. Treat these as evidence-based starting points.")

# ------------------------------------------------------------------------------------------ DATA & METHOD
else:
    st.title("Data and method")
    st.subheader("Data sources and provenance")
    coll = (raw_posts["collection_date"].iloc[0] if raw_posts is not None and "collection_date" in raw_posts.columns else "see posts.csv")
    st.markdown(
        f"- **Source:** YouTube Data API v3 (official, permitted access) · public videos and comments of five Pakistani brand channels.\n"
        f"- **Collected on:** {coll}.\n"
        f"- **Posts (Table A):** {n_posts:,} women's lawn and seasonal collection videos. "
        f"**Comments (Table B):** {len(com):,} collected, {n_real:,} real after removing {int(com['is_spam'].sum())} spam comments.\n"
        f"- **Real vs simulated:** all records are real; none are simulated or generated (`data_origin` column).\n"
        f"- **Privacy:** usernames are one-way hashed; phone numbers, emails and @handles are masked.\n"
        f"- **Reuse:** public data used for coursework analysis only; API terms followed.")
    st.subheader("Why YouTube only")
    st.markdown("Instagram, TikTok and Facebook comments cannot be collected permitted-ly for brands we do not own "
                "(their APIs only give data for accounts you manage). YouTube provides public comments through an "
                "official API. **Shares** are not available from YouTube, so that field stays empty.")
    st.subheader("Method")
    st.markdown(
        "1. **Posts:** all uploads of five channels → lawn / seasonal women's wear filter (keywords, men/kids/bridal/winter excluded) → "
        "campaign and content type from title and duration.\n"
        "2. **Comments:** most relevant top-level comments per post (max 300), brand's own comments removed.\n"
        "3. **Labels:** language, topic, purchase intent from keyword dictionaries (English, Roman Urdu, Urdu); "
        "sentiment with VADER plus an Urdu/Roman Urdu word list.\n"
        "4. **Clustering:** TF-IDF → SVD → K-Means (k by silhouette) on comments; K-Means on post engagement.\n"
        "5. **Anomalies:** Isolation Forest on each post's z-scores within its own brand.\n"
        "6. **Recommendations:** rules that read the evidence above.")
    st.subheader("Validation (checked by hand)")
    for k, label in (("posts", "Post filter"), ("comments_topic", "Comment topic"), ("comments_sentiment", "Comment sentiment")):
        st.markdown(f"- **{label}:** {VALIDATION[k] or '(add your result in app/app.py, VALIDATION)'}")
    st.subheader("Limitations")
    st.markdown("- Only YouTube; five brands; mostly Shorts.\n- Comments are concentrated on a few posts (most posts have none).\n"
                "- Keyword rules and VADER are less accurate on Roman Urdu and mixed-language comments.\n"
                "- Clusters of short text are only weakly separated (low silhouette); names are interpretations.\n"
                "- Engagement shows association, not cause.")
    with st.expander("Browse the data"):
        st.markdown("**Posts (Table A, with groups and anomaly flags)**")
        st.dataframe(posts.head(200))
        st.markdown("**Comments (Table B, labelled)**")
        st.dataframe(real[["post_id", "brand", "comment_text", "language_type", "topic", "sentiment", "purchase_intent"]].head(300))
