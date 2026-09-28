from datetime import datetime, time, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, declarative_base, mapped_column, relationship


Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    user_pw: Mapped[str] = mapped_column(String(512), nullable=False)
    user_group: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(512), nullable=False)
    clear_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")

    user_instruments: Mapped[list["UserInstrument"]] = relationship(
        "UserInstrument",
        back_populates="user",
        cascade="all, delete-orphan",
    )


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