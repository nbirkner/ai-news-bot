"""Published list prices used for the dry-run cost log.

Together, GLM-5.3 serverless, checked 2026-10-19:
https://www.together.ai/pricing
https://docs.together.ai/docs/glm-5.3-quickstart
Input $1.40 / 1M tokens, output $4.40 / 1M tokens.
Cached input ($0.26 / 1M) is not billed here; the API usage block we log
is prompt_tokens and completion_tokens.

Exa, checked 2026-10-19:
https://exa.ai/docs/admin/pricing
/search type "auto" is $7 / 1,000 requests ($0.007 each), up to 10 results.
/contents text is $1 / 1,000 pages ($0.001 each).
New accounts get $10 of credits every month.
"""

TOGETHER_INPUT_PER_MILLION = 1.40
TOGETHER_OUTPUT_PER_MILLION = 4.40
TOGETHER_PRICING_URL = "https://www.together.ai/pricing"
TOGETHER_MODEL = "zai-org/GLM-5.3"

# This bot sends type "auto" and numResults 5, so the base search price applies.
EXA_SEARCH_USD = 7.0 / 1000
EXA_CONTENTS_PAGE_USD = 1.0 / 1000
EXA_PRICING_URL = "https://exa.ai/docs/admin/pricing"
EXA_FREE_CREDIT_USD = 10.0
RUNS_PER_MONTH = 13


def llm_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (
        (int(input_tokens) / 1_000_000) * TOGETHER_INPUT_PER_MILLION
        + (int(output_tokens) / 1_000_000) * TOGETHER_OUTPUT_PER_MILLION
    )


def exa_cost_usd(search_calls: int, content_pages: int) -> float:
    return int(search_calls) * EXA_SEARCH_USD + int(content_pages) * EXA_CONTENTS_PAGE_USD
