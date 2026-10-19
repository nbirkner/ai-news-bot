"""Commit one MDX issue to nbirkner/site. The token never goes into a log line."""
import base64
import os
import sys

import requests

DEFAULT_REPO = "nbirkner/site"
DEFAULT_BRANCH = "main"


def _redact(text: str, secret: str | None) -> str:
    if not text:
        return ""
    if secret:
        text = text.replace(secret, "[redacted]")
    return text[:300]


def _repo() -> str:
    repo = (os.environ.get("SITE_REPO") or DEFAULT_REPO).strip()
    if repo.count("/") != 1 or not all(repo.split("/")):
        raise RuntimeError("SITE_REPO must look like owner/name")
    return repo


def publish_mdx(mdx: str, *, path: str, token=None, repo=None, branch=None, request=None) -> dict:
    """Create src/content/newsletter/<date>.mdx. No token means skip, not fail."""
    if token is None:
        token = os.environ.get("SITE_GITHUB_TOKEN") or None
    if isinstance(token, str):
        token = token.strip() or None
    if not token:
        print("SITE_GITHUB_TOKEN is not set. Skipping the site archive.", file=sys.stderr)
        return {"skipped": True, "reason": "SITE_GITHUB_TOKEN is not set"}

    repo = (repo or _repo()).strip()
    branch = (branch or os.environ.get("SITE_BRANCH") or DEFAULT_BRANCH).strip()
    request = request or requests.request
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-news-bot",
    }
    existing = request("GET", url, headers=headers, params={"ref": branch}, timeout=20)
    status = getattr(existing, "status_code", 0)
    if status == 200:
        payload = existing.json()
        encoded = (payload.get("content") or "").encode()
        current = base64.b64decode(encoded).decode()
        if current == mdx:
            print(f"Site archive already has {path}.", file=sys.stderr)
            return {"skipped": False, "unchanged": True, "path": path}
        raise RuntimeError(f"{path} already exists with different content")
    if status != 404:
        detail = _redact(getattr(existing, "text", "") or "", token)
        raise RuntimeError(f"GitHub contents lookup returned {status}: {detail}")

    body = {
        "message": f"Publish AI news {path.rsplit('/', 1)[-1].removesuffix('.mdx')}",
        "content": base64.b64encode(mdx.encode()).decode(),
        "branch": branch,
    }
    created = request("PUT", url, headers=headers, json=body, timeout=20)
    created_status = getattr(created, "status_code", 0)
    if created_status >= 400:
        detail = _redact(getattr(created, "text", "") or "", token)
        raise RuntimeError(f"GitHub contents write returned {created_status}: {detail}")
    print(f"Committed {path} to {repo}.", file=sys.stderr)
    return {"skipped": False, "unchanged": False, "path": path, "status": created_status}
