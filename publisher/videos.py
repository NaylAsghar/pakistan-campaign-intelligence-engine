"""Videos: upload, preview, edit details, thumbnail, delete.

HTML pages (login required; anonymous visitors go to /login):
    GET  /videos                          list my videos
    GET  /videos/new                      upload page
    GET  /videos/{id}                     preview + edit form + thumbnail
    POST /videos/{id}/edit                save title, description, tags, privacy
    POST /videos/{id}/delete              delete the video and its files

JSON endpoints used by the upload scripts (401 when not logged in):
    POST /videos/presign                  get a one-time upload URL
    POST /videos/confirm                  record the finished upload
    POST /videos/{id}/thumbnail/presign   same two steps for a thumbnail
    POST /videos/{id}/thumbnail/confirm

Files (owner only):
    GET  /videos/{id}/file                the video (R2: signed link, 1 hour)
    GET  /videos/{id}/thumbnail           the thumbnail
    POST /videos/{id}/thumbnail/delete

The browser sends the big file straight to storage (local disk or R2), never
through a form post. A video that belongs to another user is always reported
as 404, so nobody can find out which ids exist.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from publisher.auth import require_user
from publisher.db import get_db
from publisher.models import PRIVACY_OPTIONS, PRIVACY_PRIVATE, Product, User, Video
from publisher.products import api_user
from publisher.storage import (
    MAX_THUMB_BYTES,
    MAX_VIDEO_BYTES,
    THUMB_TYPES,
    VIDEO_TYPES,
    VIDEO_VIEW_SECONDS,
    content_type_for_key,
    get_storage,
    looks_like_image,
    looks_like_video,
    new_thumbnail_key,
    new_video_key,
    thumbnail_user_id_from_key,
    video_user_id_from_key,
)
from publisher.templating import templates

router = APIRouter()

# YouTube's own limits, checked here so a bad value fails now and not at 3 a.m.
MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_BYTES = 5000
MAX_TAGS_LENGTH = 500
MAX_TAG_LENGTH = 100
FORBIDDEN_CHARS = ("<", ">")  # the YouTube API rejects these in titles/descriptions

_FLASH = "video_message"


# ---------------------------------------------------------------- helpers


def _page(request: Request, name: str, status_code: int = 200, **context):
    return templates.TemplateResponse(
        request=request, name=name, context=context, status_code=status_code
    )


def _flash(request: Request, kind: str, text: str) -> None:
    """One message shown on the next video page view."""
    request.session[_FLASH] = {"kind": kind, "text": text}


def _owned_video(db: Session, user: User, video_id: int) -> Video:
    video = db.get(Video, video_id)
    if video is None or video.user_id != user.id:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


def _my_products(db: Session, user: User) -> list[Product]:
    return list(
        db.scalars(
            select(Product).where(Product.user_id == user.id).order_by(Product.name)
        ).all()
    )


def _find_product(db: Session, user: User, raw_id) -> Product | None:
    """The user's product for a form value, or None for 'no product'.
    Raises ValueError when the value names a product that is not theirs."""
    if raw_id in (None, "", "0"):
        return None
    try:
        product = db.get(Product, int(raw_id))
    except (TypeError, ValueError):
        raise ValueError("Choose a product from the list.") from None
    if product is None or product.user_id != user.id:
        raise ValueError("Choose a product from the list.")
    return product


def _clean_title(raw: str) -> str:
    """Make a title safe for YouTube: no angle brackets, at most 100 characters."""
    for char in FORBIDDEN_CHARS:
        raw = raw.replace(char, "")
    return raw.strip()[:MAX_TITLE_LENGTH]


def normalize_tags(raw: str) -> tuple[str, str | None]:
    """('a, b, c', None) for valid input, or ('', error message).

    Splits on commas, trims, drops empties and repeats (ignoring case).
    """
    seen, tags = set(), []
    for piece in raw.replace("\n", ",").split(","):
        tag = piece.strip()
        if not tag or tag.lower() in seen:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            return "", f"Each tag must be at most {MAX_TAG_LENGTH} characters."
        if any(char in tag for char in FORBIDDEN_CHARS):
            return "", "Tags cannot contain < or >."
        seen.add(tag.lower())
        tags.append(tag)
    joined = ", ".join(tags)
    if len(joined) > MAX_TAGS_LENGTH:
        return "", f"Tags are too long: {MAX_TAGS_LENGTH} characters in total at most."
    return joined, None


def validate_details(title: str, description: str, privacy: str) -> str | None:
    """The first problem with the edit form's text fields, or None."""
    if not title:
        return "Title is required."
    if len(title) > MAX_TITLE_LENGTH:
        return f"Title must be at most {MAX_TITLE_LENGTH} characters."
    if any(char in title for char in FORBIDDEN_CHARS):
        return "Title cannot contain < or >."
    if len(description.encode("utf-8")) > MAX_DESCRIPTION_BYTES:
        return f"Description must be at most {MAX_DESCRIPTION_BYTES} bytes."
    if any(char in description for char in FORBIDDEN_CHARS):
        return "Description cannot contain < or >."
    if privacy not in PRIVACY_OPTIONS:
        return "Choose public, unlisted or private."
    return None


def _detail_page(
    request: Request,
    db: Session,
    user: User,
    video: Video,
    status_code: int = 200,
    form: dict | None = None,
    error: str | None = None,
):
    """The video page. `form` holds what the user typed after a failed save."""
    return _page(
        request, "video_detail.html", status_code,
        video=video,
        products=_my_products(db, user),
        privacy_options=list(PRIVACY_OPTIONS),
        form=form or {
            "title": video.title,
            "description": video.description,
            "tags": video.tags,
            "privacy": video.privacy_status,
            "product_id": video.product_id or "",
        },
        error=error,
        message=request.session.pop(_FLASH, None),
        max_title=MAX_TITLE_LENGTH,
        max_description=MAX_DESCRIPTION_BYTES,
        max_thumb_mb=MAX_THUMB_BYTES // (1024 * 1024),
        thumb_types=list(THUMB_TYPES),
    )


class PresignRequest(BaseModel):
    filename: str = ""
    content_type: str
    size: int


class ConfirmVideoRequest(BaseModel):
    key: str
    filename: str = ""
    title: str = ""
    product_id: int | None = None


class ConfirmThumbnailRequest(BaseModel):
    key: str


def _check_presign(body: PresignRequest, types: dict, max_bytes: int, label: str) -> None:
    if body.content_type not in types:
        raise HTTPException(400, f"Unsupported {label} type. Allowed: {_allowed(types)}.")
    if body.size <= 0:
        raise HTTPException(400, "The file is empty.")
    if body.size > max_bytes:
        raise HTTPException(
            400, f"The {label} is too large (max {max_bytes // (1024 * 1024)} MB)."
        )


def _allowed(types: dict) -> str:
    return ", ".join(sorted(ext.upper() for ext in types.values()))


def _verify_uploaded(storage, key: str, max_bytes: int, check, label: str) -> int:
    """Make sure the file really arrived, is small enough and has the right
    first bytes. Deletes it and raises 400 if not. Returns its size in bytes."""
    info = storage.stat(key)
    if info is None:
        raise HTTPException(400, "The file was not uploaded. Please try again.")
    if info.size > max_bytes:
        storage.delete(key)
        raise HTTPException(400, f"The {label} is too large.")
    if not check(storage.read_head(key), content_type_for_key(key)):
        storage.delete(key)
        raise HTTPException(400, f"The file is not a valid {label}.")
    return info.size


# ----------------------------------------------------------------- pages


@router.get("/videos")
def video_list(
    request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    videos = db.scalars(
        select(Video)
        .where(Video.user_id == user.id)
        .order_by(Video.created_at.desc(), Video.id.desc())
    ).all()
    return _page(
        request, "videos_list.html",
        videos=videos, message=request.session.pop(_FLASH, None),
    )


@router.get("/videos/new")  # must stay above /videos/{video_id}
def video_new(
    request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    return _page(
        request, "video_upload.html",
        products=_my_products(db, user),
        video_types=list(VIDEO_TYPES),
        allowed=_allowed(VIDEO_TYPES),
        max_mb=MAX_VIDEO_BYTES // (1024 * 1024),
        max_title=MAX_TITLE_LENGTH,
    )


@router.get("/videos/{video_id}")
def video_detail(
    request: Request,
    video_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    video = _owned_video(db, user, video_id)
    return _detail_page(request, db, user, video)


# -------------------------------------------------------- upload a video


@router.post("/videos/presign")
def video_presign(
    body: PresignRequest,
    user: User = Depends(api_user),
    storage=Depends(get_storage),
):
    _check_presign(body, VIDEO_TYPES, MAX_VIDEO_BYTES, "video")
    key = new_video_key(user.id, body.content_type)
    target = storage.make_upload(key, body.content_type)
    return {
        "key": key,
        "upload_url": target.url,
        "method": target.method,
        "headers": target.headers,
    }


@router.post("/videos/confirm")
def video_confirm(
    body: ConfirmVideoRequest,
    user: User = Depends(api_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    # The key must be one our server made, for THIS user.
    if video_user_id_from_key(body.key) != user.id:
        raise HTTPException(400, "Invalid upload key.")
    if db.scalar(select(Video).where(Video.storage_key == body.key)):
        raise HTTPException(400, "This upload was already recorded.")
    try:
        product = _find_product(db, user, body.product_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    size = _verify_uploaded(storage, body.key, MAX_VIDEO_BYTES, looks_like_video, "video")

    filename = body.filename.strip()[:255]
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    title = _clean_title(body.title) or _clean_title(stem) or "Untitled video"

    video = Video(
        user_id=user.id,
        product_id=product.id if product else None,
        storage_key=body.key,
        original_filename=filename,
        size_bytes=size,
        title=title,
        privacy_status=PRIVACY_PRIVATE,  # safest default; the user opts in to public
    )
    db.add(video)
    db.commit()
    return {"id": video.id, "url": f"/videos/{video.id}"}


# ------------------------------------------------- watch / edit / delete


@router.get("/videos/{video_id}/file")
def video_file(
    video_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    video = _owned_video(db, user, video_id)
    return storage.serve(video.storage_key, expires=VIDEO_VIEW_SECONDS)


@router.post("/videos/{video_id}/edit")
def video_edit(
    request: Request,
    video_id: int,
    title: str = Form(""),
    description: str = Form(""),
    tags: str = Form(""),
    privacy_status: str = Form(PRIVACY_PRIVATE),
    product_id: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    video = _owned_video(db, user, video_id)
    title, description = title.strip(), description.strip()
    form = {
        "title": title, "description": description, "tags": tags,
        "privacy": privacy_status, "product_id": product_id,
    }

    error = validate_details(title, description, privacy_status)
    clean_tags = ""
    product = None
    if error is None:
        clean_tags, error = normalize_tags(tags)
    if error is None:
        try:
            product = _find_product(db, user, product_id)
        except ValueError as exc:
            error = str(exc)
    if error:
        return _detail_page(request, db, user, video, 400, form=form, error=error)

    video.title = title
    video.description = description
    video.tags = clean_tags
    video.privacy_status = privacy_status
    video.product_id = product.id if product else None
    db.commit()
    _flash(request, "ok", "Saved.")
    return RedirectResponse(f"/videos/{video.id}", status_code=303)


@router.post("/videos/{video_id}/delete")
def video_delete(
    request: Request,
    video_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    video = _owned_video(db, user, video_id)
    if video.schedules:
        _flash(
            request, "error",
            "This video has scheduled or past posts. Delete those posts first.",
        )
        return RedirectResponse(f"/videos/{video.id}", status_code=303)

    storage.delete(video.storage_key)
    if video.thumbnail_key:
        storage.delete(video.thumbnail_key)
    db.delete(video)
    db.commit()
    _flash(request, "ok", "Video deleted.")
    return RedirectResponse("/videos", status_code=303)


# ------------------------------------------------------------- thumbnail


@router.get("/videos/{video_id}/thumbnail")
def thumbnail_show(
    video_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    video = _owned_video(db, user, video_id)
    if not video.thumbnail_key:
        raise HTTPException(404, "This video has no thumbnail.")
    return storage.serve(video.thumbnail_key)


@router.post("/videos/{video_id}/thumbnail/presign")
def thumbnail_presign(
    video_id: int,
    body: PresignRequest,
    user: User = Depends(api_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    _owned_video(db, user, video_id)
    _check_presign(body, THUMB_TYPES, MAX_THUMB_BYTES, "thumbnail")
    key = new_thumbnail_key(user.id, body.content_type)
    target = storage.make_upload(key, body.content_type)
    return {
        "key": key,
        "upload_url": target.url,
        "method": target.method,
        "headers": target.headers,
    }


@router.post("/videos/{video_id}/thumbnail/confirm")
def thumbnail_confirm(
    video_id: int,
    body: ConfirmThumbnailRequest,
    user: User = Depends(api_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    video = _owned_video(db, user, video_id)
    if thumbnail_user_id_from_key(body.key) != user.id:
        raise HTTPException(400, "Invalid upload key.")
    if db.scalar(select(Video).where(Video.thumbnail_key == body.key)):
        raise HTTPException(400, "This upload was already recorded.")

    _verify_uploaded(storage, body.key, MAX_THUMB_BYTES, looks_like_image, "image")

    old_key = video.thumbnail_key
    video.thumbnail_key = body.key
    db.commit()
    if old_key:  # replacing: remove the previous file only after the new one is saved
        storage.delete(old_key)
    return {"url": f"/videos/{video.id}/thumbnail"}


@router.post("/videos/{video_id}/thumbnail/delete")
def thumbnail_delete(
    request: Request,
    video_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    video = _owned_video(db, user, video_id)
    if video.thumbnail_key:
        key = video.thumbnail_key
        video.thumbnail_key = None
        db.commit()
        storage.delete(key)
    _flash(request, "ok", "Thumbnail removed.")
    return RedirectResponse(f"/videos/{video.id}", status_code=303)
