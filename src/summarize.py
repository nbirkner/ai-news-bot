import json
import sys
import requests
from dataclasses import replace
from src.models import Article

TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"


def _call_together(prompt: str, api_key: str, model: str) -> str:
    resp = requests.post(
        TOGETHER_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 300,
            "temperature": 0.3,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def summarize_breaking(article: Article, api_key: str, model: str) -> tuple:
    prompt = f"""You are a concise AI news analyst for Together AI, an AI infrastructure and inference company.

Article title: {article.title}
Source: {article.source_name}
Content: {article.content_preview}

Write two things:
1. SUMMARY: A 2-3 sentence summary of what happened. Facts only. No hype.
2. WHY_IT_MATTERS: One sentence on why this specifically matters for an AI infrastructure company like Together AI.

Format your response exactly as:
SUMMARY: <your summary here>
WHY_IT_MATTERS: <your one sentence here>

No em dashes. No corporate jargon. Be direct."""

    try:
        response = _call_together(prompt, api_key, model)
        summary = ""
        why = ""
        for line in response.splitlines():
            if line.startswith("SUMMARY:"):
                summary = line.replace("SUMMARY:", "").strip()
            elif line.startswith("WHY_IT_MATTERS:"):
                why = line.replace("WHY_IT_MATTERS:", "").strip()
        return summary or article.content_preview[:200], why or "Relevant to Together AI's market."
    except Exception as e:
        print(f"Warning: summarize_breaking failed for {article.url}: {e}", file=sys.stderr)
        return article.content_preview[:200], "See article for context."


def summarize_digest(articles: list, api_key: str, model: str) -> list:
    if not articles:
        return []

    articles_data = [
        {"index": i, "title": a.title, "source": a.source_name, "content": a.content_preview[:300]}
        for i, a in enumerate(articles)
    ]

    prompt = f"""You are summarizing AI news for Together AI's internal Slack channel.
Together AI is an AI infrastructure company offering inference, fine-tuning, and GPU clusters.
Competitors include: OpenAI, Anthropic, Google, Groq, Fireworks, Modal, Replicate, Cerebras.

For each article, write a tight one-sentence summary. If the article is directly relevant to
AI infrastructure, inference, GPU market, model releases, or competitor moves, mark it as relevant.

Articles (JSON):
{json.dumps(articles_data, indent=2)}

Return a JSON array only, no other text:
[{{"index": 0, "summary": "...", "relevant_to_together": true}}, ...]"""

    try:
        response = _call_together(prompt, api_key, model)
        start = response.find("[")
        end = response.rfind("]") + 1
        parsed = json.loads(response[start:end])

        result = []
        for i, article in enumerate(articles):
            matched = next((p for p in parsed if p["index"] == i), None)
            if matched:
                updated = replace(
                    article,
                    summary=matched.get("summary", article.content_preview[:120]),
                    relevant_to_together=matched.get("relevant_to_together", False),
                )
            else:
                updated = replace(article, summary=article.content_preview[:120])
            result.append(updated)
        return result

    except Exception as e:
        print(f"Warning: summarize_digest failed: {e}", file=sys.stderr)
        return [replace(a, summary=a.content_preview[:120]) for a in articles]
