"""Database tables for users, products, videos, and publish jobs.

Every datetime column uses UTCDateTime, so values are stored in UTC and
come back timezone-aware. The UI (later phases) converts to Asia/Karachi.
A column typed without `| None` is required.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column, relationship

from publisher.db import Base

# A schedule moves draft -> scheduled -> processing -> published or failed.
STATUS_DRAFT = "draft"
STATUS_SCHEDULED = "scheduled"
STATUS_PROCESSING = "processing"
STATUS_PUBLISHED = "published"
STATUS_FAILED = "failed"

STATUSES = (
    STATUS_DRAFT,
    STATUS_SCHEDULED,
    STATUS_PROCESSING,
    STATUS_PUBLISHED,
    STATUS_FAILED,
)

# Values YouTube accepts for a video's privacy status.
PRIVACY_PUBLIC = "public"
PRIVACY_UNLISTED = "unlisted"
PRIVACY_PRIVATE = "private"

PRIVACY_OPTIONS = (PRIVACY_PUBLIC, PRIVACY_UNLISTED, PRIVACY_PRIVATE)


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """A datetime column that always speaks UTC.

    Postgres stores this as timestamptz. SQLite has no timezone type, so
    a value can come back without one; we attach UTC in that case.
    Naive datetimes written by the app are treated as UTC.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        # SQLite sometimes returns the raw text instead of a datetime.
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class User(Base):
    """A person who can log in, upload videos, and connect YouTube."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    # back_populates names the matching attribute on the other model.
    products: Mapped[list[Product]] = relationship(back_populates="user")
    youtube_accounts: Mapped[list[YouTubeAccount]] = relationship(back_populates="user")
    videos: Mapped[list[Video]] = relationship(back_populates="user")
    schedules: Mapped[list[Schedule]] = relationship(back_populates="user")


class Product(Base):
    """A product the user wants to promote. Images hang off this row."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="products")
    images: Mapped[list[ProductImage]] = relationship(back_populates="product")
    videos: Mapped[list[Video]] = relationship(back_populates="product")


class ProductImage(Base):
    """One photo of a product, stored by key (local disk or R2)."""

    __tablename__ = "product_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    storage_key: Mapped[str] = mapped_column(String(512))
    angle_label: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    product: Mapped[Product] = relationship(back_populates="images")


class YouTubeAccount(Base):
    """A connected channel. The refresh token is stored only after encryption."""

    __tablename__ = "youtube_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    channel_id: Mapped[str] = mapped_column(String(64))
    channel_title: Mapped[str] = mapped_column(String(255), default="")
    refresh_token_encrypted: Mapped[str] = mapped_column(Text)
    connected_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="youtube_accounts")
    schedules: Mapped[list[Schedule]] = relationship(back_populates="youtube_account")


class Video(Base):
    """An uploaded product video waiting to be scheduled or already used."""

    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    storage_key: Mapped[str] = mapped_column(String(512))
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    # BigInteger: a video file can be larger than a 32-bit integer.
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    title: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(Text, default="")  # comma-separated
    thumbnail_key: Mapped[str | None] = mapped_column(String(512))
    privacy_status: Mapped[str] = mapped_column(String(16), default=PRIVACY_PRIVATE)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="videos")
    product: Mapped[Product | None] = relationship(back_populates="videos")
    schedules: Mapped[list[Schedule]] = relationship(back_populates="video")


class Schedule(Base):
    """One planned publish. The worker claims rows with status 'scheduled'."""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    youtube_account_id: Mapped[int] = mapped_column(
        ForeignKey("youtube_accounts.id"), index=True
    )
    # Indexed because the worker asks "what is due now?"
    scheduled_at_utc: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    status: Mapped[str] = mapped_column(String(32), default=STATUS_SCHEDULED, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    youtube_video_id: Mapped[str | None] = mapped_column(String(32))
    youtube_url: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    variant_label: Mapped[str | None] = mapped_column(String(50))  # A/B tests
    # Phase 7 (publisher worker). The resumable-upload address YouTube gave us,
    # so a crashed or interrupted upload continues instead of starting over.
    # Treat it like a secret: never show it in a page or a log line.
    upload_session_url: Mapped[str | None] = mapped_column(Text)
    # When the worker claimed this row; refreshed after every uploaded chunk.
    # A 'processing' row whose claimed_at is old means the worker died.
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="schedules")
    video: Mapped[Video] = relationship(back_populates="schedules")
    youtube_account: Mapped[YouTubeAccount] = relationship(back_populates="schedules")
    stats: Mapped[list[VideoStat]] = relationship(back_populates="schedule")


class VideoStat(Base):
    """A snapshot of views, likes, and comments after a video is published."""

    __tablename__ = "video_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)

    schedule: Mapped[Schedule] = relationship(back_populates="stats")
