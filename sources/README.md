# Weekly sources

The Monday newsletter (Monday 9 AM PT) reads one committed file before it reads RSS:

```text
sources/<YYYY-MM-DD>.json
```

`<YYYY-MM-DD>` is the window end on the Pacific calendar (`America/Los_Angeles`). `--window-end 2026-10-19` reads `sources/2026-10-19.json`, including when that instant is Monday 9 AM PT.

Mon/Wed/Fri briefing runs do not read this directory. They use RSS only.

## Schema

The file is a JSON list. Each element is an object:

| Field | Required | Type |
| --- | --- | --- |
| `title` | yes | string, non-empty after trimming whitespace |
| `url` | yes | string, absolute `http` or `https` URL with a host |
| `source` | yes | string, non-empty after trimming whitespace. The publication name. |
| `published` | yes | string, ISO-8601 date (`YYYY-MM-DD`) or date-time (`YYYY-MM-DDThh:mm:ssZ`, or the same form with a numeric offset such as `-07:00`) |
| `summary` | yes | string, non-empty after trimming whitespace. Public facts the writer may use. |
| `reflection` | no | string. A short note on the story. Omit the field when there is none. An empty string is allowed. |

Any other field is ignored. `reflection` must be a string when it is present. `null` is invalid.

If the file is missing, is not a JSON list, or any item fails these checks, the whole file is rejected and that run uses RSS only. An empty list (`[]`) is valid and adds no stories.

A valid file is merged with RSS. Those items are candidate stories. The model still ranks and writes the issue. Exa is not called unless `EXA_ENABLED=1`. The per-run cap is `EXA_MAX_CALLS` (default 6).

## Example

```json
[
  {
    "title": "Anthropic publishes a workforce study",
    "url": "https://www.anthropic.com/news/workforce-study",
    "source": "Anthropic",
    "published": "2026-10-18T16:00:00Z",
    "summary": "Anthropic published a workforce study of how coding agents change software teams. The study covers 12 companies.",
    "reflection": "The lab post is the primary source."
  }
]
```
