"""Database operations — thread-safe repository for all workers."""

import json
import threading
from datetime import datetime
from sqlalchemy import and_
from .models import (
    EmailAccount, TrackerAccount, PageProgress, Torrent, BlockedDomain,
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
        """Take one fresh email and mark it IN_USE. Skips blocked domains. Returns None if none available."""
        with _lock:
            s = self._session()
            blocked = {d.domain for d in s.query(BlockedDomain).all()}
            for acc in s.query(EmailAccount).filter(EmailAccount.status == AccountStatus.FRESH).all():
                domain = acc.email.split("@")[1].lower() if "@" in acc.email else ""
                if domain in blocked:
                    acc.status = AccountStatus.BANNED
                    s.commit()
                    continue
                acc.status = AccountStatus.IN_USE
                s.commit()
                s.refresh(acc)
                return acc
            return None

    def mark_email_used(self, email_id: int):
        with _lock:
            s = self._session()
            acc = s.query(EmailAccount).get(email_id)
            if acc:
                acc.status = AccountStatus.EXHAUSTED
                s.commit()

    def import_emails(self, lines: list[str]) -> tuple[int, int]:
        """
        Bulk import emails from login:password lines.
        Skips duplicates and emails with blocked domains.
        Returns (added_count, skipped_blocked_count).
        """
        added = 0
        skipped_blocked = 0
        with _lock:
            s = self._session()
            blocked = {d.domain for d in s.query(BlockedDomain).all()}
            for line in lines:
                line = line.strip()
                if not line or ":" not in line:
                    continue
                email, password = line.split(":", 1)
                email = email.strip()
                password = password.strip()
                if not email or not password:
                    continue
                domain = email.split("@")[1].lower() if "@" in email else ""
                if domain in blocked:
                    skipped_blocked += 1
                    continue
                existing = s.query(EmailAccount).filter(EmailAccount.email == email).first()
                if existing:
                    continue
                s.add(EmailAccount(email=email, password=password, status=AccountStatus.FRESH))
                added += 1
            s.commit()
        return added, skipped_blocked

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

    def clear_all_emails(self) -> int:
        """Delete all email accounts. Returns count deleted."""
        with _lock:
            s = self._session()
            count = s.query(EmailAccount).count()
            s.query(EmailAccount).delete()
            s.commit()
            return count

    # ── Blocked Domains ─────────────────────────────────────────

    def add_blocked_domain(self, domain: str, reason: str = "") -> bool:
        """Block a domain and ban all existing emails with it. Returns True if newly added."""
        domain = domain.lower().strip()
        with _lock:
            s = self._session()
            if s.query(BlockedDomain).filter(BlockedDomain.domain == domain).first():
                return False
            s.add(BlockedDomain(domain=domain, reason=reason))
            # Ban all existing emails with this domain
            for e in s.query(EmailAccount).filter(
                EmailAccount.email.like(f"%@{domain}")
            ).all():
                e.status = AccountStatus.BANNED
            s.commit()
            return True

    def remove_blocked_domain(self, domain: str):
        """Remove a domain from the blocklist (does not unban emails)."""
        domain = domain.lower().strip()
        with _lock:
            s = self._session()
            d = s.query(BlockedDomain).filter(BlockedDomain.domain == domain).first()
            if d:
                s.delete(d)
                s.commit()

    def get_blocked_domains(self) -> list[dict]:
        """Get all blocked domains for GUI display."""
        with _lock:
            s = self._session()
            rows = s.query(BlockedDomain).order_by(BlockedDomain.blocked_at.desc()).all()
            return [
                {"domain": d.domain, "reason": d.reason or "", "blocked_at": str(d.blocked_at)}
                for d in rows
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

    def torrent_exists(self, topic_id: str) -> bool:
        """Check if topic_id already in DB."""
        with _lock:
            s = self._session()
            return s.query(Torrent).filter(Torrent.topic_id == topic_id).first() is not None

    def save_torrent_data(self, data: dict) -> Torrent | None:
        """
        Save fully parsed torrent data. Skips duplicates.
        data keys: topic_id, title_raw, category_id, page_number,
                   studio, film_name, tags, formats, devices,
                   year, description, duration, file_size,
                   cover_url, cover_path, download_url, seeds, peers.
        """
        with _lock:
            s = self._session()
            if s.query(Torrent).filter(Torrent.topic_id == data["topic_id"]).first():
                return None
            t = Torrent(
                topic_id=data["topic_id"],
                title_raw=data["title_raw"],
                category_id=data["category_id"],
                page_number=data["page_number"],
                status=TorrentStatus.PARSED,
                studio=data.get("studio", ""),
                film_name=data.get("film_name", ""),
                tags=json.dumps(data.get("tags", []), ensure_ascii=False),
                formats=json.dumps(data.get("formats", []), ensure_ascii=False),
                devices=json.dumps(data.get("devices", []), ensure_ascii=False),
                year=data.get("year", ""),
                description=data.get("description", ""),
                duration=data.get("duration", ""),
                file_size=data.get("file_size", ""),
                cover_url=data.get("cover_url"),
                cover_path=data.get("cover_path"),
                download_url=data.get("download_url"),
                seeds=data.get("seeds", 0),
                peers=data.get("peers", 0),
            )
            s.add(t)
            s.commit()
            s.refresh(t)
            return t

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
                "parsed": s.query(Torrent).filter(Torrent.status == TorrentStatus.PARSED).count(),
                "approved": s.query(Torrent).filter(Torrent.status == TorrentStatus.APPROVED).count(),
                "skipped": s.query(Torrent).filter(Torrent.status == TorrentStatus.SKIPPED).count(),
                "errors": s.query(Torrent).filter(Torrent.status == TorrentStatus.ERROR).count(),
                "fresh_emails": s.query(EmailAccount).filter(EmailAccount.status == AccountStatus.FRESH).count(),
                "pages_completed": s.query(PageProgress).filter(PageProgress.is_completed == True).count(),
            }
