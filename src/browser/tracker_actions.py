"""High-level actions on the tracker site performed via Playwright."""

import logging
import re
import time
import random
from pathlib import Path
from playwright.sync_api import BrowserContext, Page

log = logging.getLogger(__name__)

HUMAN_DELAY = (0.5, 2.0)  # seconds range for human-like delays


def human_delay(low=None, high=None):
    lo = low or HUMAN_DELAY[0]
    hi = high or HUMAN_DELAY[1]
    time.sleep(random.uniform(lo, hi))


def login(ctx: BrowserContext, base_url: str, username: str, password: str) -> Page:
    """Log in to the tracker. Returns the page after successful login."""
    page = ctx.new_page()
    page.goto(f"{base_url}/forum/login.php", wait_until="domcontentloaded")
    human_delay()

    page.fill("#login-form-login-user-name, input[name='login_username']", username)
    human_delay(0.3, 0.8)
    page.fill("#login-form-login-password, input[name='login_password']", password)
    human_delay(0.3, 0.8)
    page.click("#login-form-submit, input[name='login']")
    page.wait_for_load_state("domcontentloaded")
    human_delay()

    log.info(f"Logged in as {username}")
    return page


def get_topic_list(page: Page, forum_url: str, category_id: str, page_number: int) -> list[dict]:
    """
    Navigate to category page and extract topic list.
    Returns list of {"topic_id": str, "title": str}.
    """
    # rutracker pagination: start param = (page-1)*50
    start = (page_number - 1) * 50
    url = f"{forum_url}?f={category_id}&start={start}"
    page.goto(url, wait_until="domcontentloaded")
    human_delay(1.0, 3.0)

    topics = []
    # Topic links in the forum listing
    rows = page.query_selector_all("tr.hl-tr, tr.t-row")
    for row in rows:
        link = row.query_selector("a.torTopic, a.tt-text, td.t-title a")
        if not link:
            continue
        href = link.get_attribute("href") or ""
        title = link.inner_text().strip()

        # Extract topic id from href like viewtopic.php?t=12345
        match = re.search(r"t=(\d+)", href)
        if match:
            topics.append({"topic_id": match.group(1), "title": title})

    log.info(f"Page {page_number}: found {len(topics)} topics")
    return topics


def extract_topic_details(page: Page, base_url: str, topic_id: str, download_tags: list, record_tags: list) -> dict:
    """
    Open a topic page, extract description, cover image, and match tags.
    Returns {
        "description": str,
        "cover_url": str | None,
        "matched_download_tags": list,
        "matched_record_tags": list,
    }
    """
    url = f"{base_url}/forum/viewtopic.php?t={topic_id}"
    page.goto(url, wait_until="domcontentloaded")
    human_delay(1.0, 3.0)

    # Get post body
    post_body = page.query_selector(".post_body, .post-body, #topic_main .post_wrap .post_body")
    description = post_body.inner_text().strip() if post_body else ""
    description_lower = description.lower()

    # Cover image — usually the first image inside the post body
    cover_url = None
    if post_body:
        img = post_body.query_selector("img, var.postImg")
        if img:
            cover_url = img.get_attribute("src") or img.get_attribute("title") or None

    # Match tags (case-insensitive)
    matched_dl = [t for t in download_tags if t.lower() in description_lower]
    matched_rec = [t for t in record_tags if t.lower() in description_lower]

    log.info(f"Topic {topic_id}: dl_tags={matched_dl}, rec_tags={matched_rec}")
    return {
        "description": description[:5000],  # truncate for DB
        "cover_url": cover_url,
        "matched_download_tags": matched_dl,
        "matched_record_tags": matched_rec,
    }


def download_torrent_file(page: Page, base_url: str, topic_id: str, download_dir: Path) -> str | None:
    """
    Download .torrent file from topic page.
    Returns the saved file path or None on failure.
    """
    try:
        # The download link is typically: dl.php?t=<topic_id>
        dl_url = f"{base_url}/forum/dl.php?t={topic_id}"

        with page.expect_download(timeout=30000) as dl_info:
            page.goto(dl_url)

        download = dl_info.value
        dest = download_dir / f"{topic_id}.torrent"
        download.save_as(str(dest))
        log.info(f"Downloaded torrent {topic_id} -> {dest}")
        return str(dest)
    except Exception as e:
        log.error(f"Failed to download torrent {topic_id}: {e}")
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
