from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Article:
    url: str
    title: str
    source_name: str
    source_domain: str
    published_at: datetime
    content_preview: str
    is_breaking: bool = False
    summary: Optional[str] = None
    why_it_matters: Optional[str] = None
    relevant_to_together: bool = False
