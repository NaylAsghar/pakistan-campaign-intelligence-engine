"""The local-disk upload endpoint (used when STORAGE_BACKEND=local).

    PUT /storage/local-upload?token=...    receives one file

With R2 the browser sends files straight to Cloudflare and this route is
never used (it answers 404). It handles images, videos and thumbnails.

The body is written to a scratch file in small pieces, so a 500 MB video
never sits in memory. The size limit is enforced while streaming, and the
first bytes are checked before the file is moved to its final key.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from publisher.storage import HEAD_BYTES, get_storage, rules_for_key

router = APIRouter()


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):g} MB"


@router.put("/storage/local-upload")
async def local_upload(
    request: Request, token: str = "", storage=Depends(get_storage)
):
    """Receives the file when STORAGE_BACKEND=local (R2 gets it directly)."""
    if not hasattr(storage, "read_upload_token"):
        raise HTTPException(404)
    claims = storage.read_upload_token(token)
    if claims is None:
        raise HTTPException(400, "Upload link is invalid or has expired.")

    content_type = request.headers.get("content-type", "").split(";")[0].strip()
    if content_type != claims["ct"]:
        raise HTTPException(415, "File type does not match the upload link.")

    key = claims["key"]
    rule = rules_for_key(key)
    if rule is None or content_type not in rule.content_types:
        raise HTTPException(400, "Upload link is invalid or has expired.")

    temp_path = storage.new_temp_path()
    try:
        total = 0
        with temp_path.open("wb") as handle:
            async for chunk in request.stream():
                total += len(chunk)
                if total > rule.max_bytes:
                    raise HTTPException(
                        413, f"The file is too large (max {_mb(rule.max_bytes)})."
                    )
                handle.write(chunk)

        with temp_path.open("rb") as handle:
            head = handle.read(HEAD_BYTES)
        if not rule.check(head, content_type):
            raise HTTPException(400, f"The file is not a valid {rule.label}.")

        storage.commit_temp(temp_path, key)
    finally:
        temp_path.unlink(missing_ok=True)  # nothing left behind after an error
    return Response(status_code=200)
