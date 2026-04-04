"""Database operations — thread-safe repository for all workers."""

import json
import threading
from contextlib import contextmanager
from datetime import datetime
from sqlalchemy import and_
from sqlalchemy.orm import sessionmaker
from .models import (
    EmailAccount, TrackerAccount, PageProgress, Torrent, BlockedDomain,
    AccountStatus, TorrentStatus, init_db,
)

_lock = threading.Lock()


class Repository:
    def __init__(self):
        self.engine = init_db()
        self._SessionFactory = sessionmaker(bind=self.engine)

    @contextmanager
    def _session(self):
        """Context manager that creates a session and always closes it."""
        s = self._SessionFactory()
        try:
            yield s
        finally:
            s.close()

    # ── Email Accounts ──────────────────────────────────────────

    def add_email(self, email: str, password: str) -> EmailAccount:
        with _lock, self._session() as s:
            acc = EmailAccount(email=email, password=password, status=AccountStatus.FRESH)
            s.add(acc)
            s.commit()
            s.refresh(acc)
            return acc

    def get_fresh_email(self) -> EmailAccount | None:
        """Take one fresh email and mark it IN_USE. Skips blocked domains."""
        with _lock, self._session() as s:
            blocked = {d.domain for d in s.query(BlockedDomain).all()}
            for acc in s.query(EmailAccount).filter(EmailAccount.status == AccountStatus.FRESH).all():
                domain = acc.email.split("@")[1].lower() if "@" in acc.email else ""
                if domain in blocked:
                    acc.status = AccountStatus.BANNED
                    s.commit()
                    continue
                acc.status = AccountStatus.IN_USE
                s.commit()
                # Detach from session before returning
                s.expunge(acc)
                return acc
            return None

    def mark_email_used(self, email_id: int):
        with _lock, self._session() as s:
            acc = s.query(EmailAccount).get(email_id)
            if acc:
                acc.status = AccountStatus.EXHAUSTED
                s.commit()

    def import_emails(self, lines: list[str]) -> tuple[int, int]:
        """Bulk import emails. Returns (added, skipped_blocked)."""
        added = 0
        skipped_blocked = 0
        with _lock, self._session() as s:
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
        with _lock, self._session() as s:
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
        with _lock, self._session() as s:
            count = s.query(EmailAccount).count()
            s.query(EmailAccount).delete()
            s.commit()
            return count

    # ── Blocked Domains ─────────────────────────────────────────

    def add_blocked_domain(self, domain: str, reason: str = "") -> bool:
        domain = domain.lower().strip()
        with _lock, self._session() as s:
            if s.query(BlockedDomain).filter(BlockedDomain.domain == domain).first():
                return False
            s.add(BlockedDomain(domain=domain, reason=reason))
            for e in s.query(EmailAccount).filter(
                EmailAccount.email.like(f"%@{domain}")
            ).all():
                e.status = AccountStatus.BANNED
            s.commit()
            return True

    def remove_blocked_domain(self, domain: str):
        domain = domain.lower().strip()
        with _lock, self._session() as s:
            d = s.query(BlockedDomain).filter(BlockedDomain.domain == domain).first()
            if d:
                s.delete(d)
                s.commit()

    def get_blocked_domains(self) -> list[dict]:
        with _lock, self._session() as s:
            rows = s.query(BlockedDomain).order_by(BlockedDomain.blocked_at.desc()).all()
            return [
                {"domain": d.domain, "reason": d.reason or "", "blocked_at": str(d.blocked_at)}
                for d in rows
            ]

    # ── Tracker Accounts ────────────────────────────────────────

    def add_tracker_account(self, username: str, password: str, email_id: int) -> TrackerAccount:
        with _lock, self._session() as s:
            acc = TrackerAccount(
                username=username, password=password,
                email_id=email_id, status=AccountStatus.FRESH
            )
            s.add(acc)
            s.commit()
            s.refresh(acc)
            return acc

    def get_fresh_tracker_account(self) -> TrackerAccount | None:
        with _lock, self._session() as s:
            acc = s.query(TrackerAccount).filter(
                TrackerAccount.status == AccountStatus.FRESH
            ).first()
            if acc:
                acc.status = AccountStatus.IN_USE
                s.commit()
                s.expunge(acc)
            return acc

    def increment_download(self, account_id: int) -> bool:
        with _lock, self._session() as s:
            acc = s.query(TrackerAccount).get(account_id)
            if not acc:
                return False
            acc.downloads_count += 1
            if acc.downloads_count >= acc.max_downloads:
                acc.status = AccountStatus.EXHAUSTED
            s.commit()
            return acc.downloads_count < acc.max_downloads

    def exhaust_tracker_account(self, account_id: int):
        with _lock, self._session() as s:
            acc = s.query(TrackerAccount).get(account_id)
            if acc:
                acc.status = AccountStatus.EXHAUSTED
                s.commit()

    # ── Page Progress ───────────────────────────────────────────

    def claim_next_page(self, category_id: str, worker_id: str) -> int | None:
        with _lock, self._session() as s:
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
        with _lock, self._session() as s:
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
        with _lock, self._session() as s:
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
        with _lock, self._session() as s:
            return s.query(Torrent).filter(Torrent.topic_id == topic_id).first() is not None

    def save_torrent_data(self, data: dict) -> Torrent | None:
        """Save fully parsed torrent data. Skips duplicates."""
        with _lock, self._session() as s:
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
                actors=json.dumps(data.get("actors", []), ensure_ascii=False),
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
            s.expunge(t)
            return t

    def get_all_torrents(self, status_filter: str = "", limit: int = 500) -> list[dict]:
        with _lock, self._session() as s:
            q = s.query(Torrent).order_by(Torrent.id.desc())
            if status_filter:
                try:
                    st = TorrentStatus(status_filter)
                    q = q.filter(Torrent.status == st)
                except ValueError:
                    pass
            rows = q.limit(limit).all()
            return [
                {
                    "id": t.id,
                    "topic_id": t.topic_id,
                    "studio": t.studio or "",
                    "film_name": t.film_name or "",
                    "actors": t.actors or "[]",
                    "year": t.year or "",
                    "formats": t.formats or "[]",
                    "tags": t.tags or "[]",
                    "devices": t.devices or "[]",
                    "duration": t.duration or "",
                    "file_size": t.file_size or "",
                    "seeds": t.seeds or 0,
                    "peers": t.peers or 0,
                    "status": t.status.value if t.status else "",
                    "download_url": t.download_url or "",
                    "cover_path": t.cover_path or "",
                }
                for t in rows
            ]

    def update_torrent_status(self, topic_id: str, new_status: str):
        with _lock, self._session() as s:
            t = s.query(Torrent).filter(Torrent.topic_id == topic_id).first()
            if t:
                try:
                    t.status = TorrentStatus(new_status)
                    s.commit()
                except ValueError:
                    pass

    def clear_all_torrents(self) -> int:
        with _lock, self._session() as s:
            count = s.query(Torrent).count()
            s.query(Torrent).delete()
            s.query(PageProgress).delete()
            s.commit()
            return count

    def mark_torrent_error(self, topic_id: str):
        with _lock, self._session() as s:
            t = s.query(Torrent).filter(Torrent.topic_id == topic_id).first()
            if t:
                t.status = TorrentStatus.ERROR
                s.commit()

    def get_stats(self) -> dict:
        with _lock, self._session() as s:
            return {
                "total_torrents": s.query(Torrent).count(),
                "parsed": s.query(Torrent).filter(Torrent.status == TorrentStatus.PARSED).count(),
                "approved": s.query(Torrent).filter(Torrent.status == TorrentStatus.APPROVED).count(),
                "skipped": s.query(Torrent).filter(Torrent.status == TorrentStatus.SKIPPED).count(),
                "errors": s.query(Torrent).filter(Torrent.status == TorrentStatus.ERROR).count(),
                "fresh_emails": s.query(EmailAccount).filter(EmailAccount.status == AccountStatus.FRESH).count(),
                "pages_completed": s.query(PageProgress).filter(PageProgress.is_completed == True).count(),
            }
