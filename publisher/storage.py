"""Where uploaded files live: local disk (development) or Cloudflare R2.

Both backends offer the same small interface, so the routes never care
which one is active (STORAGE_BACKEND in .env picks it):

    make_upload(key, content_type) -> where the browser should PUT the file
    stat(key)                      -> size and type of a stored file, or None
    delete(key)                    -> remove a file (no error if missing)
    serve(key, content_type)       -> an HTTP response that shows the file

Upload flow (the same for both backends):
    1. browser asks our server for an upload URL      (presign)
    2. browser PUTs the file to that URL              (local disk or R2)
    3. browser tells our server the upload is done    (confirm)
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.responses import FileResponse, RedirectResponse, Response

from publisher.config import get_settings

# Image types we accept, and the file extension we store them under.
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
EXT_TO_TYPE = {ext: ctype for ctype, ext in IMAGE_TYPES.items()}

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB per image
UPLOAD_URL_SECONDS = 600  # an upload link works for 10 minutes

# Keys are made by the server only: products/<product id>/<32 hex>.<ext>
_KEY_RE = re.compile(r"^products/(\d+)/[0-9a-f]{32}\.(?:jpg|png|webp)$")


def new_image_key(product_id: int, content_type: str) -> str:
    """A fresh, unguessable storage key for one image of a product."""
    return f"products/{product_id}/{uuid.uuid4().hex}.{IMAGE_TYPES[content_type]}"


def product_id_from_key(key: str) -> int | None:
    """The product id inside a valid image key, or None if the key is not valid."""
    match = _KEY_RE.match(key)
    return int(match.group(1)) if match else None


def looks_like_image(data: bytes, content_type: str) -> bool:
    """Check the first bytes of a file, not just what the browser claims."""
    if content_type == "image/jpeg":
        return data[:3] == b"\xff\xd8\xff"
    if content_type == "image/png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if content_type == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


@dataclass
class UploadTarget:
    """Everything the browser needs to upload one file."""

    url: str
    method: str = "PUT"
    headers: dict = field(default_factory=dict)


@dataclass
class ObjectInfo:
    size: int
    content_type: str


# ------------------------------------------------------------- local disk


class LocalStorage:
    """Files in a folder on this machine. Used for development and tests."""

    def __init__(self, root: str | Path, secret_key: str):
        self.root = Path(root).resolve()
        self._signer = URLSafeTimedSerializer(secret_key, salt="local-upload")

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:  # blocks keys like "../../x"
            raise ValueError("invalid storage key")
        return path

    def make_upload(self, key: str, content_type: str) -> UploadTarget:
        token = self._signer.dumps({"key": key, "ct": content_type})
        return UploadTarget(
            url=f"/storage/local-upload?token={token}",
            headers={"Content-Type": content_type},
        )

    def read_upload_token(self, token: str) -> dict | None:
        """The {key, ct} inside a valid, unexpired token; otherwise None."""
        try:
            return self._signer.loads(token, max_age=UPLOAD_URL_SECONDS)
        except BadSignature:  # also covers expired tokens
            return None

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def stat(self, key: str) -> ObjectInfo | None:
        path = self._path(key)
        if not path.is_file():
            return None
        content_type = EXT_TO_TYPE.get(path.suffix.lstrip("."), "")
        return ObjectInfo(size=path.stat().st_size, content_type=content_type)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def serve(self, key: str, content_type: str | None = None) -> Response:
        path = self._path(key)
        if not path.is_file():
            return Response(status_code=404)
        media_type = content_type or EXT_TO_TYPE.get(path.suffix.lstrip("."))
        return FileResponse(path, media_type=media_type)


# --------------------------------------------------------- Cloudflare R2


class S3Storage:
    """Cloudflare R2 (or any S3-compatible bucket) through boto3."""

    def __init__(self, endpoint_url, access_key, secret_key, bucket, region):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region or "auto",  # R2 uses the region name "auto"
            config=Config(signature_version="s3v4"),
        )

    def make_upload(self, key: str, content_type: str) -> UploadTarget:
        url = self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=UPLOAD_URL_SECONDS,
        )
        return UploadTarget(url=url, headers={"Content-Type": content_type})

    def stat(self, key: str) -> ObjectInfo | None:
        from botocore.exceptions import ClientError

        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return ObjectInfo(
            size=head["ContentLength"], content_type=head.get("ContentType", "")
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def serve(self, key: str, content_type: str | None = None) -> Response:
        """Send the browser to a short-lived signed link for the file."""
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=300,
        )
        return RedirectResponse(url, status_code=302)


# ------------------------------------------------------------- chooser


@lru_cache(maxsize=1)
def get_storage() -> LocalStorage | S3Storage:
    """The storage backend chosen by STORAGE_BACKEND. Also a FastAPI dependency,
    so tests can swap it with app.dependency_overrides."""
    s = get_settings()
    if s.STORAGE_BACKEND == "local":
        return LocalStorage(s.LOCAL_STORAGE_DIR, s.SECRET_KEY)
    if s.STORAGE_BACKEND in ("s3", "r2"):
        return S3Storage(
            s.S3_ENDPOINT_URL, s.S3_ACCESS_KEY, s.S3_SECRET_KEY, s.S3_BUCKET, s.S3_REGION
        )
    raise RuntimeError(
        f"STORAGE_BACKEND must be 'local' or 's3', got {s.STORAGE_BACKEND!r}"
    )
