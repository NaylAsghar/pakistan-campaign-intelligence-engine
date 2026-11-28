[README (1).md](https://github.com/user-attachments/files/35552579/README.1.md)
# Pakistan Campaign Intelligence Engine + Product Video Scheduler

Big Data Analytics course project by **Nayl Asghar** (F2024376143). The repository holds two connected projects:

| Project | What it is | Where |
|---|---|---|
| **Project 4** (this README) | **Automated Product Video Scheduling and YouTube Publishing Platform**: upload product videos, pick a date and time, and they are published to YouTube automatically | `publisher/` |
| **Project 3** | **Pakistan Social Audience and Campaign Intelligence Engine**: clustering and anomaly detection on 1,959 YouTube posts and 3,873 comments of five Pakistani lawn brands | `app/`, `data/` |

Project 4 continues Project 3: the Project 3 findings feed the insights pages, and the platform reads real YouTube statistics back after publishing.

| Link | Address |
|---|---|
| Project 3 live app | https://pakistan-campaign-intelligence-enginegit-6sgfeservyb8cbumsyffm.streamlit.app/ |

---

## What Project 4 does

1. **Accounts** - register, log in, log out. Every record belongs to one user and is invisible to others.
2. **Products** - name, description and up to 10 images per product from different angles (each with an angle label).
3. **YouTube connection** - connect a channel through Google's official OAuth 2.0 screen. The refresh token is stored encrypted.
4. **Videos** - upload finished videos (made with any external tool) straight to Cloudflare R2, preview them, edit title, description, tags, privacy and thumbnail.
5. **Scheduling** - choose a date and time in Pakistan time (Asia/Karachi); it is stored as UTC. Edit or delete a post while it is still scheduled.
6. **Automatic publishing** - a background worker uploads the video to YouTube at the chosen time with its title, description, tags, thumbnail and visibility, then saves the YouTube link and status.
7. **Dashboard** - counts of scheduled, processing, published and failed posts, the schedule list, published links, and a retry for failed posts.
8. **Insights and statistics** - suggestions based on Project 3 (posting time, title and description helper, A/B variants) and a loop that reads views, likes and comments of published videos back from YouTube.

### How a post is published

```
scheduled --claim--> processing --ok--------------------------> published
                         |
                         +-- temporary error, tries left ---> scheduled (waits, tries again)
                         +-- permanent error / 5th failure --> failed
```

- **Atomic claim:** one `UPDATE ... WHERE status='scheduled'` decides which worker gets a post, so a post is never published twice.
- **Resumable upload:** the file goes to YouTube in 8 MB chunks; the session address is saved, so an interrupted upload continues from the last byte instead of starting over.
- **Retries:** waits of 2, 10, 30 and 120 minutes; a used-up daily YouTube quota waits 3 hours; the 5th failure ends as `failed` with a readable message. Permanent problems (video rejected, access revoked, file missing) fail at once.
- **Thumbnail trouble never fails a post:** the video is already live, so the post is `published` with a note.
- **Abandoned posts** stuck in `processing` for 15 minutes go back to `scheduled`.

More detail: [`docs/WORKER.md`](docs/WORKER.md).

---

## Technology

| Part | Technology |
|---|---|
| Web app | Python, FastAPI, Jinja2 |
| Database | SQLAlchemy - SQLite for development, a hosted database in production (`DATABASE_URL`) |
| File storage | Cloudflare R2 (S3-compatible) via boto3; local folder for development and tests |
| Passwords and sessions | bcrypt, signed cookie sessions (14 days, SameSite=Lax) |
| YouTube | Google OAuth 2.0 (PKCE) and YouTube Data API v3 via httpx |
| Token protection | Fernet encryption |
| Tests | pytest (192 tests, no internet needed) |

---

## Project structure

```
publisher/                Project 4 application
  main.py                 app, session middleware, routers, health route
  config.py               all settings, read from the environment
  models.py, db.py        tables; db.py also adds new columns to older databases
  auth.py, security.py    register/login/logout, bcrypt
  products.py             products and multi-angle images
  videos.py, uploads.py   video and thumbnail upload, preview, edit, delete
  storage.py              R2 and local storage, presigned URLs, file checks
  youtube.py              connect/disconnect pages (OAuth callback)
  google_oauth.py         OAuth flow, token refresh
  crypto.py               Fernet encryption of refresh tokens
  schedules.py, timeutil.py   scheduling pages, Karachi <-> UTC conversion
  youtube_api.py          resumable upload, thumbnail, error classification
  worker.py               claim, publish, retry, recover, command line
  cron.py                 /cron/publish for cron services
  dashboard.py, insights.py   dashboard and Project 3 insights
  templates/, static/     pages, CSS, upload JavaScript
tests/                    automated tests
docs/                     PHASES.md, R2_SETUP.md, WORKER.md
app/, data/               Project 3 (Streamlit app and data)
```

---

## Run it on your computer

Requirements: Python 3.11 or newer and Git.

### 1. Get the code and install

```powershell
git clone https://github.com/NaylAsghar/pakistan-campaign-intelligence-engine.git
cd pakistan-campaign-intelligence-engine
python -m venv venv
.\venv\Scripts\Activate.ps1          # macOS/Linux: source venv/bin/activate
pip install -r requirements-publisher.txt
```

### 2. Create your `.env`

Copy `.env.example` to `.env`, then make two secret keys:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"                           # SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # FERNET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"                           # CRON_TOKEN
```

| Variable | Meaning | Default |
|---|---|---|
| `SECRET_KEY` | signs the session cookie | **required** |
| `FERNET_KEY` | encrypts YouTube refresh tokens | **required** |
| `DATABASE_URL` | database address | `sqlite:///./publisher.db` |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google OAuth client | empty |
| `GOOGLE_REDIRECT_URI` | return address after Google sign-in | `http://localhost:8000/youtube/callback` |
| `STORAGE_BACKEND` | `local` or `s3` (Cloudflare R2) | `local` |
| `LOCAL_STORAGE_DIR` | folder for the local backend | `./storage` |
| `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_REGION` | Cloudflare R2 connection | empty |
| `CRON_TOKEN` | secret for `/cron/publish` (empty = switched off) | empty |
| `APP_TIMEZONE` | time zone shown to users | `Asia/Karachi` |
| `SESSION_COOKIE_SECURE` | `true` only behind HTTPS | `false` |

**Never commit `.env`.** It is listed in `.gitignore`.

With `STORAGE_BACKEND=local` you need no cloud account to try the app (files go to `./storage`).

### 3. Google OAuth (needed only to connect YouTube)

1. In [Google Cloud Console](https://console.cloud.google.com/) create a project and enable **YouTube Data API v3**.
2. **OAuth consent screen:** choose External, and while it is in *Testing* add your own Google account as a **test user**.
3. **Credentials -> Create OAuth client ID -> Web application.** Add the authorised redirect URI `http://localhost:8000/youtube/callback` (and later your live address, e.g. `https://YOUR-APP.onrender.com/youtube/callback`).
4. Put the client id and secret in `.env`. The app asks for two permissions only: upload videos and read channel information.

### 4. Cloudflare R2 (for real cloud storage)

Follow [`docs/R2_SETUP.md`](docs/R2_SETUP.md): create a bucket, create an API token, allow browser uploads in the bucket's CORS settings, and fill the `S3_*` variables with `STORAGE_BACKEND=s3`.

### 5. Start the app and the worker

Two terminals, both with the venv active:

```powershell
uvicorn publisher.main:app --reload          # terminal 1: web app at http://localhost:8000
python -m publisher.worker                   # terminal 2: publisher worker (Ctrl+C to stop)
```

`python -m publisher.worker --once` runs a single pass and prints how many posts were published, retried or failed.

### 6. Try it end to end

1. Register, then create a product and add images.
2. **YouTube** page -> Connect, sign in with the Google test account.
3. **Videos** -> upload a short mp4, set the privacy to **Private** for testing.
4. **Schedules** -> new schedule about 6 minutes ahead (the minimum is 5).
5. Wait with the worker running: the status changes to `processing`, then `published`, with a link to the video.

---

## Tests

```powershell
pytest -q
```

192 tests cover accounts, ownership, uploads, YouTube connection, scheduling, the worker and the dashboard. They use a temporary folder and a fake YouTube service that behaves like YouTube's resumable upload protocol (including interrupted and resumed uploads), so they run without the internet.

---

## Deploy (Render example)

1. Use a hosted database (for example Render Postgres) and set `DATABASE_URL`. A SQLite file on free hosting is erased at every redeploy.
2. Create a **Web Service** from this GitHub repository:
   - Build command: `pip install -r requirements-publisher.txt`
   - Start command: `uvicorn publisher.main:app --host 0.0.0.0 --port $PORT`
3. Add all environment variables from the table above in the service's Environment tab, with `SESSION_COOKIE_SECURE=true`, `STORAGE_BACKEND=s3` and `GOOGLE_REDIRECT_URI=https://YOUR-APP.onrender.com/youtube/callback`. Add the same redirect address in Google Cloud Console.
4. **Cron ping.** A free host runs only the web app, so use a cron service (for example cron-job.org) to call the worker every 1 to 2 minutes:

   ```
   POST https://YOUR-APP.onrender.com/cron/publish
   Header:  X-Cron-Token: <your CRON_TOKEN>
   ```

   The call returns `202` immediately and the worker pass runs in the background. A wrong token gets `403`; if `CRON_TOKEN` is empty the address is switched off. The ping also wakes a sleeping free service. If the cron service reports a timeout on the first call after sleep, raise its timeout to 60 seconds.

---

## Security

- Passwords are stored only as salted bcrypt hashes; every login failure shows the same message.
- Session cookies are signed, SameSite=Lax, and Secure on HTTPS.
- Google sign-in uses PKCE and a checked `state` value; refresh tokens are encrypted with Fernet and never shown or logged.
- Uploads use one-time links valid for 10 minutes; the server checks the file size and first bytes afterwards.
- Another user's product, video or schedule answers 404.
- `/cron/publish` needs a secret token compared in constant time.
- Secrets live only in environment variables, never in Git.

---

## Known limits

- **Quota:** one upload costs 1,600 of YouTube's 10,000 daily units, so about **6 uploads a day** at the default quota.
- **Private uploads:** videos uploaded through a Google Cloud project that has not passed Google's API audit are forced to *private* by YouTube, whatever visibility you pick. The app is working correctly when this happens.
- **Testing mode:** while the Google consent screen is in *Testing*, refresh tokens expire after 7 days; press **Connect** again if publishing says "Reconnect the channel".
- **Thumbnails** need a phone-verified YouTube channel.
- **Free hosting** sleeps when idle, so a post can start a minute or two late.
- Video generation is out of scope: make videos with any external tool and upload them.

## Troubleshooting

| You see | Do this |
|---|---|
| Post stays `scheduled` after its time | Is the worker running (or the cron ping set up)? Try `python -m publisher.worker --once` |
| `Attempt 1 of 5 failed: Could not get YouTube access` | Check `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, then restart |
| `Reconnect the channel` | Connect the channel again on the YouTube page |
| `The video file is missing from storage` | Upload the video again and schedule the new one |
| `daily upload quota is used up` | Wait; the worker retries by itself after 3 hours |
| Cannot log in on the live site | Set `SESSION_COOKIE_SECURE=true` only on HTTPS, and `false` on `http://localhost` |

---

## Project 3 (summary)

Project 3 collected public data with the YouTube Data API v3 (1,959 posts and 3,873 comments from Sapphire, Khaadi, Gul Ahmed, Alkaram and Sana Safinaz), grouped audience behaviour with K-Means, flagged unusual posts with Isolation Forest and turned the findings into recommendations. See the Project 3 report and the [live Streamlit app](https://pakistan-campaign-intelligence-enginegit-6sgfeservyb8cbumsyffm.streamlit.app/).

## Author

Nayl Asghar - F2024376143 - Big Data Analytics - coursework project, 2026.
