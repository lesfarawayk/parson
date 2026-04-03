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


class DomainBannedException(Exception):
    """Raised when the tracker rejects the email domain as blacklisted."""
    def __init__(self, domain: str):
        self.domain = domain
        super().__init__(f"Domain '{domain}' is blacklisted by the tracker")


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

    # Strategy 1: profile selectors (row → link inside row)
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

    # Strategy 2: broad fallback — find all links to viewtopic.php
    if not topics:
        log.warning(
            f"[{profile.name}] Page {page_number}: profile selectors found 0 rows. "
            f"Trying broad fallback (a[href*='viewtopic'])..."
        )
        # Debug: dump what the page actually contains
        try:
            sample = page.evaluate("""() => {
                const trs = document.querySelectorAll('tr');
                const sample = Array.from(trs).slice(0, 5).map(tr => ({
                    classes: tr.className,
                    id: tr.id,
                    firstTd: tr.querySelector('td')?.className || '',
                }));
                const links = document.querySelectorAll('a[href*="viewtopic"]');
                return {
                    total_trs: trs.length,
                    sample_trs: sample,
                    viewtopic_links: links.length,
                    url: location.href,
                };
            }""")
            log.info(f"[{profile.name}] Page debug: {sample}")
        except Exception:
            pass

        all_links = page.query_selector_all("a[href*='viewtopic']")
        seen_ids = set()
        for link in all_links:
            href = link.get_attribute("href") or ""
            title = link.inner_text().strip()
            if not title or len(title) < 5:
                continue
            match = re.search(profile.topic_id_pattern, href)
            if match and match.group(1) not in seen_ids:
                seen_ids.add(match.group(1))
                topics.append({"topic_id": match.group(1), "title": title})

    log.info(f"[{profile.name}] Page {page_number}: found {len(topics)} topics")
    return topics


def _extract_field(text: str, *patterns: str) -> str:
    """Try multiple regex patterns on text, return first match group 1."""
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def extract_topic_data(page: Page, profile: TrackerProfile, topic_id: str) -> dict:
    """
    Open a topic page, extract all structured data:
    description, year, duration, file_size, cover_url, download_url, seeds, peers.
    """
    url = f"{profile.base_url}/forum/viewtopic.php?t={topic_id}"
    page.goto(url, wait_until="domcontentloaded")
    human_delay(1.0, 3.0)

    post_body = page.query_selector(profile.post_body_sel)
    body_text = post_body.inner_text().strip() if post_body else ""

    # --- Cover image ---
    cover_url = None
    if post_body:
        for sel in profile.cover_img_sel.split(","):
            sel = sel.strip()
            img = post_body.query_selector(sel)
            if img:
                src = img.get_attribute("title") or img.get_attribute("src") or ""
                if src and ("http" in src or src.startswith("//")):
                    cover_url = src
                    break

    # --- Extract structured fields from body text ---
    year = _extract_field(
        body_text,
        r"(?:Год\s*(?:выпуска|производства)?)\s*[:：]\s*(\d{4})",
        r"(?:Year)\s*[:：]\s*(\d{4})",
        r"\b((?:19|20)\d{2})\b",  # fallback: any 4-digit year
    )

    duration = _extract_field(
        body_text,
        r"(?:Продолжительность|Длительность|Duration)\s*[:：]\s*([\d:]+\s*(?:мин|min|ч|h|час)?[\d\s:]*)",
        r"(\d{1,2}:\d{2}:\d{2})",
        r"(\d{1,3}\s*мин)",
    )

    file_size = _extract_field(
        body_text,
        r"(?:Размер|File\s*size|Size)\s*[:：]\s*([\d.,]+\s*(?:GB|MB|TB|ГБ|МБ|ТБ))",
        r"([\d.,]+\s*(?:GB|MB|TB|ГБ|МБ|ТБ))",
    )

    # Description: everything after "Описание:" or first meaningful paragraph
    description = _extract_field(
        body_text,
        r"(?:Описание|Description)\s*[:：]\s*(.+?)(?:\n\n|\Z)",
    )
    if not description:
        description = body_text[:5000]

    # --- Download URL (constructed, not actually downloading) ---
    dl_path = profile.download_url_tpl.format(topic_id=topic_id)
    download_url = f"{profile.base_url}{dl_path}"

    # --- Seeds / Peers ---
    seeds = 0
    peers = 0
    try:
        for sel in profile.seeds_sel.split(","):
            el = page.query_selector(sel.strip())
            if el:
                txt = el.inner_text().strip().replace(",", "").replace(" ", "")
                if txt.isdigit():
                    seeds = int(txt)
                    break
    except Exception:
        pass
    try:
        for sel in profile.peers_sel.split(","):
            el = page.query_selector(sel.strip())
            if el:
                txt = el.inner_text().strip().replace(",", "").replace(" ", "")
                if txt.isdigit():
                    peers = int(txt)
                    break
    except Exception:
        pass

    log.info(
        f"[{profile.name}] Topic {topic_id}: year={year}, duration={duration}, "
        f"size={file_size}, seeds={seeds}, peers={peers}"
    )
    return {
        "description": description[:5000],
        "cover_url": cover_url,
        "year": year,
        "duration": duration,
        "file_size": file_size,
        "download_url": download_url,
        "seeds": seeds,
        "peers": peers,
    }


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


def solve_captcha_on_page(page: Page, profile: TrackerProfile,
                         captcha_solver=None, worker_id: str = "",
                         status_callback=None) -> bool:
    """
    Detect and solve CAPTCHA on the current page.
    If captcha_solver (TelegramCaptchaSolver) is provided, screenshots and sends to Telegram.
    Otherwise waits for manual input in the browser window.
    Returns True if captcha was handled.
    """
    captcha_img = page.query_selector(profile.reg_captcha_img_sel)
    if not captcha_img:
        return True  # no captcha

    captcha_input_sel = profile.reg_captcha_input_sel.split(",")[0].strip()
    captcha_input = page.query_selector(profile.reg_captcha_input_sel)

    if captcha_solver:
        # Screenshot the captcha image
        if status_callback:
            status_callback("CAPTCHA detected — sending to Telegram...")
        try:
            screenshot = captcha_img.screenshot()
            answer = captcha_solver.send_captcha(
                screenshot, worker_id,
                context=f"{profile.name} registration",
            )
            if answer and captcha_input:
                captcha_input.fill("")
                captcha_input.type(answer, delay=random.randint(50, 150))
                log.info(f"Captcha answer entered: {answer}")
                return True
            else:
                log.warning("No captcha answer received from Telegram")
                return False
        except Exception as e:
            log.error(f"Telegram captcha error: {e}")
            # Fall through to manual mode

    # Manual mode — wait for user to type in the browser
    if status_callback:
        status_callback("CAPTCHA — solve it in browser window (90s)...")
    try:
        page.wait_for_function(
            f"() => document.querySelector('{captcha_input_sel}')?.value.length > 3",
            timeout=90000,
        )
        return True
    except Exception:
        return False


def _select_country(page: Page, profile: TrackerProfile):
    """Select Russia in the country dropdown if it exists."""
    country_sel = getattr(profile, "reg_country_sel", None)
    if not country_sel:
        return
    try:
        el = page.query_selector(country_sel)
        if not el:
            return
        # Try to pick Russia by option text, fall back to first non-empty option
        options = el.query_selector_all("option")
        for opt in options:
            text = (opt.inner_text() or "").strip()
            if "Россия" in text or "Russia" in text or "Российская" in text:
                val = opt.get_attribute("value")
                if val:
                    el.select_option(value=val)
                    log.info(f"[{profile.name}] Selected country: {text}")
                    return
        # Fall back: pick first option that has a non-empty, non-zero value
        for opt in options[1:]:
            val = opt.get_attribute("value") or ""
            if val and val != "0":
                el.select_option(value=val)
                log.info(f"[{profile.name}] Selected country fallback: {opt.inner_text().strip()}")
                return
    except Exception as e:
        log.warning(f"Country select failed: {e}")


def activate_via_email(page: Page, profile: TrackerProfile,
                       notletters_client, email_addr: str, email_password: str,
                       status_callback=None) -> bool:
    """
    Poll NotLetters mailbox for activation email and navigate to the link.
    Returns True if activation link was clicked successfully.
    """
    if status_callback:
        status_callback("Waiting for activation email (up to 5 min)...")

    # Poll with broad search terms
    letter = None
    for search in ["актив", "activat", "confirm", "подтвер"]:
        letter = notletters_client.wait_for_letter(
            email_addr, email_password,
            search=search, timeout=300, interval=10,
        )
        if letter:
            break

    if not letter:
        log.warning(f"[{profile.name}] No activation email received for {email_addr}")
        if status_callback:
            status_callback("No activation email — check spam or try again")
        return False

    log.info(f"[{profile.name}] Got activation email: {letter.subject}")

    # Extract activation URL — look for link containing tracker domain
    content = letter.html or letter.text or ""
    domain = profile.base_url.replace("https://", "").replace("http://", "")
    # Strip any path after domain to get just hostname
    domain = domain.split("/")[0]

    # Find all URLs in the email
    all_urls = re.findall(r'https?://[^\s"\'<>\]]+', content)
    activation_url = None
    for url in all_urls:
        if domain in url and ("activ" in url.lower() or "confirm" in url.lower()
                              or "mode=activate" in url.lower()
                              or "profile.php" in url.lower()):
            activation_url = url
            break

    if not activation_url:
        # Try any URL from that domain
        for url in all_urls:
            if domain in url:
                activation_url = url
                break

    if not activation_url:
        log.warning(f"[{profile.name}] Could not find activation URL in email")
        return False

    log.info(f"[{profile.name}] Navigating to activation URL: {activation_url}")
    if status_callback:
        status_callback("Clicking activation link...")

    page.goto(activation_url, wait_until="domcontentloaded")
    human_delay(2, 4)

    # Check page for success indicators
    page_text = page.inner_text("body")[:1000].lower()
    success = any(kw in page_text for kw in [
        "активирован", "activated", "успешно", "success",
        "можете войти", "you can now login", "account has been",
    ])
    if success:
        log.info(f"[{profile.name}] Account activated for {email_addr}")
    else:
        log.warning(f"[{profile.name}] Activation page text (may still be ok): {page_text[:200]}")

    return True


def register_on_tracker(ctx: BrowserContext, profile: TrackerProfile,
                        username: str, password: str, email: str,
                        captcha_solver=None, worker_id: str = "",
                        status_callback=None,
                        notletters_client=None, email_password: str = "") -> bool:
    """
    Register a new account on the tracker.
    captcha_solver: TelegramCaptchaSolver instance or None (manual mode).
    Returns True on success.
    """
    page = ctx.new_page()
    try:
        page.goto(f"{profile.base_url}{profile.register_url}", wait_until="domcontentloaded")
        human_delay(2, 4)

        # Accept rules — TorrentPier shows rules page first with agree button
        # Try multiple strategies to find and click the agree button
        agreed = False
        try:
            # Strategy 1: selector from profile
            agree_btn = page.query_selector(profile.reg_agree_sel)
            if agree_btn and agree_btn.is_visible():
                agree_btn.click()
                agreed = True
        except Exception:
            pass

        if not agreed:
            try:
                # Strategy 2: find by Russian text "Я согласен"
                page.click("text=Я согласен", timeout=5000)
                agreed = True
            except Exception:
                pass

        if not agreed:
            try:
                # Strategy 3: find any submit/button with agree-like text
                page.click("input[value*='согласен'], input[value*='Agree'], input[name='agreed']", timeout=5000)
                agreed = True
            except Exception:
                pass

        if not agreed:
            try:
                # Strategy 4: find link with agree text
                page.click("a:has-text('согласен'), a:has-text('Agree')", timeout=5000)
                agreed = True
            except Exception:
                pass

        if agreed:
            page.wait_for_load_state("domcontentloaded")
            human_delay(2, 3)
            if status_callback:
                status_callback("Rules accepted, filling form...")
        else:
            # Maybe there's no rules page, form is shown directly
            log.info(f"[{profile.name}] No agree button found — maybe form is shown directly")

        # Debug: dump all form fields so we can see what's on the page
        fields = page.evaluate("""() => {
            const inputs = document.querySelectorAll('input, select, textarea');
            return Array.from(inputs).map(el => ({
                tag: el.tagName,
                type: el.type || '',
                name: el.name || '',
                id: el.id || '',
                value: el.value || '',
                placeholder: el.placeholder || '',
            }));
        }""")
        log.info(f"[{profile.name}] Registration form fields: {fields}")

        # Fill form fields — try each with fallback to generic selectors
        def fill_field(selectors, value, field_name):
            for sel in selectors.split(","):
                sel = sel.strip()
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        el.fill(value)
                        log.info(f"Filled {field_name} via {sel}")
                        return True
                except Exception:
                    continue
            # Fallback: try all text inputs in order
            log.warning(f"Could not fill {field_name} with selectors: {selectors}")
            return False

        fill_field(profile.reg_user_sel, username, "username")
        human_delay(0.3, 0.8)
        fill_field(profile.reg_pass_sel, password, "password")
        human_delay(0.3, 0.8)
        fill_field(profile.reg_pass_confirm_sel, password, "password_confirm")
        human_delay(0.3, 0.8)
        fill_field(profile.reg_email_sel, email, "email")
        human_delay(0.3, 0.8)

        # Country dropdown
        _select_country(page, profile)
        human_delay(0.2, 0.5)

        # CAPTCHA
        solve_captcha_on_page(page, profile, captcha_solver, worker_id, status_callback)

        # Check the 18+ / agreement checkbox if present
        try:
            checkboxes = page.query_selector_all("input[type='checkbox']")
            for cb in checkboxes:
                if not cb.is_checked():
                    cb.check()
                    log.info("Checked agreement checkbox")
        except Exception:
            pass

        # Submit
        try:
            page.click(profile.reg_submit_sel, timeout=5000)
        except Exception:
            # Fallback: click by text
            try:
                page.click("text=Отправить", timeout=5000)
            except Exception:
                page.click("input[type='submit']", timeout=5000)
        page.wait_for_load_state("domcontentloaded")
        human_delay(2, 4)

        page_text = page.inner_text("body")[:1000].lower()

        # Check for domain blacklist error before anything else
        if "чёрном списке" in page_text or "black list" in page_text or "blacklisted" in page_text:
            domain = email.split("@")[1] if "@" in email else ""
            log.warning(f"[{profile.name}] Domain '{domain}' blacklisted by tracker during registration")
            page.close()
            raise DomainBannedException(domain)

        reg_success = (
            "profile.php" in page.url or
            "login" in page.url.lower() or
            "подтвер" in page_text or
            "confirm" in page_text or
            "актив" in page_text or
            "учётная запись была создана" in page_text or
            "account has been created" in page_text
        )

        if not reg_success:
            log.warning(f"[{profile.name}] Registration may have failed for {username}")
            page.close()
            return False

        log.info(f"[{profile.name}] Registration form submitted, account created: {username}")

        # Email activation required
        needs_activation = (
            "актив" in page_text or
            "activat" in page_text or
            "учётная запись была создана" in page_text or
            "account has been created" in page_text
        )

        if needs_activation and notletters_client and email_password:
            activated = activate_via_email(
                page, profile, notletters_client, email, email_password,
                status_callback=status_callback,
            )
            if not activated:
                log.warning(f"[{profile.name}] Email activation failed for {username}")
                page.close()
                return False
        elif needs_activation:
            log.warning(
                f"[{profile.name}] Account {username} needs email activation "
                f"but no NotLetters client configured — login may fail"
            )
            if status_callback:
                status_callback("Account needs email activation — configure NotLetters API token")

        page.close()
        log.info(f"[{profile.name}] Registered and activated: {username}")
        return True

    except Exception as e:
        log.error(f"[{profile.name}] Registration error: {e}")
        try:
            page.close()
        except Exception:
            pass
        return False
