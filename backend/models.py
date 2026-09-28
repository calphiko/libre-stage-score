from datetime import datetime, time, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, declarative_base, mapped_column, relationship


Base = declarative_base()


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    user_memberships: Mapped[list["UserGroupMembership"]] = relationship(
        "UserGroupMembership",
        back_populates="group",
        cascade="all, delete-orphan",
    )
    document_accesses: Mapped[list["DocumentAccess"]] = relationship(
        "DocumentAccess",
        back_populates="group",
        cascade="all, delete-orphan",
    )

    @property
    def users(self) -> list["User"]:
        return [membership.user for membership in self.user_memberships if membership.user is not None]


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    user_pw: Mapped[str] = mapped_column(String(512), nullable=False)
    user_group: Mapped[str] = mapped_column(String(128), nullable=False, default="user", server_default="user")
    email: Mapped[str] = mapped_column(String(512), nullable=False)
    clear_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    musician: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_singer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mm_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")

    group_memberships: Mapped[list["UserGroupMembership"]] = relationship(
        "UserGroupMembership",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    @property
    def groups(self) -> list["Group"]:
        return [membership.group for membership in self.group_memberships if membership.group is not None]

    user_instruments: Mapped[list["UserInstrument"]] = relationship(
        "UserInstrument",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    document_accesses: Mapped[list["DocumentAccess"]] = relationship(
        "DocumentAccess",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserGroupMembership(Base):
    __tablename__ = "user_groups"
    __table_args__ = (UniqueConstraint("user_id", "group_id", name="uq_user_group"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="group_memberships")
    group: Mapped["Group"] = relationship("Group", back_populates="user_memberships")


class DocumentAccess(Base):
    __tablename__ = "document_access"
    __table_args__ = (
        UniqueConstraint("document_type", "document_id", "user_id", "group_id", name="uq_document_access"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    document_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    group_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=True)

    user: Mapped["User | None"] = relationship("User", back_populates="document_accesses")
    group: Mapped["Group | None"] = relationship("Group", back_populates="document_accesses")


class UsedPasswordResetToken(Base):
    __tablename__ = "used_password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    used_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped["User"] = relationship("User", backref="refresh_tokens")


class TokenBlacklist(Base):
    __tablename__ = "token_blacklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    blacklisted_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    instrument_name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    instrument_tuning: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    user_instruments: Mapped[list["UserInstrument"]] = relationship(
        "UserInstrument",
        back_populates="instrument",
        cascade="all, delete-orphan",
    )


class SongCollection(Base):
    __tablename__ = "song_collections"

    song_id: Mapped[int] = mapped_column(Integer, ForeignKey("songs.id", ondelete="CASCADE"), primary_key=True)
    collection_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True
    )


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    songs: Mapped[list["Song"]] = relationship(
        "Song",
        secondary="song_collections",
        back_populates="collections",
    )


class Song(Base):
    __tablename__ = "songs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(512), unique=False, index=True, nullable=False)
    tune: Mapped[str | None] = mapped_column(String(32), nullable=True)
    composer: Mapped[str | None] = mapped_column(String(1024), nullable=True, index=True)
    arrangement: Mapped[str | None] = mapped_column(String(1024), nullable=True, index=True)
    length: Mapped[time | None] = mapped_column(Time, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    collections: Mapped[list["Collection"]] = relationship(
        "Collection",
        secondary="song_collections",
        back_populates="songs",
    )


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    song_id: Mapped[int] = mapped_column(Integer, ForeignKey("songs.id"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(Integer, ForeignKey("instruments.id"), nullable=False)
    storage_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class UserInstrument(Base):
    __tablename__ = "user_instruments"
    __table_args__ = (UniqueConstraint("user_id", "instrument_id", name="uq_user_instrument"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(Integer, ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="user_instruments")
    instrument: Mapped["Instrument"] = relationship("Instrument", back_populates="user_instruments")