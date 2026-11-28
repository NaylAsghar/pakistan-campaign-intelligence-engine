"""Where uploaded files live: local disk (development) or Cloudflare R2.

Both backends offer the same small interface, so the routes never care
which one is active (STORAGE_BACKEND in .env picks it):

    make_upload(key, content_type) -> where the browser should PUT the file
    stat(key)                      -> size and type of a stored file, or None
    read_head(key)                 -> the first bytes of a file (type check)
    delete(key)                    -> remove a file (no error if missing)
    serve(key, content_type)       -> an HTTP response that shows the file
    download_to_path(key, path)    -> copy a stored file to a local path
                                      (the Phase 7 publisher uses this)

Upload flow (the same for every kind of file):
    1. browser asks our server for an upload URL      (presign)
    2. browser PUTs the file to that URL              (local disk or R2)
    3. browser tells our server the upload is done    (confirm)

Three kinds of file exist. Their keys are made by the server only:

    product image   products/<product id>/<32 hex>.jpg|png|webp   max 10 MB
    video           videos/<user id>/<32 hex>.mp4|mov|webm        max 500 MB
    thumbnail       thumbnails/<user id>/<32 hex>.jpg|png         max 2 MB
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.responses import FileResponse, RedirectResponse, Response

from publisher.config import get_settings

# --------------------------------------------------------------- file kinds

# Image types we accept, and the file extension we store them under.
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
# Video types YouTube accepts that browsers can also play for the preview.
VIDEO_TYPES = {"video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm"}
# YouTube thumbnails must be JPEG or PNG (WebP is refused), so we say so here.
THUMB_TYPES = {"image/jpeg": "jpg", "image/png": "png"}

# Every extension we ever store, mapped back to its content type.
EXT_TO_TYPE = {
    ext: ctype
    for table in (IMAGE_TYPES, VIDEO_TYPES, THUMB_TYPES)
    for ctype, ext in table.items()
}

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB per product image
MAX_VIDEO_BYTES = 500 * 1024 * 1024  # 500 MB per video
MAX_THUMB_BYTES = 2 * 1024 * 1024  # 2 MB: YouTube's own thumbnail limit
UPLOAD_URL_SECONDS = 600  # an upload link works for 10 minutes
VIDEO_VIEW_SECONDS = 3600  # a video preview link works for 1 hour
HEAD_BYTES = 16  # how much of a file start looks_like_* needs

_IMAGE_KEY_RE = re.compile(r"^products/(\d+)/[0-9a-f]{32}\.(?:jpg|png|webp)$")
_VIDEO_KEY_RE = re.compile(r"^videos/(\d+)/[0-9a-f]{32}\.(?:mp4|mov|webm)$")
_THUMB_KEY_RE = re.compile(r"^thumbnails/(\d+)/[0-9a-f]{32}\.(?:jpg|png)$")


def new_image_key(product_id: int, content_type: str) -> str:
    """A fresh, unguessable storage key for one image of a product."""
    return f"products/{product_id}/{uuid.uuid4().hex}.{IMAGE_TYPES[content_type]}"


def new_video_key(user_id: int, content_type: str) -> str:
    """A fresh, unguessable storage key for one video of a user."""
    return f"videos/{user_id}/{uuid.uuid4().hex}.{VIDEO_TYPES[content_type]}"


def new_thumbnail_key(user_id: int, content_type: str) -> str:
    """A fresh, unguessable storage key for one video thumbnail."""
    return f"thumbnails/{user_id}/{uuid.uuid4().hex}.{THUMB_TYPES[content_type]}"


def _id_from_key(pattern: re.Pattern, key: str) -> int | None:
    match = pattern.match(key)
    return int(match.group(1)) if match else None


def product_id_from_key(key: str) -> int | None:
    """The product id inside a valid image key, or None if the key is not valid."""
    return _id_from_key(_IMAGE_KEY_RE, key)


def video_user_id_from_key(key: str) -> int | None:
    """The user id inside a valid video key, or None if the key is not valid."""
    return _id_from_key(_VIDEO_KEY_RE, key)


def thumbnail_user_id_from_key(key: str) -> int | None:
    """The user id inside a valid thumbnail key, or None if the key is not valid."""
    return _id_from_key(_THUMB_KEY_RE, key)


def content_type_for_key(key: str) -> str:
    """The content type that goes with a key's file extension ('' if unknown)."""
    return EXT_TO_TYPE.get(key.rsplit(".", 1)[-1], "")


# ------------------------------------------------------ checking file starts


def looks_like_image(data: bytes, content_type: str) -> bool:
    """Check the first bytes of a file, not just what the browser claims."""
    if content_type == "image/jpeg":
        return data[:3] == b"\xff\xd8\xff"
    if content_type == "image/png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if content_type == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def looks_like_video(data: bytes, content_type: str) -> bool:
    """MP4 and MOV files have 'ftyp' at byte 4; WebM files start with the
    EBML header 1A 45 DF A3. This catches a renamed text file or image."""
    if content_type in ("video/mp4", "video/quicktime"):
        return data[4:8] == b"ftyp"
    if content_type == "video/webm":
        return data[:4] == b"\x1a\x45\xdf\xa3"
    return False


@dataclass(frozen=True)
class KeyRule:
    """What is allowed for one kind of key."""

    label: str  # used in error messages: "image", "video"
    content_types: tuple[str, ...]
    max_bytes: int
    check: Callable[[bytes, str], bool]


def rules_for_key(key: str) -> KeyRule | None:
    """The upload rules for a storage key, or None if it is not one of ours.

    Built on every call (not at import time) so tests can change the limits.
    """
    if _IMAGE_KEY_RE.match(key):
        return KeyRule("image", tuple(IMAGE_TYPES), MAX_IMAGE_BYTES, looks_like_image)
    if _VIDEO_KEY_RE.match(key):
        return KeyRule("video", tuple(VIDEO_TYPES), MAX_VIDEO_BYTES, looks_like_video)
    if _THUMB_KEY_RE.match(key):
        return KeyRule("image", tuple(THUMB_TYPES), MAX_THUMB_BYTES, looks_like_image)
    return None


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

    def new_temp_path(self) -> Path:
        """A scratch file for a streamed upload, on the same disk as the
        final location so commit_temp() is a fast rename."""
        folder = self.root / ".tmp"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / uuid.uuid4().hex

    def commit_temp(self, temp_path: Path, key: str) -> None:
        """Move a finished upload into place under its key."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, path)

    def stat(self, key: str) -> ObjectInfo | None:
        path = self._path(key)
        if not path.is_file():
            return None
        content_type = EXT_TO_TYPE.get(path.suffix.lstrip("."), "")
        return ObjectInfo(size=path.stat().st_size, content_type=content_type)

    def read_head(self, key: str, size: int = HEAD_BYTES) -> bytes:
        path = self._path(key)
        if not path.is_file():
            return b""
        with path.open("rb") as handle:
            return handle.read(size)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def serve(
        self, key: str, content_type: str | None = None, expires: int = 300
    ) -> Response:
        """`expires` only matters for R2; local files never expire."""
        path = self._path(key)
        if not path.is_file():
            return Response(status_code=404)
        media_type = content_type or EXT_TO_TYPE.get(path.suffix.lstrip("."))
        return FileResponse(path, media_type=media_type)

    def download_to_path(self, key: str, destination: str | Path) -> None:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        shutil.copyfile(path, destination)


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

    def read_head(self, key: str, size: int = HEAD_BYTES) -> bytes:
        """Fetch only the first bytes (a ranged GET), never the whole file."""
        from botocore.exceptions import ClientError

        try:
            response = self.client.get_object(
                Bucket=self.bucket, Key=key, Range=f"bytes=0-{size - 1}"
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return b""
            raise
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def serve(
        self, key: str, content_type: str | None = None, expires: int = 300
    ) -> Response:
        """Send the browser to a short-lived signed link for the file.

        R2 answers Range requests itself, so video seeking works.
        """
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires,
        )
        return RedirectResponse(url, status_code=302)

    def download_to_path(self, key: str, destination: str | Path) -> None:
        self.client.download_file(self.bucket, key, str(destination))


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
