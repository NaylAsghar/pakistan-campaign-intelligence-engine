# Cloudflare R2 setup (do this later, when you want real cloud storage)

Phase 3 works with local disk (`STORAGE_BACKEND=local`, the default). Switch to
R2 when you deploy, or earlier if you want to test it.

1. Create a free Cloudflare account -> **R2 Object Storage** -> **Create bucket**
   (for example `product-videos`). R2 needs a payment method on file, but the
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
6. Restart the app and upload an image. Never commit `.env`.
