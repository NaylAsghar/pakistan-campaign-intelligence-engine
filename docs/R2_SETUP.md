# Cloudflare R2 setup (do this when you want real cloud storage)

Phases 3 and 5 work with local disk (`STORAGE_BACKEND=local`, the default).
Switch to R2 to test the real thing, and before you deploy. Product images,
videos and thumbnails all go to the same bucket (under `products/`, `videos/`
and `thumbnails/`).

1. Create a free Cloudflare account -> **R2 Object Storage** -> **Create bucket**
   (for example `product-videos`). Keep the bucket **private**; the app gives
   the browser short-lived signed links. R2 needs a payment method on file, but the
   free tier (10 GB) is enough for this project.
2. **R2 -> Manage API tokens -> Create API token**, permission
   **Object Read & Write**, limited to your bucket. Copy the Access Key ID and
   Secret Access Key (the secret is shown only once).
3. Note your **Account ID** (R2 overview page). The endpoint is
   `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`.
4. Bucket -> **Settings -> CORS policy** -> add (so the browser may PUT files
   straight to R2; add your deployed URL to AllowedOrigins later):
   ```json
   [
     {
       "AllowedOrigins": ["http://localhost:8000"],
       "AllowedMethods": ["PUT", "GET"],
       "AllowedHeaders": ["Content-Type"],
       "MaxAgeSeconds": 3600
     }
   ]
   ```
5. In `.env`:
   ```
   STORAGE_BACKEND=s3
   S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
   S3_ACCESS_KEY=<Access Key ID>
   S3_SECRET_KEY=<Secret Access Key>
   S3_BUCKET=product-videos
   S3_REGION=auto
   ```
6. Restart the app, then upload an image (Products) and a small video (Videos).
   In the R2 dashboard you should see the new objects. Never commit `.env`.

## If an upload to R2 fails

- The progress bar reaches 100% and then the page shows "Upload failed. Check
  your connection (and the R2 CORS settings...)": the CORS policy in step 4 is
  missing or does not list your site in `AllowedOrigins`. Press F12 -> Console
  to see the exact CORS message. `PUT` and the `Content-Type` header must be allowed.
- "403" on upload: the API token in `.env` does not have *Object Read & Write*
  for this bucket, or the link expired (links last 10 minutes).
- Video plays but cannot be dragged to a later time: nothing to fix on R2,
  it supports this. Hard-refresh the page (Ctrl+F5).
- Switching `STORAGE_BACKEND` does not move old files. Files uploaded to local
  disk are not in R2. Use fresh test data after switching.
