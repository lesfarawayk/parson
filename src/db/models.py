"""SQLAlchemy models for the torrent parser database."""

from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Boolean,
    DateTime, Enum as SAEnum, ForeignKey
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
    FOUND = "found"           # seen in listing, not yet processed
    TAGGED = "tagged"         # tags extracted, not downloading
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    SKIPPED = "skipped"
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
    title = Column(String(1000), nullable=False)
    category_id = Column(String(50), nullable=False)
    page_number = Column(Integer, nullable=False)
    status = Column(SAEnum(TorrentStatus), default=TorrentStatus.FOUND)

    description = Column(Text, nullable=True)
    cover_url = Column(String(1000), nullable=True)
    cover_path = Column(String(1000), nullable=True)
    file_path = Column(String(1000), nullable=True)

    download_tags = Column(Text, nullable=True)   # JSON list of matched download tags
    record_tags = Column(Text, nullable=True)      # JSON list of matched record tags

    discovered_at = Column(DateTime, default=datetime.utcnow)
    downloaded_at = Column(DateTime, nullable=True)


def init_db():
    """Initialize database, create tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
    Base.metadata.create_all(engine)
    return engine


def get_session(engine=None):
    """Create a new DB session."""
    if engine is None:
        engine = init_db()
    Session = sessionmaker(bind=engine)
    return Session()
