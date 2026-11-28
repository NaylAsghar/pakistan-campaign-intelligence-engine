# Publisher worker (Phase 7)

The worker is the part of the app that actually publishes. The web pages only
*plan* a post (Phase 6). The worker looks for posts whose time has come and
uploads them to YouTube.

## How to run it

From the repo root, venv active, `.env` filled in:

    python -m publisher.worker --once     # one pass, then exit
    python -m publisher.worker            # keep running, look every 30 seconds
    python -m publisher.worker --poll 10  # look every 10 seconds

Or over HTTP (for the free host in Phase 11, where only the web app runs):

    POST or GET  /cron/publish        header  X-Cron-Token: <CRON_TOKEN>

A cron service calls that URL every 1-2 minutes. The call returns `202` straight
away and the pass runs in the background. If `CRON_TOKEN` is empty in `.env`
the URL is switched off (503); a wrong token gets 403.

## What one pass does

1. **Recover** posts stuck in `processing` for more than 15 minutes (the worker
   died). They go back to `scheduled`, keeping their resume point.
2. **Claim** the oldest due post with one atomic `UPDATE ... WHERE status='scheduled'`.
   If two workers race, only one UPDATE matches a row, so a post is never
   published twice. Due means: status `scheduled`, publish time reached,
   and no retry wait pending.
3. **Publish**: download the video from storage, upload it to YouTube in 8 MB
   chunks (resumable), set the thumbnail, save the YouTube link.
4. Repeat, at most 10 posts per pass.

## Statuses

    scheduled --claim--> processing --ok--------------------------> published
                              |
                              +-- temporary error, tries left ---> scheduled (waits, then tries again)
                              +-- permanent error / 5th failure --> failed

## Retry rules

| What went wrong | Result |
|---|---|
| Network problem, YouTube 5xx / 429, storage hiccup, unexpected bug | retry; waits 2, 10, 30, then 120 minutes |
| Daily YouTube quota used up | retry in 3 hours |
| 5th failed attempt | `failed`, message starts "Gave up after 5 attempts" |
| YouTube rejects the video or its details (400, 403 forbidden) | `failed` at once |
| YouTube access revoked (`invalid_grant`) | `failed` at once: "Reconnect the channel" |
| Video file missing from storage | `failed` at once |
| Thumbnail could not be set | still `published`; a note says the thumbnail was not set |

Small network blips inside one upload are retried 3 times right away, without
using up an attempt.

## Resumable upload

YouTube gives each upload a session address. We save it in `schedules.upload_session_url`.
If an attempt is interrupted, the next attempt asks YouTube how many bytes it
already has and sends only the rest. Sessions last about a week; an expired
one is replaced automatically. The address is treated as a secret (never shown
in a page or log) and is only ever used if it points at `https://www.googleapis.com/`.

Pressing Ctrl+C in the worker terminal during an upload hands the post back at
once (the interrupted attempt does not count) and keeps the resume point, so
starting the worker again continues the upload.

As soon as YouTube confirms the upload, the video id is saved. A later retry
(for example after a crash during the thumbnail step) will not upload the
video again.

## YouTube limits to know about

* **Quota:** `videos.insert` costs 1,600 units, `thumbnails.set` 50. The default
  quota is 10,000 units a day, about **6 uploads a day**. It resets at midnight Pacific time.
* **Unverified API projects:** videos uploaded through a Google Cloud project
  that has not passed Google's API audit are forced to **private**, whatever
  privacy the video has in this app. The app is working correctly when that
  happens; mention it in the report.
* **Custom thumbnails** need a phone-verified YouTube channel.
* **Testing mode:** while the OAuth consent screen is in "Testing", Google
  expires refresh tokens after 7 days. If publishing fails with
  "Reconnect the channel", press Connect on the YouTube page again.

## Troubleshooting

| You see | Do this |
|---|---|
| Post stays `scheduled` after its time | Is the worker running? Look at its terminal. `python -m publisher.worker --once` prints the counts. |
| `Attempt 1 of 5 failed: Could not get YouTube access` | Check `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in `.env`, restart. |
| `YouTube refused the request (403)` | The channel or project may lack upload permission; reconnect the channel. |
| `The video file is missing from storage` | Upload the video again and schedule the new one. |
| `daily upload quota is used up` | Wait; it retries by itself. |
| Video appears on YouTube as **private** though you chose public | Unverified API project (see above). |
| Post stuck in `processing` | If the worker was killed, it recovers by itself after 15 minutes. |
