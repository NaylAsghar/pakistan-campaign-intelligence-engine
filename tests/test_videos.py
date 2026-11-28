"""Phase 5 tests: video upload, preview, edit, thumbnail, delete, ownership.

Nothing here talks to Cloudflare: files go to a throw-away LocalStorage
folder, exactly like the product tests.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from publisher import storage as storage_module
from publisher.db import SessionLocal
from publisher.main import app
from publisher.models import (
    Product,
    Schedule,
    User,
    Video,
    YouTubeAccount,
    utcnow,
)
from publisher.storage import (
    LocalStorage,
    S3Storage,
    get_storage,
    looks_like_video,
    new_thumbnail_key,
    new_video_key,
    rules_for_key,
    thumbnail_user_id_from_key,
    video_user_id_from_key,
)

PASSWORD = "correct horse"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 64


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
        c.email = sign_up(c)
        yield c


@pytest.fixture()
def other_client(storage):
    with make_client() as c:
        c.email = sign_up(c)
        yield c


def user_id_of(client):
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.email == client.email))


def upload_video(client, data=MP4, content_type="video/mp4", filename="Summer Lawn.mp4",
                 title="", product_id=None):
    """Run the whole presign -> PUT -> confirm flow. Returns the confirm response."""
    pre = client.post(
        "/videos/presign",
        json={"filename": filename, "content_type": content_type, "size": len(data)},
    )
    assert pre.status_code == 200, pre.text
    info = pre.json()
    put = client.put(info["upload_url"], content=data, headers=info["headers"])
    assert put.status_code == 200, put.text
    return client.post(
        "/videos/confirm",
        json={"key": info["key"], "filename": filename, "title": title,
              "product_id": product_id},
    )


def upload_thumbnail(client, video_id, data=PNG, content_type="image/png"):
    pre = client.post(
        f"/videos/{video_id}/thumbnail/presign",
        json={"filename": "t.png", "content_type": content_type, "size": len(data)},
    )
    assert pre.status_code == 200, pre.text
    info = pre.json()
    put = client.put(info["upload_url"], content=data, headers=info["headers"])
    assert put.status_code == 200, put.text
    return client.post(
        f"/videos/{video_id}/thumbnail/confirm", json={"key": info["key"]}
    ), info["key"]


def make_video(client) -> int:
    r = upload_video(client)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def create_product(client, name="Lawn suit"):
    r = client.post("/products", data={"name": name, "description": ""})
    assert r.status_code == 303
    return int(r.headers["location"].rsplit("/", 1)[1])


GOOD_EDIT = {
    "title": "New title", "description": "About it", "tags": "lawn, summer",
    "privacy_status": "unlisted", "product_id": "",
}


# ------------------------------------------------------------ storage unit


def test_video_and_thumbnail_keys_are_checked():
    key = new_video_key(7, "video/mp4")
    assert key.startswith("videos/7/") and key.endswith(".mp4")
    assert video_user_id_from_key(key) == 7
    assert thumbnail_user_id_from_key(key) is None  # a video key is not a thumbnail key

    thumb = new_thumbnail_key(7, "image/png")
    assert thumb.startswith("thumbnails/7/") and thumb.endswith(".png")
    assert thumbnail_user_id_from_key(thumb) == 7
    assert video_user_id_from_key(thumb) is None

    for bad in ["../x.mp4", "videos/7/../../x.mp4", "videos/x/abc.mp4",
                "videos/7/abc.mp4", "videos/7/" + "a" * 32 + ".exe"]:
        assert video_user_id_from_key(bad) is None


def test_rules_for_key():
    assert rules_for_key(new_video_key(1, "video/webm")).label == "video"
    assert rules_for_key("products/1/" + "a" * 32 + ".png").label == "image"
    thumb_rule = rules_for_key(new_thumbnail_key(1, "image/jpeg"))
    assert "image/webp" not in thumb_rule.content_types  # YouTube refuses WebP
    assert rules_for_key("anything/else.txt") is None


def test_looks_like_video():
    assert looks_like_video(MP4, "video/mp4")
    assert looks_like_video(MP4, "video/quicktime")
    assert looks_like_video(WEBM, "video/webm")
    assert not looks_like_video(b"just some text here", "video/mp4")
    assert not looks_like_video(PNG, "video/webm")
    assert not looks_like_video(MP4, "video/x-unknown")


def test_local_storage_new_methods(tmp_path):
    store = LocalStorage(tmp_path / "root", "k")
    key = new_video_key(1, "video/mp4")
    store.save(key, MP4)
    assert store.read_head(key, 8) == MP4[:8]
    assert store.read_head(new_video_key(1, "video/mp4")) == b""  # missing file

    copy = tmp_path / "copy.mp4"
    store.download_to_path(key, copy)
    assert copy.read_bytes() == MP4
    with pytest.raises(FileNotFoundError):
        store.download_to_path(new_video_key(1, "video/mp4"), tmp_path / "nope.mp4")

    temp = store.new_temp_path()
    temp.write_bytes(b"streamed")
    other = new_video_key(1, "video/webm")
    store.commit_temp(temp, other)
    assert store.stat(other).size == 8 and not temp.exists()
    assert store.stat(other).content_type == "video/webm"


def test_s3_storage_methods_with_fake_client():
    """S3Storage is exercised with a fake boto3 client, so no network is used."""

    class FakeBody:
        def read(self):
            return b"headbytes"

    class FakeClient:
        def __init__(self):
            self.calls = []

        def generate_presigned_url(self, op, Params, ExpiresIn):
            self.calls.append((op, ExpiresIn))
            return f"https://r2.example/{Params['Key']}?exp={ExpiresIn}"

        def get_object(self, **kwargs):
            self.calls.append(("get_object", kwargs["Range"]))
            return {"Body": FakeBody()}

        def download_file(self, bucket, key, destination):
            self.calls.append(("download_file", key, destination))

    store = S3Storage.__new__(S3Storage)  # skip __init__, which needs boto3
    store.bucket = "b"
    store.client = FakeClient()

    assert store.read_head("k", 16) == b"headbytes"
    assert ("get_object", "bytes=0-15") in store.client.calls

    response = store.serve("videos/1/x.mp4", expires=3600)
    assert response.status_code == 302 and "exp=3600" in response.headers["location"]
    assert store.serve("videos/1/x.mp4").headers["location"].endswith("exp=300")

    store.download_to_path("videos/1/x.mp4", "/tmp/out.mp4")
    assert ("download_file", "videos/1/x.mp4", "/tmp/out.mp4") in store.client.calls


# ------------------------------------------------------------ login rules


def test_pages_need_login(storage):
    with make_client() as anon:
        for path in ("/videos", "/videos/new", "/videos/1", "/videos/1/file",
                     "/videos/1/thumbnail"):
            r = anon.get(path)
            assert r.status_code == 303 and r.headers["location"] == "/login", path
        assert anon.post("/videos/1/edit", data=GOOD_EDIT).status_code == 303
        assert anon.post("/videos/1/delete").status_code == 303
        body = {"content_type": "video/mp4", "size": 5}
        assert anon.post("/videos/presign", json=body).status_code == 401
        assert anon.post("/videos/confirm", json={"key": "x"}).status_code == 401
        assert anon.post("/videos/1/thumbnail/presign",
                         json={"content_type": "image/png", "size": 5}).status_code == 401


# ------------------------------------------------------------ upload flow


def test_full_upload_flow_list_detail_and_file(client):
    assert "no videos yet" in client.get("/videos").text
    r = upload_video(client, filename="Summer Lawn.mp4")
    assert r.status_code == 200
    video_id = r.json()["id"]
    assert r.json()["url"] == f"/videos/{video_id}"

    with SessionLocal() as db:
        video = db.get(Video, video_id)
        assert video.title == "Summer Lawn"  # from the file name
        assert video.original_filename == "Summer Lawn.mp4"
        assert video.size_bytes == len(MP4)
        assert video.privacy_status == "private"
        assert video.storage_key.startswith(f"videos/{user_id_of(client)}/")

    assert "Summer Lawn" in client.get("/videos").text
    detail = client.get(f"/videos/{video_id}")
    assert detail.status_code == 200 and "<video" in detail.text

    shown = client.get(f"/videos/{video_id}/file")
    assert shown.status_code == 200 and shown.content == MP4
    assert shown.headers["content-type"] == "video/mp4"


def test_webm_upload_and_custom_title(client):
    r = upload_video(client, data=WEBM, content_type="video/webm",
                     filename="x.webm", title="  <b>My</b> promo  ")
    assert r.status_code == 200
    with SessionLocal() as db:
        # angle brackets are stripped because YouTube rejects them
        assert db.get(Video, r.json()["id"]).title == "bMy/b promo"


def test_upload_with_product(client):
    pid = create_product(client)
    r = upload_video(client, product_id=pid)
    assert r.status_code == 200
    with SessionLocal() as db:
        assert db.get(Video, r.json()["id"]).product_id == pid
    assert "Lawn suit" in client.get("/videos").text


def test_confirm_rejects_other_users_product(client, other_client):
    foreign_pid = create_product(other_client)
    r = upload_video(client, product_id=foreign_pid)
    assert r.status_code == 400 and "product" in r.json()["detail"].lower()


def test_presign_rejects_bad_requests(client):
    ok = {"filename": "a.mp4", "content_type": "video/mp4", "size": 100}
    assert client.post("/videos/presign", json=ok).status_code == 200
    for bad in (
        {**ok, "content_type": "video/x-msvideo"},
        {**ok, "content_type": "image/png"},
        {**ok, "content_type": "application/pdf"},
        {**ok, "size": 0},
        {**ok, "size": 501 * 1024 * 1024},
    ):
        assert client.post("/videos/presign", json=bad).status_code == 400, bad


def test_local_upload_rejects_bad_files(client):
    pre = client.post(
        "/videos/presign",
        json={"filename": "a.mp4", "content_type": "video/mp4", "size": 100},
    ).json()
    url = pre["upload_url"]

    # wrong declared type
    r = client.put(url, content=MP4, headers={"Content-Type": "video/webm"})
    assert r.status_code == 415
    # right type but the bytes are a text file renamed to .mp4
    r = client.put(url, content=b"just some text, not a video", headers={"Content-Type": "video/mp4"})
    assert r.status_code == 400 and "not a valid video" in r.json()["detail"]
    # forged token
    r = client.put("/storage/local-upload?token=garbage", content=MP4,
                   headers={"Content-Type": "video/mp4"})
    assert r.status_code == 400


def test_local_upload_enforces_size_while_streaming(client, storage, monkeypatch):
    monkeypatch.setattr(storage_module, "MAX_VIDEO_BYTES", 100)
    pre = client.post(
        "/videos/presign",
        json={"filename": "a.mp4", "content_type": "video/mp4", "size": 50},
    ).json()
    r = client.put(pre["upload_url"], content=MP4 + b"0" * 500, headers=pre["headers"])
    assert r.status_code == 413
    assert storage.stat(pre["key"]) is None  # nothing was stored
    assert list((storage.root / ".tmp").glob("*")) == []  # scratch file cleaned up


def test_local_upload_cannot_write_an_image_as_a_video_key(client):
    """A PNG sent to a video upload link is refused: the key decides the rules."""
    pre = client.post(
        "/videos/presign",
        json={"filename": "a.mp4", "content_type": "video/mp4", "size": 100},
    ).json()
    r = client.put(pre["upload_url"], content=PNG, headers={"Content-Type": "video/mp4"})
    assert r.status_code == 400


def test_confirm_rejects_missing_foreign_repeated_and_fake_videos(client, other_client, storage):
    pre = client.post(
        "/videos/presign",
        json={"filename": "a.mp4", "content_type": "video/mp4", "size": 100},
    ).json()
    # never uploaded
    r = client.post("/videos/confirm", json={"key": pre["key"]})
    assert r.status_code == 400 and "not uploaded" in r.json()["detail"]

    # a made-up key, and a key that belongs to another user
    assert client.post("/videos/confirm", json={"key": "../../etc/passwd"}).status_code == 400
    foreign = new_video_key(user_id_of(other_client), "video/mp4")
    assert client.post("/videos/confirm", json={"key": foreign}).status_code == 400
    # a thumbnail key cannot be confirmed as a video
    assert client.post("/videos/confirm",
                       json={"key": new_thumbnail_key(user_id_of(client), "image/png")}).status_code == 400

    # a file that bypassed the upload checks but is not a video is rejected and removed
    storage.save(pre["key"], b"this is not a video at all")
    r = client.post("/videos/confirm", json={"key": pre["key"]})
    assert r.status_code == 400 and "not a valid video" in r.json()["detail"]
    assert storage.stat(pre["key"]) is None

    # the same upload cannot be recorded twice
    again = client.post(
        "/videos/presign",
        json={"filename": "a.mp4", "content_type": "video/mp4", "size": 100},
    ).json()
    assert client.put(again["upload_url"], content=MP4, headers=again["headers"]).status_code == 200
    assert client.post("/videos/confirm", json={"key": again["key"]}).status_code == 200
    assert client.post("/videos/confirm", json={"key": again["key"]}).status_code == 400


def test_confirm_removes_oversized_file(client, storage, monkeypatch):
    pre = client.post(
        "/videos/presign",
        json={"filename": "a.mp4", "content_type": "video/mp4", "size": 100},
    ).json()
    storage.save(pre["key"], MP4)
    monkeypatch.setattr("publisher.videos.MAX_VIDEO_BYTES", 10)
    r = client.post("/videos/confirm", json={"key": pre["key"]})
    assert r.status_code == 400 and "too large" in r.json()["detail"]
    assert storage.stat(pre["key"]) is None


# --------------------------------------------------------------- ownership


def test_other_users_cannot_touch_my_video(client, other_client):
    vid = make_video(client)
    assert other_client.get(f"/videos/{vid}").status_code == 404
    assert other_client.get(f"/videos/{vid}/file").status_code == 404
    assert other_client.get(f"/videos/{vid}/thumbnail").status_code == 404
    assert other_client.post(f"/videos/{vid}/edit", data=GOOD_EDIT).status_code == 404
    assert other_client.post(f"/videos/{vid}/delete").status_code == 404
    assert other_client.post(f"/videos/{vid}/thumbnail/delete").status_code == 404
    r = other_client.post(
        f"/videos/{vid}/thumbnail/presign",
        json={"content_type": "image/png", "size": 5},
    )
    assert r.status_code == 404
    assert other_client.post(
        f"/videos/{vid}/thumbnail/confirm",
        json={"key": new_thumbnail_key(user_id_of(other_client), "image/png")},
    ).status_code == 404
    assert "Summer Lawn" not in other_client.get("/videos").text
    # still there for the owner
    assert client.get(f"/videos/{vid}").status_code == 200


# ----------------------------------------------------------------- editing


def test_edit_saves_details_and_cleans_tags(client):
    vid = make_video(client)
    pid = create_product(client)
    r = client.post(
        f"/videos/{vid}/edit",
        data={**GOOD_EDIT, "title": "  Eid lawn  ", "tags": " lawn, Summer ,lawn,, SUMMER, eid ",
              "product_id": str(pid)},
    )
    assert r.status_code == 303 and r.headers["location"] == f"/videos/{vid}"
    with SessionLocal() as db:
        video = db.get(Video, vid)
        assert video.title == "Eid lawn"
        assert video.tags == "lawn, Summer, eid"  # trimmed, no repeats
        assert video.privacy_status == "unlisted"
        assert video.description == "About it"
        assert video.product_id == pid
    page = client.get(f"/videos/{vid}")
    assert "Saved." in page.text and "Eid lawn" in page.text
    assert "Saved." not in client.get(f"/videos/{vid}").text  # message shows once


def test_edit_can_clear_the_product(client):
    pid = create_product(client)
    vid = upload_video(client, product_id=pid).json()["id"]
    assert client.post(f"/videos/{vid}/edit", data={**GOOD_EDIT, "product_id": ""}).status_code == 303
    with SessionLocal() as db:
        assert db.get(Video, vid).product_id is None


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"title": "   "}, "Title is required"),
        ({"title": "x" * 101}, "at most 100"),
        ({"title": "a < b"}, "cannot contain"),
        ({"description": "x" * 5001}, "at most 5000 bytes"),
        ({"description": "<script>"}, "cannot contain"),
        ({"privacy_status": "everyone"}, "public, unlisted or private"),
        ({"tags": "ok, " + "x" * 101}, "at most 100"),
        ({"tags": ", ".join(f"tag{i:03d}" for i in range(80))}, "500 characters"),
        ({"tags": "a<b"}, "cannot contain"),
        ({"product_id": "99999"}, "Choose a product"),
        ({"product_id": "abc"}, "Choose a product"),
    ],
)
def test_edit_validation(client, changes, expected):
    vid = make_video(client)
    r = client.post(f"/videos/{vid}/edit", data={**GOOD_EDIT, **changes})
    assert r.status_code == 400 and expected in r.text
    with SessionLocal() as db:  # nothing was saved
        assert db.get(Video, vid).title == "Summer Lawn"


def test_description_limit_counts_bytes_not_characters(client):
    vid = make_video(client)
    # 2600 Urdu letters are 2600 characters but 5200 bytes in UTF-8.
    r = client.post(f"/videos/{vid}/edit", data={**GOOD_EDIT, "description": "ا" * 2600})
    assert r.status_code == 400 and "bytes" in r.text


def test_edit_cannot_use_another_users_product(client, other_client):
    vid = make_video(client)
    foreign = create_product(other_client)
    r = client.post(f"/videos/{vid}/edit", data={**GOOD_EDIT, "product_id": str(foreign)})
    assert r.status_code == 400
    with SessionLocal() as db:
        assert db.get(Video, vid).product_id is None


def test_failed_edit_keeps_what_the_user_typed(client):
    vid = make_video(client)
    r = client.post(f"/videos/{vid}/edit", data={**GOOD_EDIT, "title": "", "description": "Keep me"})
    assert r.status_code == 400 and "Keep me" in r.text


# --------------------------------------------------------------- thumbnail


def test_thumbnail_upload_replace_and_delete(client, storage):
    vid = make_video(client)
    assert client.get(f"/videos/{vid}/thumbnail").status_code == 404

    r, first_key = upload_thumbnail(client, vid, PNG, "image/png")
    assert r.status_code == 200 and r.json()["url"] == f"/videos/{vid}/thumbnail"
    shown = client.get(f"/videos/{vid}/thumbnail")
    assert shown.status_code == 200 and shown.content == PNG
    assert "poster=" in client.get(f"/videos/{vid}").text

    # replacing removes the old file from storage
    r, second_key = upload_thumbnail(client, vid, JPEG, "image/jpeg")
    assert r.status_code == 200
    assert storage.stat(first_key) is None and storage.stat(second_key) is not None
    assert client.get(f"/videos/{vid}/thumbnail").content == JPEG

    r = client.post(f"/videos/{vid}/thumbnail/delete")
    assert r.status_code == 303
    assert storage.stat(second_key) is None
    assert client.get(f"/videos/{vid}/thumbnail").status_code == 404
    with SessionLocal() as db:
        assert db.get(Video, vid).thumbnail_key is None


def test_thumbnail_presign_rules(client):
    vid = make_video(client)
    url = f"/videos/{vid}/thumbnail/presign"
    ok = {"filename": "t.png", "content_type": "image/png", "size": 100}
    assert client.post(url, json=ok).status_code == 200
    assert client.post(url, json={**ok, "content_type": "image/webp"}).status_code == 400
    assert client.post(url, json={**ok, "content_type": "image/gif"}).status_code == 400
    assert client.post(url, json={**ok, "size": 0}).status_code == 400
    assert client.post(url, json={**ok, "size": 3 * 1024 * 1024}).status_code == 400


def test_thumbnail_upload_rejects_wrong_bytes_and_sizes(client, storage, monkeypatch):
    vid = make_video(client)
    pre = client.post(
        f"/videos/{vid}/thumbnail/presign",
        json={"filename": "t.png", "content_type": "image/png", "size": 100},
    ).json()
    r = client.put(pre["upload_url"], content=b"not a png", headers=pre["headers"])
    assert r.status_code == 400

    monkeypatch.setattr(storage_module, "MAX_THUMB_BYTES", 50)
    r = client.put(pre["upload_url"], content=PNG, headers=pre["headers"])
    assert r.status_code == 413


def test_thumbnail_confirm_rejects_bad_keys(client, storage):
    vid = make_video(client)
    other_vid = make_video(client)
    confirm = f"/videos/{vid}/thumbnail/confirm"
    me = user_id_of(client)

    assert client.post(confirm, json={"key": "../../x.png"}).status_code == 400
    assert client.post(confirm, json={"key": new_video_key(me, "video/mp4")}).status_code == 400
    missing = new_thumbnail_key(me, "image/png")
    r = client.post(confirm, json={"key": missing})
    assert r.status_code == 400 and "not uploaded" in r.json()["detail"]

    # fake image bytes that skipped the upload checks
    fake = new_thumbnail_key(me, "image/png")
    storage.save(fake, b"not really a png")
    r = client.post(confirm, json={"key": fake})
    assert r.status_code == 400 and storage.stat(fake) is None

    # one thumbnail file cannot be attached to two videos
    _, key = upload_thumbnail(client, vid)
    assert client.post(f"/videos/{other_vid}/thumbnail/confirm", json={"key": key}).status_code == 400


# ------------------------------------------------------------------ delete


def test_delete_removes_row_and_files(client, storage):
    vid = make_video(client)
    _, thumb_key = upload_thumbnail(client, vid)
    with SessionLocal() as db:
        video_key = db.get(Video, vid).storage_key
    assert storage.stat(video_key) and storage.stat(thumb_key)

    r = client.post(f"/videos/{vid}/delete")
    assert r.status_code == 303 and r.headers["location"] == "/videos"
    assert storage.stat(video_key) is None and storage.stat(thumb_key) is None
    with SessionLocal() as db:
        assert db.get(Video, vid) is None
    assert client.get(f"/videos/{vid}").status_code == 404
    assert "Video deleted." in client.get("/videos").text


def test_delete_is_blocked_when_a_schedule_uses_the_video(client, storage):
    vid = make_video(client)
    uid = user_id_of(client)
    with SessionLocal() as db:
        account = YouTubeAccount(
            user_id=uid, channel_id="UC_x", channel_title="x", refresh_token_encrypted="x"
        )
        db.add(account)
        db.flush()
        db.add(Schedule(user_id=uid, video_id=vid, youtube_account_id=account.id,
                        scheduled_at_utc=utcnow()))
        db.commit()
        key = db.get(Video, vid).storage_key

    r = client.post(f"/videos/{vid}/delete")
    assert r.status_code == 303 and r.headers["location"] == f"/videos/{vid}"
    assert "Delete those posts first" in client.get(f"/videos/{vid}").text
    assert storage.stat(key) is not None  # the file is still there
    with SessionLocal() as db:
        assert db.get(Video, vid) is not None


def test_deleting_a_product_keeps_its_videos(client):
    pid = create_product(client)
    vid = upload_video(client, product_id=pid).json()["id"]
    assert client.post(f"/products/{pid}/delete").status_code == 303
    with SessionLocal() as db:
        assert db.get(Product, pid) is None
        assert db.get(Video, vid).product_id is None
    assert client.get(f"/videos/{vid}").status_code == 200


def test_navigation_has_videos_link(client):
    assert 'href="/videos"' in client.get("/products").text
