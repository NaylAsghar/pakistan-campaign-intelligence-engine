"""Build the Project 3 Word report from YOUR real data.

Run from the project root, after the pipeline scripts have run:
    python src/generate_report.py
    python src/generate_report.py https://your-app.streamlit.app     (adds the live app link)
Output: reports/Project3_Report.docx  (charts are also saved in images/report_*.png)
Every number, table and chart is calculated from your CSV files, so rerun it if the data changes.
"""
import datetime
import os
import re
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from scipy import stats

# ---- EDIT THESE --------------------------------------------------------------------------------
STUDENT_NAME = "Nayl Asghar"
COURSE = "Big Data Analytics"
GITHUB_URL = "https://github.com/NaylAsghar/pakistan-campaign-intelligence-engine"
TITLE = "Pakistan Social Audience and Campaign Intelligence Engine"
SUBTITLE = "Clustering-based analysis of YouTube posts and comments of Pakistani women's lawn brands"
POST_VALIDATION = ("35 of 50 randomly sampled posts were clearly women's lawn or seasonal women's wear (70%), "
                   "5 were wrong (10%) and 10 could not be decided from the title (20%); "
                   "among the 40 decidable posts, 88% were correct.")
COMMENT_VALIDATION = None   # e.g. "Topic: 41 of 50 correct (82%). Sentiment: 44 of 50 correct (88%)."
# -----------------------------------------------------------------------------------------------------
APP_URL = sys.argv[1] if len(sys.argv) > 1 else ""
PROC, REP, IMG, OUT = "data/processed", "reports", "images", "reports/Project3_Report.docx"
for d in (REP, IMG):
    os.makedirs(d, exist_ok=True)


def read(path):
    return pd.read_csv(path, encoding="utf-8-sig") if os.path.exists(path) else None


posts, com = read(f"{PROC}/posts_clustered.csv"), read(f"{PROC}/comments.csv")
if posts is None or com is None:
    sys.exit("Missing data files. Run cluster_posts.py and label_comments.py first.")
clusters, csum = read(f"{PROC}/comment_clusters.csv"), read(f"{REP}/comment_cluster_summary.csv")
ksel, pprof = read(f"{REP}/cluster_k_selection.csv"), read(f"{REP}/post_cluster_profiles.csv")
raw_posts, census = read(f"{PROC}/posts.csv"), read("data/raw/channel_census.csv")
raw_comments = read("data/raw/comments_raw.csv")
if clusters is not None:
    com = com.merge(clusters.drop(columns=[c for c in clusters.columns if c in com.columns and c != "comment_id"]),
                    on="comment_id", how="left")

SHOPPING = ["price", "availability", "discount_offers", "delivery_service", "fabric_quality", "sizing"]
REACTIONS = ["music_song", "celebrity_model", "design_style", "general_praise", "brand_loyalty", "content_request", "chit_chat"]
NICE = {"music_song": "Song and music", "celebrity_model": "Celebrity / model", "design_style": "Design and style",
        "general_praise": "General praise", "brand_loyalty": "Brand loyalty", "content_request": "Content requests",
        "chit_chat": "Chit-chat", "price": "Price", "availability": "Availability", "discount_offers": "Discounts and sale",
        "delivery_service": "Delivery and service", "fabric_quality": "Fabric quality", "sizing": "Sizing",
        "complaint_criticism": "Complaints and criticism", "other": "Other", "noise": "Noise", "emoji_only": "Emoji only",
        "spam": "Spam"}
GROUP_COLOR = {"Shopping needs": "#e08a2e", "Campaign reactions": "#3b6fd4", "Complaints and criticism": "#d64545",
               "Other / noise": "#9aa0a6"}
NAVY = "1F3864"


def group_of(t):
    return ("Shopping needs" if t in SHOPPING else "Campaign reactions" if t in REACTIONS
            else "Complaints and criticism" if t == "complaint_criticism" else "Other / noise")


real = com[~com["is_spam"]].copy()
real["group"] = real["topic"].apply(group_of)
N_POSTS, N_REAL, N_BRANDS = len(posts), len(real), posts["brand"].nunique()
posts["is_short"] = posts["content_type"].str.startswith("Short")
posts["format"] = np.where(posts["is_short"], "Short video", "Longer video")


def pc(x, d=1):
    return f"{x:.{d}f}%"


def pv_text(p):
    return f"p = {p:.4f}" if p >= 0.0001 else "p < 0.0001"


# ===================================================================================== ANALYSIS
topic_share = real["topic"].value_counts(normalize=True).mul(100)
meaningful = real[~real["topic"].isin(["emoji_only", "noise"])]
topic_share_m = meaningful["topic"].value_counts(normalize=True).mul(100)
group_share = meaningful["group"].value_counts(normalize=True).mul(100)
shop_share = real["topic"].isin(SHOPPING).mean() * 100
sent = real["sentiment"].value_counts(normalize=True).mul(100)
lang = real["language_type"].value_counts(normalize=True).mul(100)
inten = real["purchase_intent"].value_counts(normalize=True).mul(100)

camp = posts.groupby("campaign").agg(posts=("post_id", "size"), views_day=("views_per_day", "median"),
                                     like_rate=("like_rate", "median"), comment_rate=("comment_rate", "median"),
                                     with_comments=("comments_count", lambda s: (s > 0).mean() * 100)).round(2)
camp = camp.sort_values("like_rate", ascending=False)
fmt = posts.groupby("format").agg(posts=("post_id", "size"), views_day=("views_per_day", "median"),
                                  like_rate=("like_rate", "median"), comment_rate=("comment_rate", "median"),
                                  with_comments=("comments_count", lambda s: (s > 0).mean() * 100)).round(2)
brand_t = posts.groupby("brand").agg(posts=("post_id", "size"), views_day=("views_per_day", "median"),
                                     like_rate=("like_rate", "median"),
                                     with_comments=("comments_count", lambda s: (s > 0).mean() * 100)).round(2)
brand_t["comments"] = real["brand"].value_counts()
brand_t = brand_t.sort_values("posts", ascending=False)
sh, lo = posts[posts["is_short"]], posts[~posts["is_short"]]
tests = {}
if len(sh) >= 10 and len(lo) >= 10:
    for m in ("like_rate", "views_per_day", "comment_rate"):
        tests[f"fmt_{m}"] = stats.mannwhitneyu(sh[m], lo[m]).pvalue
big_c = camp[camp["posts"] >= 15].index
if len(big_c) >= 2:
    for m in ("like_rate", "views_per_day"):
        tests[f"camp_{m}"] = stats.kruskal(*[posts.loc[posts["campaign"] == c, m] for c in big_c]).pvalue

flag = posts[posts["anomaly_flag"]].sort_values("anomaly_score", ascending=False)
reason_txt = flag["anomaly_reason"].fillna("")
r_comm = int(reason_txt.str.contains("comment").sum())
r_like = int(reason_txt.str.contains("like rate").sum())
r_view = int(reason_txt.str.contains("views per day").sum())


# ===================================================================================== CHARTS
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})


def save_fig(name):
    plt.tight_layout()
    path = f"{IMG}/report_{name}.png"
    plt.savefig(path, dpi=200)
    plt.close()
    return path


t = topic_share_m.sort_values()
plt.figure(figsize=(6.4, 4.2))
plt.barh([NICE.get(k, k) for k in t.index], t.values, color=[GROUP_COLOR[group_of(k)] for k in t.index])
for i, v in enumerate(t.values):
    plt.text(v + 0.3, i, f"{v:.1f}%", va="center", fontsize=8)
plt.xlabel("% of comments"); plt.title(f"Main topic of {len(meaningful):,} comments")
plt.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=c) for c in GROUP_COLOR.values()], labels=list(GROUP_COLOR),
           fontsize=7, loc="lower right")
fig_topics = save_fig("topics")

c = camp[camp["posts"] >= 10].sort_values("like_rate")
plt.figure(figsize=(6.4, 3.2))
plt.barh([f"{i} (n={int(n)})" for i, n in zip(c.index, c["posts"])], c["like_rate"], color="#3b6fd4")
for i, v in enumerate(c["like_rate"]):
    plt.text(v + 0.01, i, f"{v:.2f}", va="center", fontsize=8)
plt.xlabel("median likes per 100 views"); plt.title("Engagement by campaign")
fig_camp = save_fig("campaign")

plt.figure(figsize=(6.4, 2.8))
fig, ax = plt.subplots(1, 3, figsize=(6.4, 2.8))
for a, (col, lab) in zip(ax, (("like_rate", "likes per 100 views"), ("views_day", "views per day"),
                              ("comment_rate", "comments per 1,000 views"))):
    a.bar(fmt.index, fmt[col], color=["#8fb0ee", "#3b6fd4"][: len(fmt)])
    a.set_title(lab, fontsize=8); a.tick_params(axis="x", labelsize=7)
    for i, v in enumerate(fmt[col]):
        a.text(i, v, f"{v:.2f}", ha="center", va="bottom", fontsize=7)
fig.suptitle("Short vs longer videos (medians)", fontsize=9)
fig_fmt = save_fig("format")

gb = pd.crosstab(meaningful["brand"], meaningful["group"], normalize="index").mul(100)
gb = gb[[g for g in GROUP_COLOR if g in gb.columns]]
gb.plot(kind="barh", stacked=True, figsize=(6.4, 3.0), color=[GROUP_COLOR[g] for g in gb.columns])
plt.xlabel("% of the brand's comments"); plt.ylabel(""); plt.legend(fontsize=7, loc="lower right")
plt.title("What each brand's audience talks about")
fig_brand = save_fig("brand_topics")

fig_seg = fig_shop = None
if csum is not None:
    def seg_chart(group, name, title):
        s = csum[csum["group"] == group].sort_values("size")
        if s.empty:
            return None
        def latin(words):     # the chart font cannot draw Urdu script, so use the English/Roman words only
            return ", ".join([w for w in str(words).split(", ") if w.isascii()][:2])
        def ascii_name(n):
            return re.sub(r"\(\s*\)", "", "".join(ch for ch in str(n) if ord(ch) < 128)).strip()
        labels = [f"{ascii_name(n)[:34]} [{latin(w)}]" if c >= 0 else n for n, w, c in zip(s["name"], s["top_terms"], s["cluster"])]
        plt.figure(figsize=(6.4, 3.4))
        plt.barh(labels, s["size"], color="#7a5cd6")
        for i, v in enumerate(s["size"]):
            plt.text(v, i, f" {int(v):,}", va="center", fontsize=7)
        plt.xlabel("comments"); plt.title(title); plt.yticks(fontsize=7)
        return save_fig(name)
    fig_seg = seg_chart("all", "segments", "Audience segments (K-Means on comments)")
    fig_shop = seg_chart("shopping", "shopping_segments", "Shopping-need segments (K-Means on shopping comments)")

cap = float(posts["like_rate"].quantile(0.99))
plt.figure(figsize=(6.4, 3.4))
n_ = posts[~posts["anomaly_flag"]]
plt.scatter(n_["views_per_day"], n_["like_rate"], s=8, alpha=0.5, color="#9aa0a6", label="normal")
plt.scatter(flag["views_per_day"], flag["like_rate"], s=22, facecolors="none", edgecolors="#d64545", label="unusual")
plt.xscale("log"); plt.ylim(-0.05 * cap, cap * 1.6)
plt.xlabel("views per day (log scale)"); plt.ylabel("likes per 100 views"); plt.legend()
plt.title("Posts and unusual posts (vertical axis capped near the 99th percentile)")
fig_anom = save_fig("anomalies")


# ===================================================================================== RECOMMENDATIONS
def has(pattern):
    return int(real["comment_text"].str.lower().str.contains(pattern, regex=True).sum())


recs = []


def add(area, title, evidence, action, test, strength):
    recs.append({"area": area, "priority": "High" if strength >= 100 else "Medium" if strength >= 30 else "Low",
                 "title": title, "evidence": " ".join(e for e in evidence if e), "action": action, "test": test})


music = int(real["topic_music_song"].sum())
asks = has(r"song name|name of (?:the )?song|singer|full (?:song|version)|release|lyrics|ganey ka naam|gane ka naam")
add("Content", "Credit the song and singer; release the full version",
    [f"{pc(music / N_REAL * 100)} of comments are about the song or music ({music:,}).",
     f"{asks:,} comments ask for the song name, the singer or the full version."],
    "Put the song and singer in every description and pinned comment; release a full version.",
    "Compare comment and like rates with and without song credit.", music)
names = ["yumna", "ayeza", "aiza", "iqra", "mahira", "hania", "sajal", "ramsha", "kinza", "mawra", "kubra", "saba", "shuja"]
cnt = {n: int(real["text_clean"].str.contains(rf"\b{n}\b", regex=True).sum()) for n in names}
topn = sorted(cnt.items(), key=lambda x: -x[1])[:3]
celeb = int(real["topic_celebrity_model"].sum())
add("Content", "Keep featuring the celebrities the audience names",
    [f"{celeb:,} comments mention models or celebrities ({pc(celeb / N_REAL * 100)}).",
     "Most named: " + ", ".join(f"{n.title()} ({c})" for n, c in topn) + "."],
    "Name the featured celebrity in the title and thumbnail of the next campaign video.",
    "Compare views per day for videos that name a celebrity in the title versus those that do not.", celeb)
if "fmt_like_rate" in tests:
    a_, b_ = sh["like_rate"].median(), lo["like_rate"].median()
    better = "Longer videos" if b_ > a_ else "Short videos"
    sig = tests["fmt_like_rate"] < 0.05
    add("Content", f"Format: {better.lower()} earn the higher like rate",
        [f"Median likes per 100 views: Short {a_:.2f} vs longer {b_:.2f} ({'significant' if sig else 'not significant'}, "
         f"{pv_text(tests['fmt_like_rate'])}).", f"{pc(posts['is_short'].mean() * 100)} of all posts are Shorts."],
        f"Keep reach on Shorts but add {better.lower()} for key campaigns." if sig else "No clear winner; keep both formats and test.",
        "Publish the same campaign in both formats and compare.", 100 if sig else 30)
if "camp_like_rate" in tests and len(big_c) >= 2:
    cc = camp.loc[big_c]
    top_c = cc["like_rate"].idxmax()
    sig = tests["camp_like_rate"] < 0.05
    add("Content", f"Campaign: '{top_c}' posts have the highest like rate",
        [f"Median {cc.loc[top_c, 'like_rate']:.2f} likes per 100 views over {int(cc.loc[top_c, 'posts'])} posts.",
         f"Differences between campaigns are {'significant' if sig else 'not significant'} ({pv_text(tests['camp_like_rate'])})."],
        "Plan more of this campaign type." if sig else "Campaign type alone does not decide engagement; test creative ideas inside each campaign.",
        "Run two creative versions inside the same campaign.", 100 if sig else 30)
price = int(real["topic_price"].sum())
ask_price = has(r"\bprice\b|\bprices\b|kitne ka|kitni ka|\brate\b|qeemat")
add("Promotions", "Show the price in the description",
    [f"{price:,} comments are about price; {ask_price:,} simply ask 'price?'.",
     f"Price is {pc(price / max(1, int(real['topic'].isin(SHOPPING).sum())) * 100)} of shopping-need comments."],
    "Add the starting price or price range to every description and pinned comment.",
    "Compare 'price?' comments per 1,000 views before and after.", price)
disc = int(real["topic_discount_offers"].sum())
ask_sale = has(r"sale kab|sale kb|sell kb|sell kab|when .{0,20}sale|sale (?:lagy|lage|lagi)")
add("Promotions", "Announce sale dates in advance",
    [f"{disc:,} comments are about discounts or sales; {ask_sale} ask when the next sale is."],
    "Post sale dates in descriptions and pinned comments and tease them in Shorts.",
    "Track comment rate and views on sale posts that announce dates versus those that do not.", disc)
ds = real[real["topic_delivery_service"]]
neg = (ds["sentiment"] == "negative").mean() * 100 if len(ds) else 0
bc = real.groupby("brand").agg(n=("comment_id", "size"), d=("topic_delivery_service", "sum"))
bc = bc[bc["n"] >= 100]
worst = (bc["d"] / bc["n"] * 1000).idxmax() if len(bc) else None
add("Customer service", "Answer delivery, return and order comments quickly",
    [f"{len(ds)} comments concern delivery, returns, orders or service; {neg:.0f}% are negative.",
     (f"Highest rate: {worst} ({bc.loc[worst, 'd'] / bc.loc[worst, 'n'] * 1000:.1f} per 1,000 comments)." if worst else "")],
    "Assign someone to reply within 24 hours; pin order-tracking and complaint contact details.",
    "Measure the share of delivery comments that get a brand reply and their later sentiment.", len(ds) * 3)
avail = int(real["topic_availability"].sum())
ask_av = has(r"is this available|available hai|where can i (?:buy|get)|kahan se mil|not available|website")
add("Customer service", "Make availability obvious",
    [f"{avail:,} comments are about availability, stores or the website; {ask_av} ask where to buy or whether it is available."],
    "Add direct product or collection links and store availability to descriptions.",
    "Compare 'is this available?' comments per 1,000 views before and after.", avail)
fq = real[real["topic_fabric_quality"]]
if len(fq):
    add("Customer service", "Address fabric-quality concerns openly",
        [f"{len(fq)} comments discuss fabric or quality; {(fq['sentiment'] == 'negative').mean() * 100:.0f}% are negative "
         f"and {(fq['sentiment'] == 'positive').mean() * 100:.0f}% positive."],
        "Show fabric close-ups and care details; answer quality complaints publicly.",
        "Track the share of negative quality comments per collection.", len(fq))
add("Campaign testing", "Run three simple A/B tests next season",
    ["Short vs longer cut of one campaign.", "Price in the description vs none.", "Song credit and full-version link vs none."],
    "Change one thing at a time; keep brand and posting day the same.",
    "Measure likes per 100 views, comments per 1,000 views and the share of comments asking about price or the song.", 30)


# ===================================================================================== WORD HELPERS
def clean_xml(s):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]", "", str(s))


def short(s, n):
    s = clean_xml(s).replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "\u2026"


def shade(cell, fill):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), fill)
    cell._tc.get_or_add_tcPr().append(shd)


def para(doc, text="", bold=False, italic=False, size=None, color=None, align=None, after=6):
    p = doc.add_paragraph()
    r = p.add_run(clean_xml(text))
    r.bold, r.italic = bold, italic
    if size: r.font.size = Pt(size)
    if color: r.font.color.rgb = RGBColor.from_string(color)
    if align is not None: p.alignment = align
    p.paragraph_format.space_after = Pt(after)
    return p


def bullet(doc, text, bold_prefix=None):
    p = doc.add_paragraph(style="List Bullet")
    if bold_prefix:
        p.add_run(clean_xml(bold_prefix)).bold = True
    p.add_run(clean_xml(text if bold_prefix else text.lstrip()))
    p.paragraph_format.space_after = Pt(2)


def caption(doc, text):
    p = para(doc, text, italic=True, size=9, color="555555", after=3)
    p.paragraph_format.keep_with_next = True


def table(doc, header, rows, widths, right_cols=(), size=8.5, bold_last=False):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"; t.alignment = WD_TABLE_ALIGNMENT.CENTER; t.autofit = False
    for i, h in enumerate(header):
        c = t.rows[0].cells[i]; c.width = Inches(widths[i]); shade(c, NAVY)
        c.paragraphs[0].text = ""
        r = c.paragraphs[0].add_run(h); r.bold = True; r.font.size = Pt(size); r.font.color.rgb = RGBColor(255, 255, 255)
        if i in right_cols: c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].width = Inches(widths[i])
            if ri % 2 == 1: shade(cells[i], "EEF3FA")
            cells[i].paragraphs[0].text = ""
            r = cells[i].paragraphs[0].add_run(clean_xml(v)); r.font.size = Pt(size)
            if bold_last and ri == len(rows) - 1: r.bold = True
            if i in right_cols: cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for i, w in enumerate(widths):
        t.columns[i].width = Inches(w)
    for row in t.rows:
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for c in row.cells:
            for p in c.paragraphs:
                p.paragraph_format.space_after = Pt(1); p.paragraph_format.space_before = Pt(1)
    if len(t.rows) <= 12:
        for row in t.rows[:-1]:
            for c in row.cells:
                for p in c.paragraphs:
                    p.paragraph_format.keep_with_next = True
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


def figure(doc, path, text):
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run().add_picture(path, width=Inches(5.3))
    p.paragraph_format.space_after = Pt(0); p.paragraph_format.keep_with_next = True
    para(doc, text, italic=True, size=9, color="555555", align=WD_ALIGN_PARAGRAPH.CENTER, after=8)


def heading(doc, text, level=1):
    h = doc.add_heading(clean_xml(text), level=level)
    for r in h.runs:
        r.font.color.rgb = RGBColor.from_string(NAVY); r.font.name = "Calibri"; r.font.size = Pt(15 if level == 1 else 12)
    h.paragraph_format.space_before = Pt(12 if level == 1 else 6); h.paragraph_format.space_after = Pt(4)
    h.paragraph_format.keep_with_next = True


def page_number(section):
    p = section.footer.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run("Page ").font.size = Pt(9)
    r = p.add_run(); r.font.size = Pt(9)
    for kind, txt in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar"); el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText"); el.set(qn("xml:space"), "preserve"); el.text = txt
        r._r.append(el)


def seg_label(row):
    if row["cluster"] < 0:
        return row["name"]
    return f"{row['name']}  [{', '.join(row['top_terms'].split(', ')[:4])}]"


# ===================================================================================== BUILD
doc = Document()
sec = doc.sections[0]
sec.left_margin = sec.right_margin = Inches(0.9); sec.top_margin = sec.bottom_margin = Inches(0.8)
doc.styles["Normal"].font.name = "Calibri"; doc.styles["Normal"].font.size = Pt(10.5)
page_number(sec)

para(doc, TITLE, bold=True, size=21, color=NAVY, after=2)
para(doc, SUBTITLE, size=12, color="555555", after=4)
para(doc, f"Prepared by {STUDENT_NAME}  |  {COURSE}  |  {datetime.date.today():%d %B %Y}", size=9.5, color="555555", after=1)
para(doc, f"Code and data: {GITHUB_URL}", size=9.5, color="555555", after=1)
if APP_URL:
    para(doc, f"Live web application: {APP_URL}", size=9.5, color="555555", after=1)
para(doc, "", after=4)

# ---- 1 executive summary
top_topic = topic_share_m.index[0]
heading(doc, "1. Executive Summary")
para(doc, f"This project builds a small web application that helps a Pakistani lawn brand understand what its social "
          f"audience does and wants. It analyses {N_POSTS:,} public YouTube posts and {N_REAL:,} audience comments from "
          f"{N_BRANDS} brands (Sapphire, Khaadi, Gul Ahmed, Alkaram and Sana Safinaz), groups similar audience behaviour "
          f"with clustering, flags unusual posts with anomaly detection, and turns the findings into recommendations.")
bullet(doc, f" the largest topic in comments is {NICE.get(top_topic, top_topic).lower()} "
            f"({pc(topic_share_m.iloc[0])}); only {pc(shop_share)} of comments express shopping needs such as price, "
            f"availability, discounts, delivery, quality or size.", "Audience:")
if "camp_like_rate" in tests or "fmt_like_rate" in tests:
    top_c = camp[camp["posts"] >= 15]["like_rate"].idxmax()
    sig_c = tests.get("camp_like_rate", 1) < 0.05
    sig_f = tests.get("fmt_like_rate", 1) < 0.05
    bullet(doc, f" '{top_c}' posts have the highest median like rate "
                f"({'a statistically significant difference' if sig_c else 'not a statistically significant difference'}); "
                f"Short videos make up {pc(posts['is_short'].mean() * 100, 0)} of posts, and format differences are "
                f"{'significant' if sig_f else 'not significant'}.", "Content:")
bullet(doc, f" {len(flag)} posts ({pc(len(flag) / N_POSTS * 100)}) behave very differently from their own brand's usual level "
            f"and are listed for investigation.", "Unusual patterns:")
bullet(doc, f" {pc((posts['comments_count'] > 0).mean() * 100)} of posts have any comments, so conversation is concentrated "
            f"on a few posts.", "Concentration:")
bullet(doc, f" {len(recs)} evidence-based recommendations for content, promotions, customer service and campaign testing.", "Actions:")

# ---- 2 business problem
heading(doc, "2. Business Problem and Objectives")
para(doc, "A brand running social campaigns for a seasonal collection receives thousands of comments such as 'price please?', "
          "'is this available?' or 'delivery kitne din mein hogi?'. It also has engagement data, but it does not know which "
          "customer needs and content patterns are hidden inside these interactions. The task is not just to label comments "
          "positive or negative, but to discover audience segments and campaign opportunities.")
para(doc, "The engine answers five marketing questions:", after=2)
for q in ("Which content attracts attention? (formats, campaigns and brands compared by views, likes and comments)",
          "What does the audience want? (price, fabric quality, sizing, delivery, discounts, availability, purchase interest)",
          "Which marketing patterns are unusual? (posts that differ substantially from comparable posts)",
          "Which audience needs should the brand address? (discount-focused, delivery-concerned, availability-seeking groups)",
          "What should the marketing team do next? (content, promotions, customer service and campaign testing)"):
    bullet(doc, q)

# ---- 3 data
heading(doc, "3. Data")
heading(doc, "3.1 Source, access and scope", 2)
para(doc, "Data were collected with the YouTube Data API v3, which is an official, permitted way to read public videos and "
          "comments. The brief names Instagram, TikTok and Facebook as typical platforms, but comments on brand accounts "
          "that the analyst does not own cannot be collected through permitted methods (their APIs only return data for "
          "accounts you manage, and scraping would break the platform terms). YouTube was therefore used. The product "
          "category is women's lawn suits and seasonal women's wear. Shares are not available from YouTube, so that field is empty.")
rows = [[b, f"{int(r['posts']):,}", f"{int(r['comments']):,}"] for b, r in brand_t.iterrows()]
rows.append(["Total", f"{N_POSTS:,}", f"{N_REAL:,}"])
caption(doc, "Table 1. Posts and real comments per brand")
table(doc, ["Brand (official YouTube channel)", "Posts", "Comments"], rows, [3.4, 1.2, 1.2], right_cols=(1, 2), bold_last=True, size=9)

heading(doc, "3.2 Table A (posts) and Table B (comments)", 2)
para(doc, "Both tables follow the structure in the brief and carry extra provenance columns.", after=3)
caption(doc, "Table 2. Table A, posts.csv: fields and meaning")
table(doc, ["Field", "Meaning in this project"],
      [["post_id, brand, platform", "Identifier, brand (channel) and platform (YouTube)"],
       ["campaign, content_type", "Rule-based from title and duration: Eid, Summer, Festive, Spring, Sale; Short (Reel-style), collection showcase, influencer, styling and others"],
       ["product_category", "Lawn suit (women's lawn and seasonal women's wear)"],
       ["likes, comments_count, views", "As reported by YouTube at collection time (comments_count usually includes replies)"],
       ["shares", "Not available from the YouTube API, left empty"],
       ["days_since_post", "Days between publication and collection date"],
       ["source_url, collection_date, data_origin", "Provenance: video link, date of collection, and 'real' marker"]],
      [2.1, 4.6], size=8.5)
caption(doc, "Table 3. Table B, comments.csv: fields and meaning")
table(doc, ["Field", "Meaning in this project"],
      [["post_id, comment_text", "Link to the post; comment text with emails, phone numbers and @handles masked"],
       ["language_type", "English, Roman Urdu, Urdu script or Emoji/Other (rule-based)"],
       ["topic", "Main topic from keyword dictionaries: price, availability, discounts, delivery and service, fabric quality, sizing, song and music, celebrity, design, praise and others"],
       ["sentiment", "Positive, neutral or negative (VADER plus an Urdu and Roman Urdu word list); secondary to topics and segments"],
       ["purchase_intent", "High (explicit wish to buy), medium (price or availability enquiry) or none"],
       ["author_hash, is_spam", "One-way hashed username (no real identity stored); spam flag"]],
      [2.1, 4.6], size=8.5)

heading(doc, "3.3 Provenance and reuse", 2)
coll = (raw_posts["collection_date"].iloc[0] if raw_posts is not None and "collection_date" in raw_posts.columns else "see posts.csv")
table(doc, ["Item", "Detail"],
      [["Source", "YouTube Data API v3 (public videos and comments of five brand channels); each post keeps its source_url"],
       ["Collection date", str(coll)],
       ["Real vs simulated", "All records are real. No simulated or generated records are used (data_origin column)."],
       ["Reuse permissions", "Public data used for coursework analysis under the YouTube API terms. Usernames are hashed and personal details are masked; the raw dataset is shared only for assessment."],
       ["Not used", "The 2026 paper dataset, the Open Data Pakistan e-commerce data and the product catalogue were not reused; availability and permissions were not confirmed."]],
      [1.6, 5.1], size=8.5)

heading(doc, "3.4 Collection and cleaning", 2)
steps = []
if census is not None:
    steps.append(("Videos on the five channels (all uploads)", len(census)))
steps.append(("Women's lawn posts kept after the filter (Table A)", N_POSTS))
if raw_comments is not None:
    steps.append(("Comments collected (max 300 per post)", len(raw_comments)))
steps.append(("Comments after removing duplicates and empty ones (Table B)", len(com)))
steps.append(("Real comments after removing spam", N_REAL))
caption(doc, "Table 4. Data volume at each step")
table(doc, ["Step", "Records"], [[a, f"{b:,}"] for a, b in steps], [5.0, 1.4], right_cols=(1,), size=9)
para(doc, f"The brief asks for about 2,000 to 5,000 records. This dataset has {N_POSTS:,} posts and {N_REAL:,} comments, "
          f"{N_POSTS + N_REAL:,} records in total.")
para(doc, "Validation of the post filter: " + POST_VALIDATION)
if COMMENT_VALIDATION:
    para(doc, "Validation of the comment labels: " + COMMENT_VALIDATION)
else:
    para(doc, "The comment labels (topic, sentiment, intent) were produced by rules and were not checked against a hand-labelled "
              "sample, so their accuracy is unknown and results about them should be read as indicative.")

# ---- 4 method
heading(doc, "4. Methodology")
table(doc, ["Step", "Method", "Purpose"],
      [["Collect", "YouTube Data API v3 (channel uploads, video statistics, top-level comments)", "Posts and comments"],
       ["Filter and label posts", "Keyword rules on titles (men, kids, bridal, winter, WEST, saree and home lines excluded); duration for Shorts", "Women's lawn scope, campaign, content type"],
       ["Label comments", "Keyword dictionaries in English, Roman Urdu and Urdu; VADER for sentiment", "Topic, language, intent, sentiment"],
       ["Group audience", "TF-IDF, SVD, K-Means; k chosen by silhouette score among balanced solutions", "Audience segments (clustering)"],
       ["Group posts", "K-Means on views per day, like rate and comment measures", "Post groups"],
       ["Find unusual posts", "Isolation Forest on each post's z-scores within its own brand", "Anomaly detection"],
       ["Test differences", "Mann-Whitney and Kruskal-Wallis tests", "Is a difference more than chance?"],
       ["Recommend", "Rules that turn counts and tests into actions", "Marketing decisions"]],
      [1.4, 3.4, 1.9], size=8.5)
para(doc, "Engagement is measured per view, so old and new videos and large and small channels are comparable: like rate = "
          "likes per 100 views; comment rate = comments per 1,000 views; views per day = views divided by days since posting.")

# ---- 5 results
heading(doc, "5. Results")
heading(doc, "5.1 Which content attracts attention?", 2)
rows = [[i, f"{int(r['posts']):,}", f"{r['views_day']:.1f}", f"{r['like_rate']:.2f}", f"{r['comment_rate']:.2f}", f"{r['with_comments']:.0f}%"]
        for i, r in camp.iterrows()]
caption(doc, "Table 5. Engagement by campaign (medians)")
table(doc, ["Campaign", "Posts", "Views per day", "Likes per 100 views", "Comments per 1,000 views", "Posts with comments"],
      rows, [1.9, 0.7, 0.9, 1.0, 1.2, 1.0], right_cols=(1, 2, 3, 4, 5), size=8.5)
figure(doc, fig_camp, "Figure 1. Median like rate by campaign")
rows = [[i, f"{int(r['posts']):,}", f"{r['views_day']:.1f}", f"{r['like_rate']:.2f}", f"{r['comment_rate']:.2f}", f"{r['with_comments']:.0f}%"]
        for i, r in fmt.iterrows()]
caption(doc, "Table 6. Short versus longer videos (medians)")
table(doc, ["Format", "Posts", "Views per day", "Likes per 100 views", "Comments per 1,000 views", "Posts with comments"],
      rows, [1.9, 0.7, 0.9, 1.0, 1.2, 1.0], right_cols=(1, 2, 3, 4, 5), size=8.5)
figure(doc, fig_fmt, "Figure 2. Short versus longer videos")
txt = []
if "fmt_like_rate" in tests:
    txt.append(f"Short videos have a median like rate of {sh['like_rate'].median():.2f} versus {lo['like_rate'].median():.2f} for longer "
               f"videos ({pv_text(tests['fmt_like_rate'])}, {'significant' if tests['fmt_like_rate'] < 0.05 else 'not significant'} at 5%).")
if "camp_like_rate" in tests:
    txt.append(f"Across the {len(big_c)} campaigns with at least 15 posts, like rates differ "
               f"{'significantly' if tests['camp_like_rate'] < 0.05 else 'but not significantly'} "
               f"(Kruskal-Wallis {pv_text(tests['camp_like_rate'])}).")
bt = brand_t.sort_values("like_rate", ascending=False)
txt.append(f"By brand, {bt.index[0]} has the highest median like rate ({bt['like_rate'].iloc[0]:.2f}) and {bt.index[-1]} the lowest "
           f"({bt['like_rate'].iloc[-1]:.2f}).")
para(doc, " ".join(txt))
para(doc, f"Interpretation: {pc(posts['is_short'].mean() * 100, 0)} of posts are Shorts, so the other formats have little data. "
          "Median like rates are small in absolute terms, which is normal for brand videos; differences should be read as "
          "indications for testing, not as proof that one format causes better results.")

heading(doc, "5.2 What does the audience want?", 2)
caption(doc, "Table 7. Main topic of comments (comments with real content)")
ex_rows = []
for tp in topic_share_m.index[:10]:
    sub = real[(real["topic"] == tp) & real["comment_text"].str.len().between(20, 100)]
    ex = short(sub.sort_values("like_count", ascending=False)["comment_text"].iloc[0], 70) if len(sub) else ""
    ex_rows.append([NICE.get(tp, tp), group_of(tp), pc(topic_share_m[tp]), ex])
table(doc, ["Topic", "Group", "Share", "Example comment"], ex_rows, [1.5, 1.4, 0.6, 3.2], right_cols=(2,), size=8.5)
figure(doc, fig_topics, "Figure 3. Topics in comments, coloured by group")
para(doc, f"Campaign reactions account for {pc(group_share.get('Campaign reactions', 0))} of meaningful comments, shopping needs for "
          f"{pc(group_share.get('Shopping needs', 0))} and complaints for {pc(group_share.get('Complaints and criticism', 0))}. "
          f"Sentiment is {pc(sent.get('positive', 0), 0)} positive, {pc(sent.get('neutral', 0), 0)} neutral and "
          f"{pc(sent.get('negative', 0), 0)} negative. Languages: {', '.join(f'{k} {pc(v, 0)}' for k, v in lang.items())}. "
          f"Purchase intent is high in {pc(inten.get('high', 0))} and medium in {pc(inten.get('medium', 0))} of comments.")
figure(doc, fig_brand, "Figure 4. Topic groups by brand")
para(doc, "Interpretation: the audience mostly reacts to the campaign film itself (the song, the celebrity, the design). Only a "
          "small share states a shopping need, but those comments carry clear buying intent and are the most actionable for the brand.")

heading(doc, "5.3 Which marketing patterns are unusual?", 2)
dirs = flag["anomaly_direction"].value_counts()
para(doc, f"An Isolation Forest compared each post with its own brand's usual views per day, like rate, comments and comment rate. "
          f"It flagged {len(flag)} posts ({pc(len(flag) / N_POSTS * 100)}): "
          + ", ".join(f"{v} {k}" for k, v in dirs.items()) + ". "
          f"Among flagged posts, {r_comm} have unusual comment counts or comment rates, {r_like} an unusual like rate and "
          f"{r_view} unusual views per day.")
figure(doc, fig_anom, "Figure 5. Posts and unusual posts")
caption(doc, "Table 8. The eight most unusual posts")
rows = [[r["brand"], short(r["video_title"], 42), f"{int(r['views']):,}", f"{int(r['likes']):,}", f"{int(r['comments_count']):,}",
         short(str(r["anomaly_reason"]).split(";")[0], 36)] for _, r in flag.head(8).iterrows()]
table(doc, ["Brand", "Video title", "Views", "Likes", "Comments", "Main reason"], rows,
      [0.8, 2.3, 0.8, 0.6, 0.7, 1.6], right_cols=(2, 3, 4), size=8)
para(doc, "These posts are leads for investigation, not errors. Some have more likes than views, which can happen when a video's "
          "view count is very low or delayed. The brand should open each one and ask whether a giveaway, a celebrity or a different format explains it.")

heading(doc, "5.4 Which audience needs should the brand address?", 2)
if csum is not None and fig_seg:
    all_s = csum[csum["group"] == "all"].sort_values("size", ascending=False)
    k_all = int((all_s["cluster"] >= 0).sum())
    sil = ""
    if ksel is not None:
        r = ksel[(ksel["group"] == "all") & (ksel["k"] == k_all)]
        if len(r):
            sil = f" The chosen solution (k = {k_all}) has a silhouette score of {r['silhouette'].iloc[0]:.3f}, a low value that is typical for short social-media text."
    para(doc, "Comments with real content were clustered with TF-IDF, SVD and K-Means. Very short generic reactions such as 'nice' "
              "carry no distinguishing words and form one segment. Segment labels are generated automatically from the most distinctive "
              "topic; read them together with the top words and example comments." + sil)
    figure(doc, fig_seg, "Figure 6. Audience segments")
    caption(doc, "Table 9. Audience segments")
    rows = [[seg_label(r), f"{int(r['size']):,}", f"{r['share_pct']:.1f}%", f"{r['pct_positive']:.0f}%", f"{r['pct_buying_intent']:.0f}%"]
            for _, r in all_s.iterrows()]
    table(doc, ["Segment [top words]", "Comments", "Share", "Positive", "Buying intent"], rows,
          [3.5, 0.8, 0.7, 0.8, 0.9], right_cols=(1, 2, 3, 4), size=8)
    shop_s = csum[csum["group"] == "shopping"].sort_values("size", ascending=False)
    if len(shop_s) and fig_shop:
        para(doc, f"The {int(shop_s['size'].sum()):,} comments about price, availability, discounts, delivery, quality, size or buying "
                  "were clustered again. These are the shopping needs the brief asks about.")
        figure(doc, fig_shop, "Figure 7. Shopping-need segments")
        caption(doc, "Table 10. Shopping-need segments")
        rows = [[seg_label(r), f"{int(r['size']):,}", f"{r['pct_positive']:.0f}%", f"{r['pct_negative']:.0f}%", f"{r['pct_buying_intent']:.0f}%",
                 short(str(r["examples"]).split(" || ")[0], 40)] for _, r in shop_s.iterrows()]
        table(doc, ["Segment [top words]", "Comments", "Positive", "Negative", "Buying intent", "Example"], rows,
              [2.1, 0.7, 0.6, 0.6, 0.7, 2.0], right_cols=(1, 2, 3, 4), size=8)
else:
    para(doc, "Comment clusters were not found. Run src/cluster_comments.py and generate the report again.")
if pprof is not None:
    caption(doc, "Table 11. Post groups (K-Means on engagement)")
    rows = [[r["name"], f"{int(r['posts']):,}", f"{r['share_pct']:.1f}%", f"{r['median_views_per_day']:.1f}", f"{r['median_like_rate']:.2f}",
             f"{r['mean_comments']:.1f}"] for _, r in pprof.sort_values("posts", ascending=False).iterrows()]
    table(doc, ["Post group", "Posts", "Share", "Median views per day", "Median like rate", "Mean comments"], rows,
          [2.4, 0.6, 0.6, 1.0, 1.0, 1.0], right_cols=(1, 2, 3, 4, 5), size=8.5)

heading(doc, "5.5 What should the marketing team do next?", 2)
para(doc, "The recommendations below are generated from the evidence in this report and are also shown in the web application.", after=3)
caption(doc, "Table 12. Evidence-based recommendations")
rows = [[r["area"], r["priority"], r["title"], short(r["evidence"], 230), short(r["action"], 150)] for r in recs]
table(doc, ["Area", "Priority", "Recommendation", "Evidence", "Action"], rows, [0.8, 0.6, 1.4, 2.4, 1.5], size=7.5)
para(doc, "Each recommendation also has a way to test it (see the web application). Evidence shows association, not cause, so the "
          "last recommendation proposes three simple A/B tests.")

# ---- 6 traceability
heading(doc, "6. How the Brief Is Met")
table(doc, ["Requirement in the brief", "How it is met"],
      [["Small web application answering five questions", "Streamlit app with one page per question plus Overview and Data and method"],
       ["Group similar audience behaviour (clustering)", "TF-IDF with K-Means on comments (audience and shopping segments) and K-Means on posts"],
       ["Identify unusual patterns", "Isolation Forest per brand; flagged posts with reasons and links"],
       ["Audience needs (discount, delivery, availability)", "Topic labels and shopping-need segments (Section 5.4)"],
       ["Evidence-based recommendations", "Content, promotions, customer service and campaign testing (Section 5.5)"],
       ["One product category, about 2,000 to 5,000 records", f"Women's lawn; {N_POSTS:,} posts and {N_REAL:,} comments"],
       ["Table A and Table B fields", "posts.csv and comments.csv contain every listed field (shares empty: not available)"],
       ["Source, date, provenance, reuse; real vs simulated", "Section 3.3 and provenance columns; all records are real"],
       ["Permitted access, platform terms", "Official YouTube Data API only; no scraping; Instagram, TikTok and Facebook not used"]],
      [2.9, 3.8], size=8.5)

# ---- 7 web app
heading(doc, "7. The Web Application")
para(doc, "The app is built with Streamlit and Plotly. Pages: Overview; 1. What content attracts attention; 2. What the audience wants; "
          "3. Unusual patterns; 4. Audience segments; 5. Recommendations; Data and method.")
bullet(doc, " streamlit run app/app.py (after installing requirements.txt).", "Run locally:")
if APP_URL:
    bullet(doc, f" {APP_URL}", "Live app:")
bullet(doc, f" {GITHUB_URL}", "Source code:")
para(doc, "Repository structure: src/ (collection and analysis scripts), app/ (web application), data/raw and data/processed (datasets), "
          "reports/ and images/ (outputs), README.md, requirements.txt, .gitignore and .env.example. The API key is kept in a local "
          ".env file that is never committed.")

# ---- 8 limitations
heading(doc, "8. Limitations and Ethics")
for b in ("Only YouTube was used; results may differ on Instagram, TikTok or Facebook, and shares could not be measured.",
          "Five brands and mostly Shorts; some campaign types have few posts.",
          "Most posts have no comments, so comment findings come from a small set of posts.",
          "Keyword rules and VADER are weaker on Roman Urdu and mixed-language comments; "
          + ("comment labels were hand-validated on a sample." if COMMENT_VALIDATION else "comment labels were not hand-validated."),
          "Clusters of short text are only weakly separated (low silhouette); segment labels are interpretations.",
          "Engagement shows association, not cause. Recommendations are starting points for testing.",
          "Ethics: public data only, official API, hashed usernames, masked personal details, no attempt to identify individuals."):
    bullet(doc, b)

# ---- 9 conclusion
heading(doc, "9. Conclusion and Future Work")
para(doc, f"The engine shows that, on YouTube, Pakistani lawn campaigns mostly attract reactions to the film itself (song, celebrity and "
          f"design), while only about {pc(shop_share, 0)} of comments state a shopping need. Clustering turned thousands of comments into a "
          f"handful of audience segments, the anomaly detector pointed to {len(flag)} posts worth investigating, and the "
          f"recommendation engine converted the findings into concrete actions: credit the song, show prices and availability, answer "
          f"service comments quickly, and test formats and campaigns. Future work: add Instagram and TikTok through approved business "
          f"access, link posts to product prices from a catalogue, add transaction data, and hand-label a larger sample to measure the "
          f"accuracy of the comment labels.")

# ---- appendix
heading(doc, "Appendix: Chosen k for the clustering")
if ksel is not None:
    rows = [[r["group"], int(r["k"]), f"{r['silhouette']:.3f}", f"{r['davies_bouldin']:.2f}", f"{r['smallest_cluster_pct']:.1f}%"]
            for _, r in ksel.iterrows() if "davies_bouldin" in ksel.columns]
    if rows:
        caption(doc, "Table 13. Silhouette and Davies-Bouldin scores for each k tried (comment clustering)")
        table(doc, ["Group", "k", "Silhouette", "Davies-Bouldin", "Smallest cluster"], rows, [1.4, 0.6, 1.0, 1.2, 1.2],
              right_cols=(1, 2, 3, 4), size=8.5)

doc.save(OUT)
print(f"Report saved: {OUT}")
