"""Actions for registering email accounts on Rambler."""

import logging
import random
import string
import time
from playwright.sync_api import BrowserContext, Page

log = logging.getLogger(__name__)


def _random_string(length=10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _random_name() -> str:
    first_names = [
        "Alexei", "Ivan", "Dmitry", "Sergey", "Andrei",
        "Pavel", "Mikhail", "Oleg", "Nikolai", "Viktor"
    ]
    last_names = [
        "Ivanov", "Petrov", "Sidorov", "Smirnov", "Kuznetsov",
        "Popov", "Lebedev", "Volkov", "Kozlov", "Morozov"
    ]
    return random.choice(first_names), random.choice(last_names)


def register_rambler_email(ctx: BrowserContext, rambler_url: str) -> dict | None:
    """
    Register a new Rambler email account.
    Returns {"email": str, "password": str} or None on failure.
    """
    page = ctx.new_page()
    try:
        login_name = _random_string(12)
        password = _random_string(8) + random.choice("!@#$%") + str(random.randint(10, 99))
        first_name, last_name = _random_name()

        page.goto(rambler_url, wait_until="domcontentloaded")
        time.sleep(random.uniform(2, 4))

        # Fill registration form — selectors may need adjustment for actual site
        # Rambler registration typically has: login, password, confirm password, name fields
        page.fill("input[name='login'], #login", login_name)
        time.sleep(random.uniform(0.3, 0.8))

        page.fill("input[name='password'], #newPassword", password)
        time.sleep(random.uniform(0.3, 0.8))

        page.fill("input[name='confirmPassword'], #confirmPassword", password)
        time.sleep(random.uniform(0.3, 0.8))

        # Try to fill optional name fields
        try:
            page.fill("input[name='firstname'], #firstname", first_name, timeout=3000)
            time.sleep(random.uniform(0.2, 0.5))
            page.fill("input[name='lastname'], #lastname", last_name, timeout=3000)
        except Exception:
            pass

        # Answer security question if present
        try:
            question_select = page.query_selector("select[name='question'], #question")
            if question_select:
                question_select.select_option(index=1)
                time.sleep(0.3)
                page.fill("input[name='answer'], #answer", _random_string(8), timeout=3000)
        except Exception:
            pass

        # Submit
        page.click("button[type='submit'], .rui-Button-content, button.rui-RamblerIdConfirmButton")
        time.sleep(random.uniform(3, 5))

        # Check for CAPTCHA — if present, wait for manual solving
        captcha = page.query_selector("iframe[src*='captcha'], .captcha, #captcha")
        if captcha:
            log.warning("CAPTCHA detected during email registration — waiting for manual solve (60s)")
            page.wait_for_selector("input[name='login']", state="hidden", timeout=60000)

        email = f"{login_name}@rambler.ru"
        log.info(f"Registered email: {email}")
        page.close()
        return {"email": email, "password": password}

    except Exception as e:
        log.error(f"Email registration failed: {e}")
        try:
            page.close()
        except Exception:
            pass
        return None
