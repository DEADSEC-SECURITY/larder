from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from larder.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WebSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A signed-in browser.

    Sessions are deliberately long-lived (a year by default) so an installed
    phone app never asks for a password mid-shop. That is only safe because the
    row is server-side and revocable: the cookie carries nothing but this id,
    the refresh token never reaches the browser, and `revalidate_after` forces a
    periodic refresh against Authentik. When the password changes Authentik
    revokes the refresh token, the next refresh fails, and the row is deleted.
    """

    __tablename__ = "web_sessions"
    __table_args__ = (
        Index("ix_web_sessions_subject", "subject"),
        Index("ix_web_sessions_expires_at", "expires_at"),
    )

    subject: Mapped[str] = mapped_column(Text, nullable=False)
    # Denormalized OIDC claims (name/email/groups) rendered by the UI.
    claims: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Next time the refresh token must be exchanged to prove the account is
    # still good. NULL means the provider issued no refresh token.
    revalidate_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(Text)
