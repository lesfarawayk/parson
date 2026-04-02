"""
Telegram CAPTCHA solver — sends captcha images to a Telegram chat and waits for the answer.

Flow:
1. Worker screenshots the captcha element
2. Bot sends image to your chat with a message like "CAPTCHA for worker parser-0"
3. You reply with the captcha text
4. Bot returns the answer to the worker

Uses Telegram Bot API directly (no extra dependencies).
"""

import logging
import time
import requests
import threading

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}"


class TelegramCaptchaSolver:
    """Send captcha images to Telegram, receive answers via polling."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api = API_BASE.format(token=bot_token)
        self._last_update_id = 0
        self._lock = threading.Lock()

        # Flush old messages on startup so we don't read stale replies
        self._flush_updates()

    def _flush_updates(self):
        """Skip all pending updates."""
        try:
            resp = requests.get(f"{self.api}/getUpdates", params={"timeout": 0}, timeout=10)
            data = resp.json()
            if data.get("ok") and data["result"]:
                self._last_update_id = data["result"][-1]["update_id"] + 1
        except Exception as e:
            log.warning(f"Telegram flush failed: {e}")

    def send_captcha(self, image_bytes: bytes, worker_id: str, context: str = "") -> str | None:
        """
        Send captcha image to Telegram and wait for reply.
        Returns the captcha answer text, or None on timeout.
        """
        # Send photo
        caption = f"CAPTCHA — {worker_id}"
        if context:
            caption += f"\n{context}"
        caption += "\nОтветь текстом капчи:"

        try:
            resp = requests.post(
                f"{self.api}/sendPhoto",
                data={"chat_id": self.chat_id, "caption": caption},
                files={"photo": ("captcha.png", image_bytes, "image/png")},
                timeout=30,
            )
            if not resp.json().get("ok"):
                log.error(f"Telegram sendPhoto failed: {resp.text}")
                return None
            msg_id = resp.json()["result"]["message_id"]
            log.info(f"Captcha sent to Telegram (msg_id={msg_id}), waiting for answer...")
        except Exception as e:
            log.error(f"Telegram send failed: {e}")
            return None

        # Poll for reply
        return self._wait_for_reply(timeout=120, interval=3)

    def send_message(self, text: str):
        """Send a simple text message (for notifications)."""
        try:
            requests.post(
                f"{self.api}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=10,
            )
        except Exception as e:
            log.warning(f"Telegram sendMessage failed: {e}")

    def _wait_for_reply(self, timeout: int = 120, interval: int = 3) -> str | None:
        """
        Poll Telegram for new messages from the chat.
        Returns the first text reply, or None on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with self._lock:
                    resp = requests.get(
                        f"{self.api}/getUpdates",
                        params={
                            "offset": self._last_update_id,
                            "timeout": min(interval, 5),
                            "allowed_updates": '["message"]',
                        },
                        timeout=interval + 5,
                    )
                    data = resp.json()

                    if data.get("ok"):
                        for update in data["result"]:
                            self._last_update_id = update["update_id"] + 1
                            msg = update.get("message", {})
                            # Only accept messages from our chat
                            if str(msg.get("chat", {}).get("id")) == str(self.chat_id):
                                text = msg.get("text", "").strip()
                                if text:
                                    log.info(f"Captcha answer received: {text}")
                                    return text
            except Exception as e:
                log.warning(f"Telegram poll error: {e}")

            time.sleep(interval)

        log.warning("Captcha answer timeout")
        return None
