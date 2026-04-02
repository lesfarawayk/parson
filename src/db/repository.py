"""Database operations — thread-safe repository for all workers."""

import json
import threading
from datetime import datetime
from sqlalchemy import and_
from .models import (
    EmailAccount, TrackerAccount, PageProgress, Torrent,
    AccountStatus, TorrentStatus, init_db, get_session
)

_lock = threading.Lock()


class Repository:
    def __init__(self):
        self.engine = init_db()

    def _session(self):
        return get_session(self.engine)

    # ── Email Accounts ──────────────────────────────────────────

    def add_email(self, email: str, password: str) -> EmailAccount:
        with _lock:
            s = self._session()
            acc = EmailAccount(email=email, password=password, status=AccountStatus.FRESH)
            s.add(acc)
            s.commit()
            s.refresh(acc)
            return acc

    def get_fresh_email(self) -> EmailAccount | None:
        """Take one fresh email and mark it IN_USE. Returns None if none available."""
        with _lock:
            s = self._session()
            acc = s.query(EmailAccount).filter(
                EmailAccount.status == AccountStatus.FRESH
            ).first()
            if acc:
                acc.status = AccountStatus.IN_USE
                s.commit()
                s.refresh(acc)
            return acc

    def mark_email_used(self, email_id: int):
        with _lock:
            s = self._session()
            acc = s.query(EmailAccount).get(email_id)
            if acc:
                acc.status = AccountStatus.EXHAUSTED
                s.commit()

    def import_emails(self, lines: list[str]) -> int:
        """
        Bulk import emails from login:password lines.
        Skips duplicates. Returns count of newly added.
        """
        added = 0
        with _lock:
            s = self._session()
            for line in lines:
                line = line.strip()
                if not line or ":" not in line:
                    continue
                email, password = line.split(":", 1)
                email = email.strip()
                password = password.strip()
                if not email or not password:
                    continue
                existing = s.query(EmailAccount).filter(EmailAccount.email == email).first()
                if existing:
                    continue
                s.add(EmailAccount(email=email, password=password, status=AccountStatus.FRESH))
                added += 1
            s.commit()
        return added

    def get_all_emails(self) -> list[dict]:
        """Get all emails with their status for GUI display."""
        with _lock:
            s = self._session()
            emails = s.query(EmailAccount).order_by(EmailAccount.id).all()
            return [
                {
                    "id": e.id,
                    "email": e.email,
                    "status": e.status.value,
                    "created_at": str(e.created_at) if e.created_at else "",
                }
                for e in emails
            ]

    # ── Tracker Accounts ────────────────────────────────────────

    def add_tracker_account(self, username: str, password: str, email_id: int) -> TrackerAccount:
        with _lock:
            s = self._session()
            acc = TrackerAccount(
                username=username, password=password,
                email_id=email_id, status=AccountStatus.FRESH
            )
            s.add(acc)
            s.commit()
            s.refresh(acc)
            return acc

    def get_fresh_tracker_account(self) -> TrackerAccount | None:
        """Take one fresh account and mark it IN_USE."""
        with _lock:
            s = self._session()
            acc = s.query(TrackerAccount).filter(
                TrackerAccount.status == AccountStatus.FRESH
            ).first()
            if acc:
                acc.status = AccountStatus.IN_USE
                s.commit()
                s.refresh(acc)
            return acc

    def increment_download(self, account_id: int) -> bool:
        """Increment download count. Returns False if limit reached."""
        with _lock:
            s = self._session()
            acc = s.query(TrackerAccount).get(account_id)
            if not acc:
                return False
            acc.downloads_count += 1
            if acc.downloads_count >= acc.max_downloads:
                acc.status = AccountStatus.EXHAUSTED
            s.commit()
            return acc.downloads_count < acc.max_downloads

    def exhaust_tracker_account(self, account_id: int):
        with _lock:
            s = self._session()
            acc = s.query(TrackerAccount).get(account_id)
            if acc:
                acc.status = AccountStatus.EXHAUSTED
                s.commit()

    # ── Page Progress ───────────────────────────────────────────

    def claim_next_page(self, category_id: str, worker_id: str) -> int | None:
        """Claim the next unprocessed page. Returns page number or None."""
        with _lock:
            s = self._session()
            # Find first unclaimed page
            existing = s.query(PageProgress).filter(
                and_(
                    PageProgress.category_id == category_id,
                    PageProgress.worker_id != None,
                    PageProgress.is_completed == False
                )
            ).all()
            claimed_pages = {p.page_number for p in existing}

            completed = s.query(PageProgress).filter(
                and_(
                    PageProgress.category_id == category_id,
                    PageProgress.is_completed == True
                )
            ).all()
            completed_pages = {p.page_number for p in completed}

            # Find all pages from config range
            from ..config_manager import load_config
            cfg = load_config()
            start = cfg["tracker"]["pages_start"]
            end = cfg["tracker"]["pages_end"]

            for page_num in range(start, end + 1):
                if page_num not in claimed_pages and page_num not in completed_pages:
                    progress = PageProgress(
                        category_id=category_id,
                        page_number=page_num,
                        worker_id=worker_id,
                        started_at=datetime.utcnow()
                    )
                    s.add(progress)
                    s.commit()
                    return page_num
            return None

    def complete_page(self, category_id: str, page_number: int, worker_id: str):
        with _lock:
            s = self._session()
            progress = s.query(PageProgress).filter(
                and_(
                    PageProgress.category_id == category_id,
                    PageProgress.page_number == page_number,
                    PageProgress.worker_id == worker_id
                )
            ).first()
            if progress:
                progress.is_completed = True
                progress.completed_at = datetime.utcnow()
                s.commit()

    def release_page(self, category_id: str, worker_id: str):
        """Release any pages claimed by this worker (on crash/restart)."""
        with _lock:
            s = self._session()
            pages = s.query(PageProgress).filter(
                and_(
                    PageProgress.category_id == category_id,
                    PageProgress.worker_id == worker_id,
                    PageProgress.is_completed == False
                )
            ).all()
            for p in pages:
                s.delete(p)
            s.commit()

    # ── Torrents ────────────────────────────────────────────────

    def add_torrent(self, topic_id: str, title: str, category_id: str, page_number: int) -> Torrent | None:
        """Add torrent if not exists. Returns None if duplicate."""
        with _lock:
            s = self._session()
            existing = s.query(Torrent).filter(Torrent.topic_id == topic_id).first()
            if existing:
                return None
            t = Torrent(
                topic_id=topic_id, title=title,
                category_id=category_id, page_number=page_number
            )
            s.add(t)
            s.commit()
            s.refresh(t)
            return t

    def update_torrent_tags(self, topic_id: str, download_tags: list, record_tags: list,
                            description: str = None, cover_url: str = None):
        with _lock:
            s = self._session()
            t = s.query(Torrent).filter(Torrent.topic_id == topic_id).first()
            if t:
                t.download_tags = json.dumps(download_tags, ensure_ascii=False)
                t.record_tags = json.dumps(record_tags, ensure_ascii=False)
                if description:
                    t.description = description
                if cover_url:
                    t.cover_url = cover_url
                if download_tags:
                    t.status = TorrentStatus.TAGGED
                else:
                    t.status = TorrentStatus.SKIPPED
                s.commit()

    def mark_torrent_downloading(self, topic_id: str):
        with _lock:
            s = self._session()
            t = s.query(Torrent).filter(Torrent.topic_id == topic_id).first()
            if t:
                t.status = TorrentStatus.DOWNLOADING
                s.commit()

    def mark_torrent_downloaded(self, topic_id: str, file_path: str, cover_path: str = None):
        with _lock:
            s = self._session()
            t = s.query(Torrent).filter(Torrent.topic_id == topic_id).first()
            if t:
                t.status = TorrentStatus.DOWNLOADED
                t.file_path = file_path
                t.downloaded_at = datetime.utcnow()
                if cover_path:
                    t.cover_path = cover_path
                s.commit()

    def mark_torrent_error(self, topic_id: str):
        with _lock:
            s = self._session()
            t = s.query(Torrent).filter(Torrent.topic_id == topic_id).first()
            if t:
                t.status = TorrentStatus.ERROR
                s.commit()

    def get_stats(self) -> dict:
        """Get overall statistics."""
        with _lock:
            s = self._session()
            return {
                "total_torrents": s.query(Torrent).count(),
                "downloaded": s.query(Torrent).filter(Torrent.status == TorrentStatus.DOWNLOADED).count(),
                "tagged": s.query(Torrent).filter(Torrent.status == TorrentStatus.TAGGED).count(),
                "skipped": s.query(Torrent).filter(Torrent.status == TorrentStatus.SKIPPED).count(),
                "errors": s.query(Torrent).filter(Torrent.status == TorrentStatus.ERROR).count(),
                "fresh_emails": s.query(EmailAccount).filter(EmailAccount.status == AccountStatus.FRESH).count(),
                "fresh_accounts": s.query(TrackerAccount).filter(TrackerAccount.status == AccountStatus.FRESH).count(),
                "pages_completed": s.query(PageProgress).filter(PageProgress.is_completed == True).count(),
            }
