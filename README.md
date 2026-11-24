# Pakistan Social Audience and Campaign Intelligence Engine

Clustering-based analysis of YouTube posts and comments of Pakistani women's lawn brands.
Big Data Analytics, Project 3 — Nayl Asghar (F2024376143)

**Live app:** https://pakistan-campaign-intelligence-enginegit-6sgfeservyb8cbumsyffm.streamlit.app/
**Repository:** https://github.com/NaylAsghar/pakistan-campaign-intelligence-engine

## What this project does

A small Streamlit web application that helps a Pakistani lawn brand understand its social audience. It groups similar audience behaviour with clustering, flags unusual posts with anomaly detection, and turns the findings into evidence-based marketing recommendations.

It answers five marketing questions:

1. **Which content attracts attention?** Campaigns, content types and brands compared by views, likes and comments.
2. **What does the audience want?** Comments grouped by topic: price, fabric quality, sizing, delivery, discounts, availability, purchase interest.
3. **Which marketing patterns are unusual?** Posts that differ strongly from their own brand's usual level (Isolation Forest).
4. **Which audience needs should the brand address?** Audience and shopping-need segments (TF-IDF + SVD + K-Means).
5. **What should the marketing team do next?** Recommendations for content, promotions, customer service and campaign testing.

## Data

| | |
|---|---|
| Source | YouTube Data API v3 (public videos and comments of official brand channels) |
| Brands | Sapphire, Khaadi, Gul Ahmed, Alkaram, Sana Safinaz Official |
| Product category | Women's lawn suits and seasonal women's wear |
| Size | 1,959 posts (Table A) and 3,873 real comments (Table B) |
| Collection date | 2026-11-13 |
| Real vs simulated | All records are real. No simulated or generated records (see the `data_origin` column). |

**Why YouTube?** Instagram, TikTok and Facebook only return comments for accounts you manage, and scraping would break their terms. YouTube's official API permits reading public videos and comments. Shares are not available from YouTube, so the `shares` column is empty.

**Privacy.** Usernames are replaced by a one-way hash, and emails, phone numbers and @handles are masked in comment text. No attempt is made to identify individuals. The data is shared for coursework assessment only.

### Table A — `data/processed/posts.csv`

`post_id, brand, platform, campaign, content_type, product_category, likes, comments_count, shares, views, days_since_post` plus provenance columns (`source_url`, `collection_date`, `data_origin`, `video_id`, `video_title`, `published_at`, `duration_sec`, ...).

### Table B — `data/processed/comments.csv`

`post_id, comment_text, language_type, topic, sentiment, purchase_intent` plus `author_hash`, `is_spam`, `like_count`, `topics_all` and one True/False column per topic.

Labels (topic, language, intent) come from keyword dictionaries in English, Roman Urdu and Urdu; sentiment uses VADER plus an Urdu/Roman Urdu word list. They were not validated against a large hand-labelled sample, so treat them as indicative.

## Method in brief

| Step | Method |
|---|---|
| Collect | YouTube Data API v3 (channel uploads, video statistics, top-level comments, max 300 per post) |
| Filter and label posts | Keyword rules on titles; video duration for Shorts |
| Label comments | Keyword dictionaries; VADER |
| Audience segments | TF-IDF, SVD, K-Means (k chosen by silhouette score) |
| Post groups | K-Means on views per day, like rate and comment measures |
| Unusual posts | Isolation Forest on z-scores within each brand |
| Significance tests | Mann-Whitney and Kruskal-Wallis |
| Recommendations | Rules that turn counts and tests into actions |

Engagement is measured per view: like rate = likes per 100 views, comment rate = comments per 1,000 views, views per day = views / days since posting.

## Key findings

- Song and music is the largest comment topic (34.3%); only about 8% of comments express shopping needs.
- "Spring Lawn" posts have the highest median like rate; differences between campaigns are significant (Kruskal-Wallis p < 0.0001). Short versus longer video is not significant (p = 0.1076).
- 98 posts (5.0%) are flagged as unusual and listed for investigation.
- Only 32.6% of posts have any comments, so conversation is concentrated on a few posts.
- 10 recommendations, for example: show the price and availability in descriptions, credit the song and singer, and answer delivery comments quickly.

## Repository structure

```
app/                 Streamlit web application (app.py)
src/                 Collection, labelling and analysis scripts
data/raw/            Raw collected data
data/processed/      posts.csv, comments.csv, cluster outputs
reports/             Summary CSV files and the generated Word report
images/              Charts
requirements.txt     Python dependencies
.env.example         Template for the API key file
```

## Run the app locally

```bash
git clone https://github.com/NaylAsghar/pakistan-campaign-intelligence-engine.git
cd pakistan-campaign-intelligence-engine
pip install -r requirements.txt
streamlit run app/app.py
```

The processed data is already included, so no API key is needed to run the app.

App pages: Overview · 1. Content · 2. Audience wants · 3. Unusual patterns · 4. Segments · 5. Recommendations · Data and method.

## Reproduce the pipeline (optional)

Run every script from the project root. This needs a YouTube Data API key.

1. Copy `.env.example` to `.env` and paste your key: `YOUTUBE_API_KEY=your_key_here`. The `.env` file is git-ignored and must never be committed.
2. Run the steps in order:

```bash
python src/find_channels.py "Sapphire Pakistan"   # find a brand's channel ID (brands are set in src/config.py)
python src/check_channels.py                      # check channels before collecting
python src/census_channels.py                     # list all videos and comment counts
python src/build_posts.py                         # build Table A (posts.csv)
python src/collect_comments.py                    # collect raw comments (resumable if quota runs out)
python src/label_comments.py                      # build Table B (comments.csv)
python src/cluster_comments.py                    # audience and shopping-need segments
python src/cluster_posts.py                       # post groups, unusual posts, engagement tables
python src/generate_report.py                     # optional: Word report from the data
```

## Limitations

- Only YouTube was used; results may differ on Instagram, TikTok or Facebook, and shares could not be measured.
- Five brands and mostly Shorts (about 92% of posts); some campaign types have few posts.
- Most posts have no comments, so comment findings come from a small set of posts.
- Rule-based labels and VADER are weaker on Roman Urdu and mixed-language comments.
- Clusters of short text are only weakly separated (silhouette 0.061); segment labels are interpretations.
- Engagement shows association, not cause; recommendations are starting points for A/B testing.

## Future work

Add Instagram and TikTok through approved business access, link posts to product prices from a catalogue, add transaction data, and hand-label a larger sample to measure the accuracy of the comment labels.

## Data and platform terms

Data was collected through the official YouTube Data API v3 and is used for coursework analysis under the YouTube API terms. Brand names and content belong to their respective owners.
