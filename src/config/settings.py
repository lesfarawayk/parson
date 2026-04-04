"""Application configuration loader."""

import json
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class TrackerConfig:
    base_url: str = "https://rutracker.org"
    forum_url: str = "https://rutracker.org/forum/viewforum.php"
    category_id: str = "7"
    max_downloads_per_account: int = 3


@dataclass
class TagsConfig:
    download_triggers: List[str] = field(default_factory=lambda: [
        "4K", "2160p", "UHD", "3840x2160", "3080p", "3000p"
    ])
    record_tags: List[str] = field(default_factory=lambda: [
        "военное", "зарубежное", "драма", "комедия", "боевик",
        "фантастика", "триллер", "ужасы", "документальное", "мультфильм"
    ])


@dataclass
class WorkersConfig:
    parser_count: int = 3
    email_count: int = 1
    page_delay_min: float = 2.0
    page_delay_max: float = 5.0
    action_delay_min: float = 1.0
    action_delay_max: float = 3.0


@dataclass
class BrowserConfig:
    headless: bool = False
    slow_mo: int = 100


@dataclass
class EmailConfig:
    provider: str = "rambler"
    domain: str = "rambler.ru"


@dataclass
class AppConfig:
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    tags: TagsConfig = field(default_factory=TagsConfig)
    workers: WorkersConfig = field(default_factory=WorkersConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    proxies: List[str] = field(default_factory=list)
    download_dir: str = "./downloads"
    total_pages: int = 700

    @classmethod
    def load(cls, path: str = "config.json") -> "AppConfig":
        """Load config from JSON file, falling back to defaults."""
        config = cls()
        if not os.path.exists(path):
            return config

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if 'tracker' in data:
            config.tracker = TrackerConfig(**data['tracker'])
        if 'tags' in data:
            config.tags = TagsConfig(**data['tags'])
        if 'workers' in data:
            config.workers = WorkersConfig(**data['workers'])
        if 'browser' in data:
            config.browser = BrowserConfig(**data['browser'])
        if 'email' in data:
            config.email = EmailConfig(**data['email'])
        if 'proxies' in data:
            config.proxies = data['proxies']
        if 'download_dir' in data:
            config.download_dir = data['download_dir']
        if 'total_pages' in data:
            config.total_pages = data['total_pages']

        return config

    def save(self, path: str = "config.json"):
        """Save current config to JSON."""
        data = {
            'tracker': self.tracker.__dict__,
            'tags': self.tags.__dict__,
            'workers': self.workers.__dict__,
            'browser': self.browser.__dict__,
            'email': self.email.__dict__,
            'proxies': self.proxies,
            'download_dir': self.download_dir,
            'total_pages': self.total_pages,
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
