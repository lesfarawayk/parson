"""NotLetters.com API client — buy emails, read letters, manage passwords."""

import logging
import requests
from dataclasses import dataclass

log = logging.getLogger(__name__)

BASE_URL = "https://api.notletters.com/v1"


@dataclass
class NotLettersEmail:
    email: str
    password: str


@dataclass
class Letter:
    id: str
    sender: str
    sender_name: str
    subject: str
    html: str
    text: str
    star: bool
    date: int


class NotLettersClient:
    """Client for NotLetters.com API."""

    def __init__(self, api_token: str):
        self.token = api_token
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        })

    def get_balance(self) -> dict:
        """Get account info and balance."""
        resp = self.session.get(f"{BASE_URL}/me")
        resp.raise_for_status()
        data = resp.json()["data"]
        log.info(f"NotLetters balance: {data['balance']}, user: {data['username']}")
        return data

    def buy_emails(self, count: int = 1, type_email: int = 0) -> list[NotLettersEmail]:
        """
        Buy email accounts.
        type_email: 0=Limited, 1=Unlimited, 2=RU zone, 3=Personal
        Returns list of NotLettersEmail.
        """
        resp = self.session.post(f"{BASE_URL}/buy-emails", json={
            "count": count,
            "type_email": type_email,
        })
        resp.raise_for_status()
        data = resp.json()["data"]

        emails = []
        for entry in data:
            # Format: "email@domain:password"
            email, password = entry.split(":", 1)
            emails.append(NotLettersEmail(email=email, password=password))

        log.info(f"Bought {len(emails)} emails from NotLetters")
        return emails

    def get_letters(self, email: str, password: str,
                    search: str | None = None) -> list[Letter]:
        """
        Fetch letters from a mailbox.
        Optional search filter searches by content, subject, and sender.
        """
        payload = {
            "email": email,
            "password": password,
        }
        if search:
            payload["filters"] = {"search": search}

        resp = self.session.post(f"{BASE_URL}/letters", json=payload)
        resp.raise_for_status()
        data = resp.json()["data"]

        letters = []
        for l in data.get("letters", []):
            letters.append(Letter(
                id=l["id"],
                sender=l["sender"],
                sender_name=l.get("sender_name", ""),
                subject=l["subject"],
                html=l["letter"].get("html", ""),
                text=l["letter"].get("text", ""),
                star=l.get("star", False),
                date=l["date"],
            ))
        return letters

    def change_password(self, email: str, old_password: str, new_password: str) -> bool:
        """Change email password. Returns True on success."""
        resp = self.session.post(f"{BASE_URL}/change-password", json={
            "email": email,
            "old_password": old_password,
            "new_password": new_password,
        })
        resp.raise_for_status()
        return resp.json().get("code") == 200

    def wait_for_letter(self, email: str, password: str,
                        search: str, timeout: int = 120, interval: int = 5) -> Letter | None:
        """
        Poll mailbox until a letter matching search appears.
        Returns the first matching Letter or None on timeout.
        """
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            letters = self.get_letters(email, password, search=search)
            if letters:
                return letters[0]
            log.debug(f"No letter yet for {email}, searching '{search}', retrying in {interval}s...")
            time.sleep(interval)
        log.warning(f"Timeout waiting for letter on {email} (search='{search}')")
        return None
