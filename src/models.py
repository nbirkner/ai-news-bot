from dataclasses import dataclass
from datetime import datetime


@dataclass
class Article:
    url: str
    title: str
    source_name: str
    source_domain: str
    published_at: datetime
    content_preview: str = ""
    source_kind: str = "news"  # lab, news, release, paper
    source_priority: int = 5  # lower wins when two sources cover the same story


@dataclass
class BriefingItem:
    what_happened: str
    why_it_matters: str
    url: str
    source_name: str


@dataclass
class FetchReport:
    articles: list
    failures: list
    successes: int
