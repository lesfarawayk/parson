"""Database models for the torrent parser application."""

from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Boolean,
    DateTime, Float, ForeignKey, Table
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()

# Many-to-many: torrents <-> tags
torrent_tags = Table(
    'torrent_tags', Base.metadata,
    Column('torrent_id', Integer, ForeignKey('torrents.id'), primary_key=True),
    Column('tag_id', Integer, ForeignKey('tags.id'), primary_key=True),
)


class Account(Base):
    """Tracker account (login credentials)."""
    __tablename__ = 'accounts'

    id = Column(Integer, primary_key=True)
    username = Column(String(255), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    downloads_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    is_exhausted = Column(Boolean, default=False)  # True when download limit reached
    in_use_by = Column(String(100), nullable=True)  # worker_id using this account
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailAccount(Base):
    """Registered email account for tracker registration."""
    __tablename__ = 'email_accounts'

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    provider = Column(String(50), default='rambler')
    is_used = Column(Boolean, default=False)  # Used for tracker registration
    used_by_account_id = Column(Integer, ForeignKey('accounts.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Tag(Base):
    """Tag entity (genre, quality, etc.)."""
    __tablename__ = 'tags'

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    tag_type = Column(String(50), nullable=False)  # 'download_trigger' or 'record'

    torrents = relationship('Torrent', secondary=torrent_tags, back_populates='tags')


class Torrent(Base):
    """Parsed torrent entry."""
    __tablename__ = 'torrents'

    id = Column(Integer, primary_key=True)
    tracker_id = Column(String(50), unique=True, nullable=False)  # ID on tracker
    title = Column(String(1000), nullable=False)
    description = Column(Text, nullable=True)
    cover_url = Column(String(1000), nullable=True)
    cover_path = Column(String(1000), nullable=True)  # Local path to saved cover
    torrent_file_path = Column(String(1000), nullable=True)
    page_url = Column(String(1000), nullable=False)
    category_page = Column(Integer, nullable=True)  # Page number in category listing
    should_download = Column(Boolean, default=False)  # Matched download trigger tag
    is_downloaded = Column(Boolean, default=False)
    is_processed = Column(Boolean, default=False)  # Fully processed
    downloaded_by = Column(String(100), nullable=True)  # Account that downloaded
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tags = relationship('Tag', secondary=torrent_tags, back_populates='torrents')


class PageStatus(Base):
    """Tracks which category pages have been processed."""
    __tablename__ = 'page_status'

    id = Column(Integer, primary_key=True)
    page_number = Column(Integer, unique=True, nullable=False)
    status = Column(String(50), default='pending')  # pending, in_progress, completed, error
    assigned_worker = Column(String(100), nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    torrents_found = Column(Integer, default=0)
    torrents_downloaded = Column(Integer, default=0)


class ProxyConfig(Base):
    """Proxy configuration entries."""
    __tablename__ = 'proxy_configs'

    id = Column(Integer, primary_key=True)
    address = Column(String(500), nullable=False)  # full proxy URL
    is_active = Column(Boolean, default=True)
    last_used_at = Column(DateTime, nullable=True)
    fail_count = Column(Integer, default=0)


class WorkerState(Base):
    """Persistent worker state for crash recovery."""
    __tablename__ = 'worker_states'

    id = Column(Integer, primary_key=True)
    worker_id = Column(String(100), unique=True, nullable=False)
    worker_type = Column(String(50), nullable=False)  # 'parser' or 'email'
    status = Column(String(50), default='idle')  # idle, working, paused, error
    current_page = Column(Integer, nullable=True)
    current_torrent_id = Column(String(50), nullable=True)
    current_account_id = Column(Integer, ForeignKey('accounts.id'), nullable=True)
    last_action = Column(String(255), nullable=True)
    last_active_at = Column(DateTime, default=datetime.utcnow)


def get_engine(db_path: str = 'parson.db'):
    return create_engine(f'sqlite:///{db_path}', echo=False)


def init_db(db_path: str = 'parson.db'):
    """Create all tables and return a session factory."""
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)
