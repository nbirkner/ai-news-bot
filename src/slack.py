import json
from datetime import datetime, timezone
from typing import Optional
import requests
from src.models import Article


def format_breaking(article: Article) -> dict:
    lines = [
        "🚨 BREAKING AI NEWS",
        article.title,
        "",
        article.summary or article.content_preview[:200],
    ]
    if article.why_it_matters:
        lines += ["", f"Why it matters for Together: {article.why_it_matters}"]
    lines += ["", f"{article.url}  —  {article.source_name}"]
    return {"text": "\n".join(lines)}


def format_digest(articles: list) -> Optional[dict]:
    if not articles:
        return None

    today = datetime.now(timezone.utc).strftime("%A, %B %-d")
    lines = [f"📰 AI News Digest — {today}", ""]

    together_articles = [a for a in articles if a.relevant_to_together]
    industry_articles = [a for a in articles if not a.relevant_to_together]

    if together_articles:
        lines.append("RELEVANT TO TOGETHER AI")
        for a in together_articles:
            bullet = f"• {a.title}: {a.summary or a.content_preview[:120]}"
            lines.append(bullet)
        lines.append("")

    if industry_articles:
        lines.append("INDUSTRY")
        for a in industry_articles:
            bullet = f"• {a.title}: {a.summary or a.content_preview[:120]}"
            lines.append(bullet)
        lines.append("")

    source_names = sorted({a.source_name for a in articles})
    lines.append(f"Sources: {', '.join(source_names)}")

    return {"text": "\n".join(lines)}


def post_to_slack(payload: dict, webhook_url: str) -> None:
    resp = requests.post(
        webhook_url,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
