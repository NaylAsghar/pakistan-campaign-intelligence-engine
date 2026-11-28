"""Phase 3 tests: product CRUD, image upload flow, ownership, storage."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from publisher import products as products_module
from publisher.db import SessionLocal
from publisher.main import app
from publisher.models import Product, ProductImage
from publisher.storage import (
    LocalStorage,
    get_storage,
    new_image_key,
    product_id_from_key,
)

PASSWORD = "correct horse"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


@pytest.fixture()
def storage(tmp_path):
    """A throw-away storage folder, used instead of ./storage."""
    store = LocalStorage(tmp_path, "test-secret-key")
    app.dependency_overrides[get_storage] = lambda: store
    yield store
    app.dependency_overrides.pop(get_storage, None)


def make_client():
    return TestClient(app, follow_redirects=False)


def sign_up(client):
    email = f"user-{uuid.uuid4().hex[:10]}@example.com"
    r = client.post(
        "/register",
        data={"email": email, "password": PASSWORD, "confirm_password": PASSWORD},
    )
    assert r.status_code == 303
    return email


@pytest.fixture()
def client(storage):
    with make_client() as c:
        sign_up(c)
        yield c


@pytest.fixture()
def other_client(storage):
    with make_client() as c:
        sign_up(c)
        yield c


def create_product(client, name="Lawn suit", description="Summer collection"):
    r = client.post("/products", data={"name": name, "description": description})
    assert r.status_code == 303
    return int(r.headers["location"].rsplit("/", 1)[1])


def upload_image(client, product_id, data=PNG, content_type="image/png", angle="front"):
    """Run the whole presign -> PUT -> confirm flow. Returns the confirm response."""
    pre = client.post(
        f"/products/{product_id}/images/presign",
        json={
            "filename": "a.png",
            "content_type": content_type,
            "size": len(data),
            "angle_label": angle,
        },
    )
    assert pre.status_code == 200, pre.text
    info = pre.json()
    put = client.put(info["upload_url"], content=data, headers=info["headers"])
    assert put.status_code == 200, put.text
    return client.post(
        f"/products/{product_id}/images/confirm",
        json={"key": info["key"], "angle_label": angle},
    )


# ------------------------------------------------------------ storage unit


def test_keys_are_checked():
    key = new_image_key(7, "image/png")
    assert key.startswith("products/7/") and key.endswith(".png")
    assert product_id_from_key(key) == 7
    for bad in ["../x.png", "products/7/../../x.png", "products/x/abc.png", "products/7/abc.png"]:
        assert product_id_from_key(bad) is None


def test_local_storage_blocks_path_traversal(tmp_path):
    store = LocalStorage(tmp_path / "root", "k")
    with pytest.raises(ValueError):
        store.save("../outside.png", b"x")


def test_upload_token_roundtrip(tmp_path):
    store = LocalStorage(tmp_path, "k")
    target = store.make_upload("products/1/" + "a" * 32 + ".png", "image/png")
    token = target.url.split("token=")[1]
    assert store.read_upload_token(token)["ct"] == "image/png"
    assert store.read_upload_token(token + "x") is None


# ------------------------------------------------------------ login rules


def test_pages_need_login(storage):
    with make_client() as anon:
        for path in ("/products", "/products/new", "/products/1"):
            r = anon.get(path)
            assert r.status_code == 303 and r.headers["location"] == "/login"
        assert anon.post("/products", data={"name": "x"}).status_code == 303
        r = anon.post(
            "/products/1/images/presign",
            json={"content_type": "image/png", "size": 5, "angle_label": "front"},
        )
        assert r.status_code == 401


# ---------------------------------------------------------------- CRUD


def test_create_list_detail(client):
    assert "no products yet" in client.get("/products").text
    pid = create_product(client, "  Blue Kurta ", " Cotton ")
    assert "Blue Kurta" in client.get("/products").text
    detail = client.get(f"/products/{pid}")
    assert detail.status_code == 200 and "Cotton" in detail.text

    with SessionLocal() as db:
        product = db.get(Product, pid)
        assert product.name == "Blue Kurta" and product.description == "Cotton"


def test_create_validation(client):
    r = client.post("/products", data={"name": "   ", "description": ""})
    assert r.status_code == 400 and "name is required" in r.text
    r = client.post("/products", data={"name": "x" * 256, "description": ""})
    assert r.status_code == 400


def test_edit_product(client):
    pid = create_product(client)
    assert client.get(f"/products/{pid}/edit").status_code == 200
    r = client.post(f"/products/{pid}/edit", data={"name": "New name", "description": "d"})
    assert r.status_code == 303
    assert "New name" in client.get(f"/products/{pid}").text
    assert client.post(f"/products/{pid}/edit", data={"name": ""}).status_code == 400


def test_other_users_cannot_touch_my_product(client, other_client):
    pid = create_product(client)
    assert other_client.get(f"/products/{pid}").status_code == 404
    assert other_client.get(f"/products/{pid}/edit").status_code == 404
    assert other_client.post(f"/products/{pid}/edit", data={"name": "hack"}).status_code == 404
    assert other_client.post(f"/products/{pid}/delete").status_code == 404
    # Look for the product's link, not the bare digit: "1" is in the page's own HTML.
    assert f"/products/{pid}" not in other_client.get("/products").text
    r = other_client.post(
        f"/products/{pid}/images/presign",
        json={"content_type": "image/png", "size": 5, "angle_label": "front"},
    )
    assert r.status_code == 404
    assert client.get(f"/products/{pid}").status_code == 200


# ---------------------------------------------------------- image upload


def test_full_upload_flow_and_owner_only_download(client, other_client, storage):
    pid = create_product(client)
    r = upload_image(client, pid, angle="back")
    assert r.status_code == 200
    image = r.json()
    assert image["angle_label"] == "back"

    shown = client.get(image["url"])
    assert shown.status_code == 200 and shown.content == PNG
    assert shown.headers["content-type"] == "image/png"
    assert "back" in client.get(f"/products/{pid}").text

    assert other_client.get(image["url"]).status_code == 404
    with make_client() as anon:
        assert anon.get(image["url"]).status_code == 303


def test_jpeg_upload(client):
    pid = create_product(client)
    r = upload_image(client, pid, data=JPEG, content_type="image/jpeg", angle="top")
    assert r.status_code == 200


def test_presign_rejects_bad_requests(client):
    pid = create_product(client)
    url = f"/products/{pid}/images/presign"
    ok = {"content_type": "image/png", "size": 100, "angle_label": "front"}
    assert client.post(url, json=ok).status_code == 200
    assert client.post(url, json={**ok, "content_type": "image/gif"}).status_code == 400
    assert client.post(url, json={**ok, "content_type": "application/pdf"}).status_code == 400
    assert client.post(url, json={**ok, "size": 0}).status_code == 400
    assert client.post(url, json={**ok, "size": 11 * 1024 * 1024}).status_code == 400
    assert client.post(url, json={**ok, "angle_label": "sideways"}).status_code == 400


def test_local_upload_rejects_bad_files(client):
    pid = create_product(client)
    pre = client.post(
        f"/products/{pid}/images/presign",
        json={"content_type": "image/png", "size": 100, "angle_label": "front"},
    ).json()
    url = pre["upload_url"]

    # wrong declared type
    r = client.put(url, content=PNG, headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 415
    # right type but the bytes are not a PNG
    r = client.put(url, content=b"just text", headers={"Content-Type": "image/png"})
    assert r.status_code == 400
    # forged token
    r = client.put("/storage/local-upload?token=garbage", content=PNG,
                   headers={"Content-Type": "image/png"})
    assert r.status_code == 400


def test_confirm_rejects_missing_foreign_and_repeated_keys(client):
    pid = create_product(client)
    other_pid = create_product(client, "Second")
    confirm = f"/products/{pid}/images/confirm"

    pre = client.post(
        f"/products/{pid}/images/presign",
        json={"content_type": "image/png", "size": 100, "angle_label": "front"},
    ).json()
    # never uploaded
    r = client.post(confirm, json={"key": pre["key"], "angle_label": "front"})
    assert r.status_code == 400 and "not uploaded" in r.json()["detail"]

    # a key that belongs to a different product
    foreign = new_image_key(other_pid, "image/png")
    assert client.post(confirm, json={"key": foreign, "angle_label": "front"}).status_code == 400
    # a made-up key
    assert client.post(confirm, json={"key": "../../etc/passwd", "angle_label": "front"}).status_code == 400

    # the same upload cannot be recorded twice
    put = client.put(pre["upload_url"], content=PNG, headers=pre["headers"])
    assert put.status_code == 200
    assert client.post(confirm, json={"key": pre["key"], "angle_label": "front"}).status_code == 200
    assert client.post(confirm, json={"key": pre["key"], "angle_label": "front"}).status_code == 400


def test_image_limit(client, monkeypatch):
    monkeypatch.setattr(products_module, "MAX_IMAGES_PER_PRODUCT", 2)
    pid = create_product(client)
    assert upload_image(client, pid).status_code == 200
    assert upload_image(client, pid).status_code == 200
    r = client.post(
        f"/products/{pid}/images/presign",
        json={"content_type": "image/png", "size": 100, "angle_label": "front"},
    )
    assert r.status_code == 400 and "at most 2" in r.json()["detail"]


def test_delete_image_removes_row_and_file(client, storage):
    pid = create_product(client)
    image_id = upload_image(client, pid).json()["id"]
    with SessionLocal() as db:
        key = db.get(ProductImage, image_id).storage_key
    assert storage.stat(key) is not None

    r = client.post(f"/products/{pid}/images/{image_id}/delete")
    assert r.status_code == 303
    assert storage.stat(key) is None
    assert client.get(f"/images/{image_id}").status_code == 404


def test_delete_product_removes_images_and_files(client, storage):
    pid = create_product(client)
    image_id = upload_image(client, pid).json()["id"]
    with SessionLocal() as db:
        key = db.get(ProductImage, image_id).storage_key

    r = client.post(f"/products/{pid}/delete")
    assert r.status_code == 303 and r.headers["location"] == "/products"
    assert storage.stat(key) is None
    with SessionLocal() as db:
        assert db.get(Product, pid) is None
        assert db.scalar(select(ProductImage).where(ProductImage.id == image_id)) is None
    assert client.get(f"/products/{pid}").status_code == 404
