"""SQLAlchemy models for the torrent parser database."""

from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Boolean,
    DateTime, Enum as SAEnum, ForeignKey, LargeBinary
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from pathlib import Path
import enum

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "parson.db"

Base = declarative_base()


class AccountStatus(enum.Enum):
    FRESH = "fresh"           # ready to use
    IN_USE = "in_use"         # currently used by a worker
    EXHAUSTED = "exhausted"   # download limit reached
    BANNED = "banned"


class TorrentStatus(enum.Enum):
    FOUND = "found"           # seen in listing, not yet opened
    PARSED = "parsed"         # data extracted, awaiting moderation
    APPROVED = "approved"     # approved for download by moderator
    REJECTED = "rejected"     # rejected
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    SKIPPED = "skipped"       # format didn't match filter
    ERROR = "error"


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    status = Column(SAEnum(AccountStatus), default=AccountStatus.FRESH)
    created_at = Column(DateTime, default=datetime.utcnow)

    tracker_account = relationship("TrackerAccount", back_populates="email_account", uselist=False)


class TrackerAccount(Base):
    __tablename__ = "tracker_accounts"

    id = Column(Integer, primary_key=True)
    username = Column(String(255), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    email_id = Column(Integer, ForeignKey("email_accounts.id"), nullable=False)
    status = Column(SAEnum(AccountStatus), default=AccountStatus.FRESH)
    downloads_count = Column(Integer, default=0)
    max_downloads = Column(Integer, default=3)
    created_at = Column(DateTime, default=datetime.utcnow)

    email_account = relationship("EmailAccount", back_populates="tracker_account")


class PageProgress(Base):
    """Tracks which category pages have been processed."""
    __tablename__ = "page_progress"

    id = Column(Integer, primary_key=True)
    category_id = Column(String(50), nullable=False)
    page_number = Column(Integer, nullable=False)
    worker_id = Column(String(50), nullable=True)
    is_completed = Column(Boolean, default=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)


class BlockedDomain(Base):
    """Email domains blacklisted by the tracker registration form."""
    __tablename__ = "blocked_domains"

    id = Column(Integer, primary_key=True)
    domain = Column(String(255), unique=True, nullable=False)
    reason = Column(String(500), nullable=True)
    blocked_at = Column(DateTime, default=datetime.utcnow)


class Torrent(Base):
    __tablename__ = "torrents"

    id = Column(Integer, primary_key=True)
    topic_id = Column(String(50), unique=True, nullable=False)
    title_raw = Column(String(1000), nullable=False)   # original title from listing
    category_id = Column(String(50), nullable=False)
    page_number = Column(Integer, nullable=False)
    status = Column(SAEnum(TorrentStatus), default=TorrentStatus.FOUND)

    # Parsed from title: [Studio]Name[Tags/Genres/Formats][Devices]
    studio = Column(String(500), nullable=True)
    film_name = Column(String(500), nullable=True)
    actors = Column(Text, nullable=True)           # JSON — actor names parsed from title
    tags = Column(Text, nullable=True)             # JSON — genres/tags (without formats)
    formats = Column(Text, nullable=True)          # JSON — normalized video formats
    devices = Column(Text, nullable=True)          # JSON — devices (VR, Oculus, etc.)

    # Extracted from topic page body
    year = Column(String(10), nullable=True)
    description = Column(Text, nullable=True)
    duration = Column(String(50), nullable=True)
    file_size = Column(String(50), nullable=True)

    # Media
    cover_url = Column(String(1000), nullable=True)
    cover_path = Column(String(1000), nullable=True)
    cover_data = Column(LargeBinary, nullable=True)  # original image bytes (JPEG/PNG)

    # Torrent info (saved, not actually downloaded by parser workers)
    download_url = Column(String(1000), nullable=True)
    torrent_file = Column(LargeBinary, nullable=True)  # .torrent file bytes
    seeds = Column(Integer, nullable=True)
    peers = Column(Integer, nullable=True)

    discovered_at = Column(DateTime, default=datetime.utcnow)
    downloaded_at = Column(DateTime, nullable=True)   # for download workers later


def init_db(db_path: str = ""):
    """Initialize database, create tables. Auto-migrates torrents table if schema is outdated."""
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", echo=False)

    # Check if torrents table exists but has old schema (missing title_raw column).
    # If so, drop and recreate it so the new columns are available.
    with engine.connect() as conn:
        try:
            cols = [row[1] for row in conn.execute(
                __import__("sqlalchemy").text("PRAGMA table_info(torrents)")
            )]
            if cols and ("title_raw" not in cols or "actors" not in cols or "cover_data" not in cols or "torrent_file" not in cols):
                conn.execute(__import__("sqlalchemy").text("DROP TABLE torrents"))
                conn.commit()
        except Exception:
            pass

    Base.metadata.create_all(engine)
    return engine


def get_session_factory(engine=None):
    """Create a session factory bound to the engine."""
    if engine is None:
        engine = init_db()
    return sessionmaker(bind=engine)


def get_session(engine=None):
    """Create a new DB session. Caller must close it."""
    return get_session_factory(engine)()
