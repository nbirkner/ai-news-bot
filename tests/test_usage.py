from src.pricing import exa_cost_usd, llm_cost_usd
from src.usage import format_usage, record_llm_call, reset_usage


def test_glm_and_exa_prices_match_the_public_pages():
    # Together GLM-5.3: $1.40 / 1M input, $4.40 / 1M output.
    assert llm_cost_usd(1_000_000, 1_000_000) == 1.40 + 4.40
    # Exa /search auto is $7 / 1k. /contents text is $1 / 1k pages.
    assert exa_cost_usd(1000, 1000) == 7 + 1
    assert exa_cost_usd(1, 0) == 0.007


def test_usage_log_lists_each_call_and_a_13_run_month(monkeypatch):
    reset_usage()
    record_llm_call("zai-org/GLM-5.3", 1000, 500)
    record_llm_call("zai-org/GLM-5.3", 20, 10)
    text = format_usage(model="zai-org/GLM-5.3", exa_search_calls=8, exa_content_pages=4)
    assert "call_1_input_tokens: 1000" in text
    assert "call_1_output_tokens: 500" in text
    assert "call_2_input_tokens: 20" in text
    assert "total_input_tokens: 1020" in text
    assert "total_output_tokens: 510" in text
    assert "model: zai-org/GLM-5.3" in text
    assert "https://www.together.ai/pricing" in text
    assert "1.40" in text and "4.40" in text
    assert "https://exa.ai/docs/admin/pricing" in text
    assert "exa_free_credit_usd_per_month: 10.00" in text
    assert "llm_monthly_usd_at_13_runs:" in text
    assert "exa_monthly_usd_at_13_runs:" in text
    assert "sk-" not in text
    reset_usage()


def test_call_together_records_usage_and_not_the_key(monkeypatch):
    reset_usage()
    captured = {}

    class Response:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": "{\"items\": []}"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            }

    def post(url, headers=None, json=None, timeout=None):
        captured["auth"] = headers["Authorization"]
        return Response()

    monkeypatch.setattr("src.summarize.requests.post", post)
    from src.summarize import _call_together
    text = _call_together("hello", "sk-super-secret", "zai-org/GLM-5.3")
    assert "items" in text
    assert captured["auth"] == "Bearer sk-super-secret"
    log = format_usage(model="zai-org/GLM-5.3")
    assert "call_1_input_tokens: 12" in log
    assert "call_1_output_tokens: 4" in log
    assert "sk-super-secret" not in log
    reset_usage()
