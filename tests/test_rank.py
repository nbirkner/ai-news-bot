from datetime import datetime, timezone

from src.models import Article
from src.rank import hard_noise, is_mega_order, score_article, select_items


def article(title, *, kind="news", url="https://example.com/a", source="Example", hours=10):
    return Article(
        url=url,
        title=title,
        source_name=source,
        source_domain="example.com",
        published_at=datetime(2026, 10, 14, hours, tzinfo=timezone.utc),
        content_preview=title,
        source_kind=kind,
        source_priority=1 if kind == "lab" else 3,
    )


def test_model_launch_scores_above_the_bar():
    assert score_article(article("Gemini 4 Argon: Built to solve your hardest problems", kind="lab", source="Google")) >= 6


def test_lawsuit_scores_above_the_bar():
    assert score_article(article("Eight US states sue OpenAI, claiming ChatGPT harms children")) >= 6


def test_customer_story_stays_under_the_bar():
    assert score_article(article("SentinelOne cuts malware analysis from weeks to hours", kind="lab", source="OpenAI")) < 6


def test_release_candidate_stays_under_the_bar():
    assert score_article(article("vLLM v0.32.0rc0: report host pool in logs", kind="release", source="vLLM")) < 6


def test_market_blurb_stays_under_the_bar():
    assert score_article(article("Red hot CPI report fuels AI rout")) < 6


def test_a_ban_and_a_mega_round_clear_the_bar():
    assert score_article(article("U.S. bans DeepSeek from government agency systems")) >= 6
    assert score_article(article("OpenAI races to raise $30 billion ahead of an Anthropic IPO")) >= 6
    assert score_article(article("Anthropic in talks with Nscale over a multibillion-dollar deal")) >= 6


def test_a_hacking_headline_stays_under_the_bar():
    assert score_article(article("AI agents are hacking John Deere equipment")) < 6


def test_a_lab_loss_and_a_nscale_deal_outrank_llm_d():
    loss = article("Anthropic logged net loss of over $80 billion in first half of 2026, WSJ reports")
    nscale = article("Nvidia-backed Nscale, Anthropic in talks for new $45 billion AI cloud deal")
    chips = article("ByteDance, Alibaba and Tencent order more than $35 billion of Nvidia chips")
    llmd = article("llm-d v1.0 brings distributed inference to Kubernetes", kind="release", source="llm-d")
    assert score_article(loss) > score_article(llmd)
    assert score_article(nscale) > score_article(llmd)
    assert score_article(chips) > score_article(llmd)
    assert is_mega_order(chips)
    assert not is_mega_order(nscale)


def test_a_frontier_round_outranks_a_generic_financing():
    lab_round = article("OpenAI seeks to accelerate fundraising ahead of Anthropic IPO, sources say")
    generic = article("Otter.ai raises $320 million in non-equity financing to expand enterprise business")
    loss = article("Anthropic logged net loss of over $80 billion in first half of 2026, WSJ reports")
    scrutiny = article("Governments, regulators increase scrutiny of China's DeepSeek")
    assert score_article(lab_round) > score_article(generic)
    assert score_article(loss) > score_article(scrutiny)


def test_a_generic_enterprise_deal_is_not_an_ai_story():
    braze = article("Salesforce deepens AI bets with a $5.66 billion Braze deal")
    assert hard_noise(braze)
    orchestra = article("OpenAI acquires Orchestra Bio")
    assert not hard_noise(orchestra)


def test_a_tax_commentary_is_not_a_story():
    assert hard_noise(article("As billionaires fight a tax hike, a cofounder says he will pay $8 billion"))


def test_office_opening_stays_under_the_bar():
    assert score_article(article("Anthropic opens Singapore office and signs MOU", kind="lab", source="Anthropic")) < 6


def test_select_keeps_material_items_and_drops_noise():
    items = [
        article("Gemini 4 Argon: Built to solve your hardest problems", kind="lab", source="Google", url="https://blog.google/g", hours=16),
        article("Eight US states sue OpenAI, claiming ChatGPT harms children", url="https://reuters.com/s", source="Reuters", hours=18),
        article("SentinelOne cuts malware analysis from weeks to hours", kind="lab", source="OpenAI", url="https://openai.com/s", hours=12),
        article("vLLM v0.32.0rc0: report host pool", kind="release", source="vLLM", url="https://github.com/v", hours=11),
        article("The AI race is back on", url="https://theverge.com/r", source="The Verge", hours=15),
    ]
    selected = select_items(items)
    titles = [item.title for item in selected]
    assert "Gemini 4 Argon: Built to solve your hardest problems" in titles
    assert "Eight US states sue OpenAI, claiming ChatGPT harms children" in titles
    assert all("SentinelOne" not in title for title in titles)
    assert all("rc0" not in title for title in titles)
    assert all("AI race" not in title for title in titles)


def test_select_drops_a_wire_recap_of_a_lab_post():
    lab = article(
        "Preparing for the 2026 US midterms",
        kind="lab",
        source="Anthropic",
        url="https://www.anthropic.com/news/2026-us-midterms",
        hours=12,
    )
    lab.source_priority = 1
    wire = article(
        "Anthropic to add US voting information to Claude before midterms",
        url="https://www.reuters.com/midterms",
        source="Reuters",
        hours=18,
    )
    wire.source_priority = 2
    selected = select_items([lab, wire])
    assert [item.source_name for item in selected] == ["Anthropic"]


def test_select_is_quiet_when_nothing_is_material():
    items = [
        article("Amp puts AI agents to work beyond code", kind="lab", source="OpenAI"),
        article("YouTube sticks new labels on songs", source="The Verge"),
    ]
    assert select_items(items) == []


def test_one_item_per_model_signature_keeps_the_earlier_tie():
    later = article(
        "Developing Gemini 4 safely",
        kind="lab",
        source="DeepMind",
        url="https://deepmind.google/safety",
        hours=19,
    )
    earlier = article(
        "Gemini 4 Argon: Built to solve your hardest problems",
        kind="lab",
        source="Google",
        url="https://blog.google/gemini",
        hours=16,
    )
    selected = select_items([later, earlier], max_per_signature=1)
    assert len(selected) == 1
    assert selected[0].url == "https://blog.google/gemini"


def test_strong_paper_can_be_included_and_weak_paper_cannot():
    strong = article(
        "Jailbreak attacks on LLM inference servers",
        kind="paper",
        source="arXiv",
        url="https://arxiv.org/abs/1",
    )
    weak = article(
        "A survey of agent memory designs",
        kind="paper",
        source="arXiv",
        url="https://arxiv.org/abs/2",
    )
    selected = select_items([strong, weak])
    urls = [item.url for item in selected]
    assert "https://arxiv.org/abs/1" in urls
    assert "https://arxiv.org/abs/2" not in urls
