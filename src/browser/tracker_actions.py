"""High-level actions on tracker sites performed via Playwright.

All functions accept a TrackerProfile so they work with any supported tracker.
"""

import logging
import re
import time
import random
from pathlib import Path
from playwright.sync_api import BrowserContext, Page

from .tracker_profiles import TrackerProfile

log = logging.getLogger(__name__)

HUMAN_DELAY = (0.5, 2.0)


def human_delay(low=None, high=None):
    lo = low or HUMAN_DELAY[0]
    hi = high or HUMAN_DELAY[1]
    time.sleep(random.uniform(lo, hi))


def login(ctx: BrowserContext, profile: TrackerProfile, username: str, password: str) -> Page:
    """Log in to the tracker. Returns the page after successful login."""
    page = ctx.new_page()
    page.goto(f"{profile.base_url}{profile.login_url}", wait_until="domcontentloaded")
    human_delay()

    page.fill(profile.login_user_sel, username)
    human_delay(0.3, 0.8)
    page.fill(profile.login_pass_sel, password)
    human_delay(0.3, 0.8)
    page.click(profile.login_submit_sel)
    page.wait_for_load_state("domcontentloaded")
    human_delay()

    log.info(f"[{profile.name}] Logged in as {username}")
    return page


def get_topic_list(page: Page, profile: TrackerProfile, category_id: str, page_number: int) -> list[dict]:
    """
    Navigate to category page and extract topic list.
    Returns list of {"topic_id": str, "title": str}.
    """
    start = (page_number - 1) * profile.topics_per_page
    url = f"{profile.forum_url}?f={category_id}&start={start}"
    page.goto(url, wait_until="domcontentloaded")
    human_delay(1.0, 3.0)

    topics = []
    rows = page.query_selector_all(profile.topic_row_sel)
    for row in rows:
        link = row.query_selector(profile.topic_link_sel)
        if not link:
            continue
        href = link.get_attribute("href") or ""
        title = link.inner_text().strip()

        match = re.search(profile.topic_id_pattern, href)
        if match:
            topics.append({"topic_id": match.group(1), "title": title})

    log.info(f"[{profile.name}] Page {page_number}: found {len(topics)} topics")
    return topics


def extract_topic_details(page: Page, profile: TrackerProfile, topic_id: str,
                          download_tags: list, record_tags: list) -> dict:
    """
    Open a topic page, extract description, cover image, and match tags.
    """
    url = f"{profile.base_url}/forum/viewtopic.php?t={topic_id}"
    page.goto(url, wait_until="domcontentloaded")
    human_delay(1.0, 3.0)

    post_body = page.query_selector(profile.post_body_sel)
    description = post_body.inner_text().strip() if post_body else ""
    description_lower = description.lower()

    cover_url = None
    if post_body:
        img = post_body.query_selector(profile.cover_img_sel)
        if img:
            cover_url = img.get_attribute("src") or img.get_attribute("title") or None

    matched_dl = [t for t in download_tags if t.lower() in description_lower]
    matched_rec = [t for t in record_tags if t.lower() in description_lower]

    log.info(f"[{profile.name}] Topic {topic_id}: dl_tags={matched_dl}, rec_tags={matched_rec}")
    return {
        "description": description[:5000],
        "cover_url": cover_url,
        "matched_download_tags": matched_dl,
        "matched_record_tags": matched_rec,
    }


def download_torrent_file(page: Page, profile: TrackerProfile, topic_id: str,
                          download_dir: Path) -> str | None:
    """Download .torrent file from topic page. Returns saved path or None."""
    try:
        dl_path = profile.download_url_tpl.format(topic_id=topic_id)
        dl_url = f"{profile.base_url}{dl_path}"

        with page.expect_download(timeout=30000) as dl_info:
            page.goto(dl_url)

        download = dl_info.value
        dest = download_dir / f"{topic_id}.torrent"
        download.save_as(str(dest))
        log.info(f"[{profile.name}] Downloaded torrent {topic_id} -> {dest}")
        return str(dest)
    except Exception as e:
        log.error(f"[{profile.name}] Failed to download torrent {topic_id}: {e}")
        return None


def download_cover_image(page: Page, cover_url: str, download_dir: Path, topic_id: str) -> str | None:
    """Download cover image. Returns saved path or None."""
    if not cover_url:
        return None
    try:
        response = page.request.get(cover_url)
        if response.ok:
            ext = ".jpg"
            if ".png" in cover_url.lower():
                ext = ".png"
            dest = download_dir / "covers" / f"{topic_id}{ext}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(response.body())
            return str(dest)
    except Exception as e:
        log.warning(f"Failed to download cover for {topic_id}: {e}")
    return None


def register_on_tracker(ctx: BrowserContext, profile: TrackerProfile,
                        username: str, password: str, email: str,
                        status_callback=None) -> bool:
    """
    Register a new account on the tracker.
    Returns True on success. Handles CAPTCHA by waiting for manual input.
    """
    page = ctx.new_page()
    try:
        page.goto(f"{profile.base_url}{profile.register_url}", wait_until="domcontentloaded")
        human_delay(2, 4)

        # Accept rules
        try:
            agree_btn = page.query_selector(profile.reg_agree_sel)
            if agree_btn:
                agree_btn.click()
                page.wait_for_load_state("domcontentloaded")
                human_delay(1, 2)
        except Exception:
            pass

        # Fill form
        page.fill(profile.reg_user_sel, username)
        human_delay(0.3, 0.8)
        page.fill(profile.reg_pass_sel, password)
        human_delay(0.3, 0.8)
        page.fill(profile.reg_pass_confirm_sel, password)
        human_delay(0.3, 0.8)
        page.fill(profile.reg_email_sel, email)
        human_delay(0.3, 0.8)

        # CAPTCHA — wait for manual solve
        captcha = page.query_selector(profile.reg_captcha_img_sel)
        if captcha:
            if status_callback:
                status_callback("CAPTCHA — solve it in browser window (90s)...")
            try:
                page.wait_for_function(
                    f"() => document.querySelector('{profile.reg_captcha_input_sel.split(',')[0].strip()}')?.value.length > 3",
                    timeout=90000,
                )
            except Exception:
                human_delay(5, 10)

        # Submit
        page.click(profile.reg_submit_sel)
        page.wait_for_load_state("domcontentloaded")
        human_delay(2, 4)

        page_text = page.inner_text("body")[:500].lower()
        success = (
            "profile.php" in page.url or
            "login" in page.url.lower() or
            "подтвер" in page_text or
            "confirm" in page_text or
            "актив" in page_text
        )

        page.close()
        if success:
            log.info(f"[{profile.name}] Registered: {username}")
        else:
            log.warning(f"[{profile.name}] Registration may have failed for {username}")
        return success

    except Exception as e:
        log.error(f"[{profile.name}] Registration error: {e}")
        try:
            page.close()
        except Exception:
            pass
        return False
