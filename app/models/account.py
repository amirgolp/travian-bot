from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from sqlalchemy import Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.village import Village


class AccountStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    BANNED = "banned"
    ERROR = "error"


class Account(Base, TimestampMixin):
    """One Travian login on a specific gameworld."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    server_url: Mapped[str] = mapped_column(String(255))  # e.g. https://ts1.x1.international.travian.com
    server_code: Mapped[str] = mapped_column(String(64), index=True)  # derived, e.g. "international-ts1-x1"
    username: Mapped[str] = mapped_column(String(128))
    password_encrypted: Mapped[str] = mapped_column(Text)
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, name="account_status"),
        default=AccountStatus.ACTIVE,
        nullable=False,
    )
    # Per-account humanization settings (override defaults)
    active_hours: Mapped[str | None] = mapped_column(String(128), default=None)
    user_agent: Mapped[str | None] = mapped_column(Text, default=None)
    viewport_w: Mapped[int | None] = mapped_column(default=None)
    viewport_h: Mapped[int | None] = mapped_column(default=None)
    timezone: Mapped[str | None] = mapped_column(String(64), default=None)
    locale: Mapped[str | None] = mapped_column(String(16), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    # JSON-encoded list of controller names that are disabled for this account
    # (e.g. ["map_scan", "world_sql"]). Absent / null / [] = all enabled.
    disabled_controllers: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")

    # If true, the bot will click "Watch video" to grab the 25% speedup before
    # every upgrade, and watch any adventure-bonus videos before dispatching
    # adventures. True by default (matches an engaged human player). Disable
    # per-account via the API if the server doesn't offer the video bonus or
    # the ads are failing to load.
    watch_video_bonuses: Mapped[bool] = mapped_column(
        default=True, server_default="true"
    )

    villages: Mapped[list["Village"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
