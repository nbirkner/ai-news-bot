"""Match a caller-supplied headline list against discovered articles."""
from src.clean import tokens


def load_expect_file(path: str) -> list:
    lines = []
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            lines.append(line)
    return lines


def _hit(needle: str, haystack: set) -> bool:
    """Match a token or a short suffix such as raise/raises. Not open/openai."""
    for word in haystack:
        if word == needle:
            return True
        if word.startswith(needle) and len(word) - len(needle) <= 4:
            return True
    return False


def headline_matches(expected: str, article) -> bool:
    """Match on the headline and source name.

    Body text is not used. A comparison piece that only mentions the labs
    in a paragraph would otherwise look like the lab's own announcement.
    """
    needles = [word for word in tokens(expected) if len(word) >= 5]
    if not needles:
        needles = tokens(expected)
    if not needles:
        return False
    haystack = set(tokens(f"{article.title or ''} {article.source_name or ''}"))
    hits = sum(1 for needle in needles if _hit(needle, haystack))
    return hits / len(needles) >= 0.6


def coverage_report(expected: list, pool: list, selected: list | None = None) -> str:
    """Plain text report. selected is optional (discovery-only runs)."""
    selected = selected or []
    selected_ids = {id(article) for article in selected}
    lines = ["Expected headlines:"]
    found = 0
    for headline in expected:
        match = next((article for article in pool if headline_matches(headline, article)), None)
        if match is None:
            lines.append(f"  NOT FOUND: {headline}")
            continue
        found += 1
        if selected and id(match) in selected_ids:
            status = "FOUND, ranked"
        elif selected:
            status = "FOUND, not ranked"
        else:
            status = "FOUND"
        lines.append(f"  {status}: {headline}")
        lines.append(f"    -> {match.source_name}: {match.title}")
    lines.append(f"Coverage: {found} of {len(expected)} expected headlines found in the candidate pool.")
    return "\n".join(lines)
