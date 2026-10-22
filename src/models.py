from dataclasses import dataclass, field
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
    full_text: str = ""
    details: str = ""
    reflection: str = ""  # optional note from the weekly sources file


@dataclass
class BriefingItem:
    what_happened: str
    why_it_matters: str
    url: str
    source_name: str
    also: tuple = ()  # extra (url, label) citations
    body: str = ""  # 3-5 factual sentences. Empty only on the legacy one-line path.


@dataclass
class Issue:
    """One public briefing. Slack, MDX, and email are renders of this object."""

    title: str
    date: str  # YYYY-MM-DD, the scheduled window end
    summary: str
    tags: list
    items: list
    since: datetime | None = None
    until: datetime | None = None
    also_in_window: tuple = ()  # titles that were in the window and did not make the list


@dataclass
class FetchReport:
    articles: list
    failures: list
    successes: int
    prior: list = field(default_factory=list)
