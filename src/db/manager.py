"""Thread-safe database manager for coordinating workers."""

import threading
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session

from .models import (
    Account, EmailAccount, Tag, Torrent, PageStatus,
    ProxyConfig, WorkerState, init_db
)


class DatabaseManager:
    """Thread-safe database access layer for all workers."""

    def __init__(self, db_path: str = 'parson.db'):
        self._session_factory = init_db(db_path)
        self._lock = threading.Lock()

    def get_session(self) -> Session:
        return self._session_factory()

    # ── Page coordination ──

    def claim_next_page(self, worker_id: str) -> Optional[int]:
        """Atomically claim the next unprocessed page for a worker.
        Returns page number or None if no pages left."""
        with self._lock:
            session = self.get_session()
            try:
                page = (
                    session.query(PageStatus)
                    .filter(PageStatus.status == 'pending')
                    .order_by(PageStatus.page_number)
                    .first()
                )
                if not page:
                    return None
                page.status = 'in_progress'
                page.assigned_worker = worker_id
                page.started_at = datetime.utcnow()
                session.commit()
                return page.page_number
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def complete_page(self, page_number: int, torrents_found: int = 0,
                      torrents_downloaded: int = 0):
        with self._lock:
            session = self.get_session()
            try:
                page = session.query(PageStatus).filter_by(page_number=page_number).first()
                if page:
                    page.status = 'completed'
                    page.completed_at = datetime.utcnow()
                    page.torrents_found = torrents_found
                    page.torrents_downloaded = torrents_downloaded
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def fail_page(self, page_number: int, error: str):
        with self._lock:
            session = self.get_session()
            try:
                page = session.query(PageStatus).filter_by(page_number=page_number).first()
                if page:
                    page.status = 'pending'  # Return to queue for retry
                    page.assigned_worker = None
                    page.error_message = error
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def init_pages(self, total_pages: int):
        """Initialize page records if they don't exist yet."""
        session = self.get_session()
        try:
            existing = session.query(PageStatus).count()
            if existing == 0:
                for i in range(1, total_pages + 1):
                    session.add(PageStatus(page_number=i))
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── Account management ──

    def get_available_account(self, worker_id: str, max_downloads: int = 3) -> Optional[Account]:
        """Get an account that hasn't hit the download limit."""
        with self._lock:
            session = self.get_session()
            try:
                account = (
                    session.query(Account)
                    .filter(
                        Account.is_active == True,
                        Account.is_exhausted == False,
                        Account.in_use_by.is_(None),
                        Account.downloads_count < max_downloads
                    )
                    .first()
                )
                if account:
                    account.in_use_by = worker_id
                    session.commit()
                    # Detach and return a copy of data
                    return self._copy_account(account)
                return None
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def _copy_account(self, acc: Account) -> Account:
        """Create a detached copy of account data."""
        copy = Account(
            id=acc.id, username=acc.username, password=acc.password,
            email=acc.email, downloads_count=acc.downloads_count,
            is_active=acc.is_active, is_exhausted=acc.is_exhausted,
            in_use_by=acc.in_use_by
        )
        return copy

    def release_account(self, account_id: int):
        with self._lock:
            session = self.get_session()
            try:
                acc = session.query(Account).filter_by(id=account_id).first()
                if acc:
                    acc.in_use_by = None
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def increment_download(self, account_id: int, max_downloads: int = 3):
        """Increment download counter; mark exhausted if limit reached."""
        with self._lock:
            session = self.get_session()
            try:
                acc = session.query(Account).filter_by(id=account_id).first()
                if acc:
                    acc.downloads_count += 1
                    if acc.downloads_count >= max_downloads:
                        acc.is_exhausted = True
                        acc.in_use_by = None
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def create_account(self, username: str, password: str, email: str) -> int:
        session = self.get_session()
        try:
            acc = Account(username=username, password=password, email=email)
            session.add(acc)
            session.commit()
            return acc.id
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── Email accounts ──

    def get_unused_email(self) -> Optional[EmailAccount]:
        """Get an email that hasn't been used for tracker registration."""
        with self._lock:
            session = self.get_session()
            try:
                email = (
                    session.query(EmailAccount)
                    .filter(EmailAccount.is_used == False)
                    .first()
                )
                if email:
                    result = EmailAccount(
                        id=email.id, email=email.email,
                        password=email.password, provider=email.provider
                    )
                    email.is_used = True
                    session.commit()
                    return result
                return None
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def add_email_account(self, email: str, password: str, provider: str = 'rambler'):
        session = self.get_session()
        try:
            acc = EmailAccount(email=email, password=password, provider=provider)
            session.add(acc)
            session.commit()
            return acc.id
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── Torrents ──

    def save_torrent(self, torrent_data: dict) -> int:
        session = self.get_session()
        try:
            existing = session.query(Torrent).filter_by(
                tracker_id=torrent_data['tracker_id']
            ).first()
            if existing:
                for key, val in torrent_data.items():
                    if key != 'tags' and hasattr(existing, key):
                        setattr(existing, key, val)
                torrent = existing
            else:
                torrent = Torrent(**{k: v for k, v in torrent_data.items() if k != 'tags'})
                session.add(torrent)

            # Handle tags
            if 'tags' in torrent_data:
                for tag_name, tag_type in torrent_data['tags']:
                    tag = session.query(Tag).filter_by(name=tag_name).first()
                    if not tag:
                        tag = Tag(name=tag_name, tag_type=tag_type)
                        session.add(tag)
                    if tag not in torrent.tags:
                        torrent.tags.append(tag)

            session.commit()
            return torrent.id
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def is_torrent_known(self, tracker_id: str) -> bool:
        session = self.get_session()
        try:
            return session.query(Torrent).filter_by(tracker_id=tracker_id).first() is not None
        finally:
            session.close()

    # ── Stats ──

    def get_stats(self) -> dict:
        session = self.get_session()
        try:
            total_pages = session.query(PageStatus).count()
            done_pages = session.query(PageStatus).filter_by(status='completed').count()
            in_progress = session.query(PageStatus).filter_by(status='in_progress').count()
            total_torrents = session.query(Torrent).count()
            downloaded = session.query(Torrent).filter_by(is_downloaded=True).count()
            accounts = session.query(Account).filter_by(is_active=True).count()
            emails = session.query(EmailAccount).filter_by(is_used=False).count()
            return {
                'total_pages': total_pages,
                'completed_pages': done_pages,
                'in_progress_pages': in_progress,
                'pending_pages': total_pages - done_pages - in_progress,
                'total_torrents': total_torrents,
                'downloaded_torrents': downloaded,
                'active_accounts': accounts,
                'available_emails': emails,
            }
        finally:
            session.close()

    # ── Proxies ──

    def get_proxy(self) -> Optional[str]:
        """Get the least recently used active proxy."""
        with self._lock:
            session = self.get_session()
            try:
                proxy = (
                    session.query(ProxyConfig)
                    .filter(ProxyConfig.is_active == True)
                    .order_by(ProxyConfig.last_used_at.asc().nullsfirst())
                    .first()
                )
                if proxy:
                    proxy.last_used_at = datetime.utcnow()
                    session.commit()
                    return proxy.address
                return None
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def add_proxy(self, address: str):
        session = self.get_session()
        try:
            session.add(ProxyConfig(address=address))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── Worker state ──

    def update_worker_state(self, worker_id: str, worker_type: str, **kwargs):
        session = self.get_session()
        try:
            state = session.query(WorkerState).filter_by(worker_id=worker_id).first()
            if not state:
                state = WorkerState(worker_id=worker_id, worker_type=worker_type)
                session.add(state)
            for key, val in kwargs.items():
                if hasattr(state, key):
                    setattr(state, key, val)
            state.last_active_at = datetime.utcnow()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
