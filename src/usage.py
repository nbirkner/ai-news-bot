"""Token totals for one process. _call_together records each response."""

from src.pricing import (
    EXA_CONTENTS_PAGE_USD,
    EXA_FREE_CREDIT_USD,
    EXA_PRICING_URL,
    EXA_SEARCH_USD,
    RUNS_PER_MONTH,
    TOGETHER_INPUT_PER_MILLION,
    TOGETHER_OUTPUT_PER_MILLION,
    TOGETHER_PRICING_URL,
    exa_cost_usd,
    llm_cost_usd,
)


class LlmCall:
    def __init__(self, model: str, input_tokens: int, output_tokens: int):
        self.model = model
        self.input_tokens = int(input_tokens)
        self.output_tokens = int(output_tokens)


_calls: list = []


def reset_usage() -> None:
    _calls.clear()


def record_llm_call(model: str, input_tokens: int, output_tokens: int) -> None:
    _calls.append(LlmCall(model, input_tokens, output_tokens))


def llm_calls() -> list:
    return list(_calls)


def format_usage(
    *,
    model: str,
    exa_search_calls: int = 0,
    exa_content_pages: int = 0,
    runs_per_month: int = RUNS_PER_MONTH,
) -> str:
    """Plain log. Never includes API keys or other secrets."""
    calls = llm_calls()
    total_in = sum(call.input_tokens for call in calls)
    total_out = sum(call.output_tokens for call in calls)
    cost = llm_cost_usd(total_in, total_out)
    exa = exa_cost_usd(exa_search_calls, exa_content_pages)
    monthly_llm = cost * runs_per_month
    monthly_exa = exa * runs_per_month
    lines = [
        f"model: {model}",
        f"together_pricing: {TOGETHER_PRICING_URL}",
        f"together_input_usd_per_million: {TOGETHER_INPUT_PER_MILLION:.2f}",
        f"together_output_usd_per_million: {TOGETHER_OUTPUT_PER_MILLION:.2f}",
        f"llm_calls: {len(calls)}",
    ]
    if not calls:
        lines.append("llm_note: no Together call in this run")
    for index, call in enumerate(calls, start=1):
        lines.append(f"call_{index}_model: {call.model}")
        lines.append(f"call_{index}_input_tokens: {call.input_tokens}")
        lines.append(f"call_{index}_output_tokens: {call.output_tokens}")
    lines.extend([
        f"total_input_tokens: {total_in}",
        f"total_output_tokens: {total_out}",
        f"llm_cost_usd: {cost:.6f}",
        f"llm_monthly_usd_at_{runs_per_month}_runs: {monthly_llm:.6f}",
        f"exa_pricing: {EXA_PRICING_URL}",
        f"exa_search_auto_usd_per_call: {EXA_SEARCH_USD:.4f}",
        f"exa_contents_usd_per_page: {EXA_CONTENTS_PAGE_USD:.4f}",
        f"exa_search_calls: {int(exa_search_calls)}",
        f"exa_content_pages: {int(exa_content_pages)}",
        f"exa_cost_usd: {exa:.6f}",
        f"exa_monthly_usd_at_{runs_per_month}_runs: {monthly_exa:.6f}",
        f"exa_free_credit_usd_per_month: {EXA_FREE_CREDIT_USD:.2f}",
        f"exa_monthly_after_free_credit_usd: {max(0.0, monthly_exa - EXA_FREE_CREDIT_USD):.6f}",
        "sends: none",
    ])
    return "\n".join(lines) + "\n"
