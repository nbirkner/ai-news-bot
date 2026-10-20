from datetime import datetime, timezone

from src.fetch import (
    articles_from_feed,
    enrich_previews,
    in_window,
    parse_anthropic_html,
    parse_blog_index,
    parse_federal_register,
    parse_meta_html,
    parse_reuters_sitemap,
)
from src.models import Article


OPENAI = {
    "name": "OpenAI",
    "domain": "openai.com",
    "kind": "lab",
    "priority": 1,
}
AXIOS = {
    "name": "Axios",
    "domain": "axios.com",
    "kind": "news",
    "priority": 3,
    "require_ai": True,
}
REUTERS = {
    "name": "Reuters",
    "domain": "reuters.com",
    "kind": "news",
    "priority": 2,
}
ANTHROPIC = {
    "name": "Anthropic",
    "url": "https://www.anthropic.com/news",
    "domain": "anthropic.com",
    "kind": "lab",
    "priority": 1,
}
META = {
    "name": "Meta AI",
    "url": "https://ai.meta.com/blog/",
    "domain": "ai.meta.com",
    "kind": "lab",
    "priority": 1,
}


def test_rss_parser_reads_title_link_and_date():
    raw = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item>
        <title>Gemini 4 Argon: Built to solve your hardest problems</title>
        <link>https://blog.google/example</link>
        <pubDate>Tue, 13 Oct 2026 16:00:00 +0000</pubDate>
        <description>Google published Gemini 4 Argon.</description>
      </item>
    </channel></rss>"""
    articles = articles_from_feed(OPENAI, raw)
    assert len(articles) == 1
    assert articles[0].title.startswith("Gemini 4 Argon")
    assert articles[0].published_at == datetime(2026, 10, 13, 16, 0, tzinfo=timezone.utc)
    assert "Gemini 4 Argon" in articles[0].content_preview


def test_require_ai_drops_unrelated_items():
    raw = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item>
        <title>AI tools are aiding would-be spies</title>
        <link>https://www.axios.com/ai</link>
        <pubDate>Wed, 14 Oct 2026 19:30:05 +0000</pubDate>
        <description>Insiders can misuse access.</description>
      </item>
      <item>
        <title>ICE says no to automatic body-cams</title>
        <link>https://www.axios.com/ice</link>
        <pubDate>Wed, 14 Oct 2026 17:46:12 +0000</pubDate>
        <description>A policy fight over cameras.</description>
      </item>
    </channel></rss>"""
    articles = articles_from_feed(AXIOS, raw)
    assert [item.url for item in articles] == ["https://www.axios.com/ai"]


def test_reuters_keeps_english_ai_and_drops_the_rest():
    raw = b"""<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
      <url>
        <loc>https://www.reuters.com/world/eight-us-states-sue-openai-2026-10-14/</loc>
        <news:news>
          <news:publication><news:name>Reuters</news:name><news:language>en</news:language></news:publication>
          <news:publication_date>2026-10-14T18:46:44Z</news:publication_date>
          <news:title>Eight US states sue OpenAI, claiming ChatGPT harms children</news:title>
        </news:news>
      </url>
      <url>
        <loc>https://www.reuters.com/latam/negocio/example-2026-10-14/</loc>
        <news:news>
          <news:publication><news:name>Reuters</news:name><news:language>en</news:language></news:publication>
          <news:publication_date>2026-10-14T19:39:07Z</news:publication_date>
          <news:title>Ocho estados de EEUU demandan a OpenAI, alegando que ChatGPT perjudica a los ninos</news:title>
        </news:news>
      </url>
      <url>
        <loc>https://www.reuters.com/sports/olympic-2026-10-14/</loc>
        <news:news>
          <news:publication><news:name>Reuters</news:name><news:language>en</news:language></news:publication>
          <news:publication_date>2026-10-14T19:53:33Z</news:publication_date>
          <news:title>Olympic champion withdraws from NYC Marathon</news:title>
        </news:news>
      </url>
    </urlset>"""
    articles = parse_reuters_sitemap(REUTERS, raw)
    assert [item.url for item in articles] == [
        "https://www.reuters.com/world/eight-us-states-sue-openai-2026-10-14/"
    ]


def test_anthropic_parser_reads_title_and_date():
    html = """
    <a href="/news/2026-us-midterms">
      <time>Oct 14, 2026</time>
      <span class="PublicationList__title">Preparing for the 2026 US midterms</span>
    </a>
    <a href="/news">News</a>
    """
    articles = parse_anthropic_html(ANTHROPIC, html)
    assert len(articles) == 1
    assert articles[0].title == "Preparing for the 2026 US midterms"
    assert articles[0].url == "https://www.anthropic.com/news/2026-us-midterms"
    assert articles[0].published_at == datetime(2026, 10, 14, tzinfo=timezone.utc)


def test_meta_parser_reads_nearby_date_and_skips_featured_label():
    html = """
    <div>
      <div>Research</div>
      <div><a href="https://ai.meta.com/blog/introducing-muse-spark/">Introducing Muse Spark 1.1 for developers today</a></div>
      <div>Jul 9, 2026</div>
    </div>
    <a href="https://ai.meta.com/blog/introducing-muse-spark/">FEATURED</a>
    """
    articles = parse_meta_html(META, html)
    assert len(articles) == 1
    assert articles[0].title.startswith("Introducing Muse Spark")
    assert articles[0].published_at == datetime(2026, 7, 9, tzinfo=timezone.utc)


def test_in_window():
    since = datetime(2026, 10, 12, 15, tzinfo=timezone.utc)
    now = datetime(2026, 10, 14, 15, tzinfo=timezone.utc)
    assert in_window(datetime(2026, 10, 13, tzinfo=timezone.utc), since, now)
    assert not in_window(datetime(2026, 10, 1, tzinfo=timezone.utc), since, now)


def test_monday_morning_pt_is_outside_and_tuesday_morning_is_inside():
    """Mon Oct 19 8:00 AM PT through Tue Oct 20 11:00 AM PT.

    Early Monday is before the open. The same clock time on Tuesday is inside.
    """
    since = datetime(2026, 10, 19, 15, tzinfo=timezone.utc)
    until = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)
    monday_306am = datetime(2026, 10, 19, 10, 6, tzinfo=timezone.utc)
    monday_5am = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    monday_6am = datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc)
    tuesday_306am = datetime(2026, 10, 20, 10, 6, tzinfo=timezone.utc)
    tuesday_5am = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    tuesday_6am = datetime(2026, 10, 20, 13, 0, tzinfo=timezone.utc)
    assert not in_window(monday_306am, since, until)
    assert not in_window(monday_5am, since, until)
    assert not in_window(monday_6am, since, until)
    assert in_window(tuesday_306am, since, until)
    assert in_window(tuesday_5am, since, until)
    assert in_window(tuesday_6am, since, until)
    assert in_window(since, since, until)
    assert in_window(until, since, until)


def test_enrich_fills_a_thin_preview():
    item = Article(
        url="https://www.anthropic.com/news/example",
        title="Preparing for the 2026 US midterms",
        source_name="Anthropic",
        source_domain="anthropic.com",
        published_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        content_preview="Preparing for the 2026 US midterms",
        source_kind="lab",
    )
    html = (
        '<html><head><meta property="og:description" '
        'content="Anthropic describes how it is preparing its models for the 2026 US midterms.">'
        "</head></html>"
    )
    enrich_previews([item], getter=lambda url: html)
    assert "preparing its models" in item.content_preview


def test_federal_register_keeps_an_ai_order_and_drops_a_lab_accreditation():
    source = {
        "name": "Federal Register",
        "domain": "federalregister.gov",
        "kind": "news",
        "priority": 1,
    }
    payload = {"results": [
        {
            "title": "Notice of Order Regarding Exclusion and Removal of DeepSeek Products",
            "abstract": "The Department of Homeland Security is publishing notice of an order removing DeepSeek products from federal civilian agency systems.",
            "html_url": "https://www.federalregister.gov/documents/2026/10/20/example",
            "publication_date": "2026-10-20",
        },
        {
            "title": "Accreditation of SGS North America as a Commercial Laboratory",
            "abstract": "Notice of accreditation for a gauging laboratory.",
            "html_url": "https://www.federalregister.gov/documents/2026/10/20/other",
            "publication_date": "2026-10-20",
        },
    ]}
    articles = parse_federal_register(source, payload)
    assert len(articles) == 1
    assert "DeepSeek" in articles[0].title
    assert articles[0].published_at == datetime(2026, 10, 20, 16, tzinfo=timezone.utc)
    assert "federal civilian" in articles[0].full_text


def test_blog_index_reads_the_post_date_and_treats_midnight_as_morning():
    source = {
        "name": "MiniMax",
        "url": "https://www.minimax.io/blog",
        "domain": "minimax.io",
        "kind": "lab",
        "priority": 1,
        "max_results": 2,
    }
    index = '<a href="/blog/minimax-m31f">MiniMax M3.1 Flash</a><a href="/blog">Blog</a>'
    page = """
    <html><head>
      <meta property="og:title" content="MiniMax M3.1 Flash: agent model | MiniMax">
      <meta property="og:description" content="MiniMax M3.1 Flash is a 428B model with 23B active parameters.">
      <script type="application/ld+json">{"datePublished":"2026-10-19T00:00:00.000Z"}</script>
    </head></html>
    """
    articles = parse_blog_index(source, index, getter=lambda url: page)
    assert len(articles) == 1
    assert articles[0].title.startswith("MiniMax M3.1 Flash")
    assert articles[0].published_at == datetime(2026, 10, 19, 15, tzinfo=timezone.utc)
    assert "428B" in articles[0].content_preview
