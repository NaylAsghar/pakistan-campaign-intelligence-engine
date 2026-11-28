"""Products and their images.

HTML pages (login required; anonymous visitors go to /login):
    GET  /products                         list my products
    GET  /products/new, POST /products     create
    GET  /products/{id}                    detail + image upload
    GET  /products/{id}/edit, POST ...     edit
    POST /products/{id}/delete             delete product and its images

JSON endpoints used by the upload script (401 when not logged in):
    POST /products/{id}/images/presign     get a one-time upload URL
    POST /products/{id}/images/confirm     record the finished upload

Files:
    (PUT /storage/local-upload lives in uploads.py)
    GET  /images/{id}                      show one image (owner only)
    POST /products/{id}/images/{image_id}/delete

A product that belongs to another user is always reported as 404, so
nobody can find out which ids exist.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from publisher.auth import get_current_user, require_user
from publisher.db import get_db
from publisher.models import Product, ProductImage, User
from publisher.storage import (
    IMAGE_TYPES,
    MAX_IMAGE_BYTES,
    get_storage,
    new_image_key,
    product_id_from_key,
)
from publisher.templating import templates

router = APIRouter()

MAX_IMAGES_PER_PRODUCT = 10
MAX_NAME_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 5000
ANGLE_LABELS = (
    "front", "back", "left", "right", "top", "bottom", "detail", "in use", "other",
)


# ---------------------------------------------------------------- helpers


def _page(request: Request, template: str, status_code: int = 200, **context):
    # The parameter is `template`, not `name`: a page may pass its own `name` value.
    return templates.TemplateResponse(
        request=request, name=template, context=context, status_code=status_code
    )


def _owned_product(db: Session, user: User, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None or product.user_id != user.id:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


def api_user(user: User | None = Depends(get_current_user)) -> User:
    """Like require_user, but answers 401 JSON instead of redirecting."""
    if user is None:
        raise HTTPException(status_code=401, detail="Please log in again.")
    return user


def _image_count(db: Session, product_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(ProductImage).where(
            ProductImage.product_id == product_id
        )
    )


def _validate_product_fields(name: str, description: str) -> str | None:
    if not name:
        return "Product name is required."
    if len(name) > MAX_NAME_LENGTH:
        return f"Name must be at most {MAX_NAME_LENGTH} characters."
    if len(description) > MAX_DESCRIPTION_LENGTH:
        return f"Description must be at most {MAX_DESCRIPTION_LENGTH} characters."
    return None


# ----------------------------------------------------------- product pages


@router.get("/products")
def product_list(
    request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    products = db.scalars(
        select(Product)
        .where(Product.user_id == user.id)
        .order_by(Product.created_at.desc(), Product.id.desc())
    ).all()
    return _page(request, "products_list.html", products=products)


@router.get("/products/new")
def product_new_form(request: Request, user: User = Depends(require_user)):
    return _page(
        request, "product_form.html",
        product=None, name="", description="", error=None,
    )


@router.post("/products")
def product_create(
    request: Request,
    name: str = Form(""),
    description: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    name, description = name.strip(), description.strip()
    error = _validate_product_fields(name, description)
    if error:
        return _page(
            request, "product_form.html", 400,
            product=None, name=name, description=description, error=error,
        )
    product = Product(user_id=user.id, name=name, description=description)
    db.add(product)
    db.commit()
    return RedirectResponse(f"/products/{product.id}", status_code=303)


@router.get("/products/{product_id}")
def product_detail(
    request: Request,
    product_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _owned_product(db, user, product_id)
    images = sorted(product.images, key=lambda i: i.id)
    return _page(
        request, "product_detail.html",
        product=product, images=images, angles=list(ANGLE_LABELS),
        max_images=MAX_IMAGES_PER_PRODUCT, max_mb=MAX_IMAGE_BYTES // (1024 * 1024),
    )


@router.get("/products/{product_id}/edit")
def product_edit_form(
    request: Request,
    product_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _owned_product(db, user, product_id)
    return _page(
        request, "product_form.html",
        product=product, name=product.name, description=product.description, error=None,
    )


@router.post("/products/{product_id}/edit")
def product_edit(
    request: Request,
    product_id: int,
    name: str = Form(""),
    description: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _owned_product(db, user, product_id)
    name, description = name.strip(), description.strip()
    error = _validate_product_fields(name, description)
    if error:
        return _page(
            request, "product_form.html", 400,
            product=product, name=name, description=description, error=error,
        )
    product.name, product.description = name, description
    db.commit()
    return RedirectResponse(f"/products/{product.id}", status_code=303)


@router.post("/products/{product_id}/delete")
def product_delete(
    product_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    product = _owned_product(db, user, product_id)
    for image in list(product.images):
        storage.delete(image.storage_key)
        db.delete(image)
    for video in product.videos:  # keep the videos, just unlink them
        video.product_id = None
    db.delete(product)
    db.commit()
    return RedirectResponse("/products", status_code=303)


# ------------------------------------------------------------ image upload


class PresignRequest(BaseModel):
    filename: str = ""
    content_type: str
    size: int
    angle_label: str


class ConfirmRequest(BaseModel):
    key: str
    angle_label: str


def _check_angle(angle_label: str) -> str:
    angle = angle_label.strip().lower()
    if angle not in ANGLE_LABELS:
        raise HTTPException(400, "Choose an angle from the list.")
    return angle


@router.post("/products/{product_id}/images/presign")
def image_presign(
    product_id: int,
    body: PresignRequest,
    user: User = Depends(api_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    product = _owned_product(db, user, product_id)
    _check_angle(body.angle_label)
    if body.content_type not in IMAGE_TYPES:
        raise HTTPException(400, "Only JPEG, PNG or WebP images are allowed.")
    if body.size <= 0:
        raise HTTPException(400, "The file is empty.")
    if body.size > MAX_IMAGE_BYTES:
        raise HTTPException(
            400, f"Image is too large (max {MAX_IMAGE_BYTES // (1024 * 1024)} MB)."
        )
    if _image_count(db, product.id) >= MAX_IMAGES_PER_PRODUCT:
        raise HTTPException(
            400, f"A product can have at most {MAX_IMAGES_PER_PRODUCT} images."
        )

    key = new_image_key(product.id, body.content_type)
    target = storage.make_upload(key, body.content_type)
    return {
        "key": key,
        "upload_url": target.url,
        "method": target.method,
        "headers": target.headers,
    }


@router.post("/products/{product_id}/images/confirm")
def image_confirm(
    product_id: int,
    body: ConfirmRequest,
    user: User = Depends(api_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    product = _owned_product(db, user, product_id)
    angle = _check_angle(body.angle_label)

    # The key must be one our server made, for THIS product.
    if product_id_from_key(body.key) != product.id:
        raise HTTPException(400, "Invalid upload key.")
    if db.scalar(select(ProductImage).where(ProductImage.storage_key == body.key)):
        raise HTTPException(400, "This upload was already recorded.")

    info = storage.stat(body.key)
    if info is None:
        raise HTTPException(400, "The file was not uploaded. Please try again.")
    if info.size > MAX_IMAGE_BYTES:
        storage.delete(body.key)
        raise HTTPException(400, "Image is too large.")
    if _image_count(db, product.id) >= MAX_IMAGES_PER_PRODUCT:
        storage.delete(body.key)
        raise HTTPException(
            400, f"A product can have at most {MAX_IMAGES_PER_PRODUCT} images."
        )

    image = ProductImage(product_id=product.id, storage_key=body.key, angle_label=angle)
    db.add(image)
    db.commit()
    return {"id": image.id, "url": f"/images/{image.id}", "angle_label": angle}


# ----------------------------------------------------- show / delete image


@router.get("/images/{image_id}")
def image_show(
    image_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    image = db.get(ProductImage, image_id)
    if image is None or image.product.user_id != user.id:
        raise HTTPException(404)
    return storage.serve(image.storage_key)


@router.post("/products/{product_id}/images/{image_id}/delete")
def image_delete(
    product_id: int,
    image_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    product = _owned_product(db, user, product_id)
    image = db.get(ProductImage, image_id)
    if image is None or image.product_id != product.id:
        raise HTTPException(404)
    storage.delete(image.storage_key)
    db.delete(image)
    db.commit()
    return RedirectResponse(f"/products/{product.id}", status_code=303)
