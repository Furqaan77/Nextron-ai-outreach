"""What a Claude request costs (``nextron.adapters.ai.pricing``, Story 2.7): the Price row of
the matrix, the cache rates, the long-prompt card and an unlisted model."""

from typing import Final

import pytest

from nextron.adapters.ai.pricing import FALLBACK, PRICES, RateCard, cost_micros, rate_card

HAIKU: Final = "claude-haiku-5-5"
OPUS: Final = "claude-opus-5-5"


def cost(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_5m_tokens: int = 0,
    cache_write_1h_tokens: int = 0,
) -> int:
    return cost_micros(
        model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_5m_tokens=cache_write_5m_tokens,
        cache_write_1h_tokens=cache_write_1h_tokens,
    )


def test_the_rate_cards_are_the_specified_ones() -> None:
    assert PRICES[HAIKU].standard == RateCard(100_000, 500_000, 10_000, 125_000, 200_000)
    assert PRICES[HAIKU].long == RateCard(500_000, 2_500_000, 50_000, 625_000, 1_000_000)
    assert PRICES[HAIKU].long_after == 100_000
    assert PRICES[OPUS].standard == RateCard(4_000_000, 20_000_000, 200_000, 5_000_000, 8_000_000)
    assert PRICES[OPUS].long is None
    assert set(PRICES) == {HAIKU, OPUS}


def test_the_price_row_of_the_matrix() -> None:
    assert cost(HAIKU, input_tokens=10_000, output_tokens=1_000) == 1_500
    assert cost(HAIKU, input_tokens=1) == 1  # a fraction of a micro-dollar rounds up
    assert cost(OPUS, input_tokens=1_000_000) == 4_000_000


def test_a_prompt_over_100k_tokens_takes_haikus_long_card() -> None:
    assert rate_card(HAIKU, 100_000) is PRICES[HAIKU].standard
    assert rate_card(HAIKU, 100_001) is PRICES[HAIKU].long
    assert cost(HAIKU, input_tokens=100_000) == 10_000
    assert cost(HAIKU, input_tokens=100_001) == 50_001  # 100,001 x $0.50/M, rounded up
    assert cost(HAIKU, input_tokens=100_001, output_tokens=1_000) == 50_001 + 2_500


def test_the_prompt_counts_cache_reads_and_writes() -> None:
    # 60,000 input + 30,000 read + 10,001 written = 100,001: the long card
    long = cost(
        HAIKU, input_tokens=60_000, cache_read_tokens=30_000, cache_write_5m_tokens=10_001
    )
    standard = cost(
        HAIKU, input_tokens=60_000, cache_read_tokens=30_000, cache_write_5m_tokens=10_000
    )

    assert long == -(-(60_000 * 500_000 + 30_000 * 50_000 + 10_001 * 625_000) // 10**6)
    assert standard == 6_000 + 300 + 1_250
    assert rate_card(HAIKU, 60_000 + 30_000 + 5_000 + 5_001) is PRICES[HAIKU].long


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        ({"cache_read_tokens": 100_000}, 1_000),  # $0.01/M: 0.1x input
        ({"cache_write_5m_tokens": 100_000}, 12_500),  # $0.125/M: 1.25x input
        ({"cache_write_1h_tokens": 100_000}, 20_000),  # $0.20/M: 2x input
        ({"output_tokens": 1_000_000}, 500_000),  # output isn't prompt: the standard card
    ],
)
def test_the_cache_and_output_rates_on_haiku(tokens: dict[str, int], expected: int) -> None:
    assert cost(HAIKU, **tokens) == expected


def test_the_opus_cache_rates() -> None:
    assert cost(OPUS, cache_read_tokens=1_000_000) == 200_000
    assert cost(OPUS, cache_write_5m_tokens=1_000_000) == 5_000_000
    assert cost(OPUS, cache_write_1h_tokens=1_000_000) == 8_000_000
    assert cost(OPUS, output_tokens=1_000_000) == 20_000_000


def test_an_unlisted_model_is_priced_at_the_dearest_card_field_by_field() -> None:
    assert FALLBACK == RateCard(4_000_000, 20_000_000, 200_000, 5_000_000, 8_000_000)
    for model in ("claude-haiku-5-5-20261101", "claude-sonnet-5-5", "unknown"):
        assert rate_card(model, 10) is FALLBACK
        assert cost(model, input_tokens=1_000_000) == 4_000_000
        assert cost(model, input_tokens=10_000, output_tokens=1_000) > cost(
            HAIKU, input_tokens=10_000, output_tokens=1_000
        )


def test_no_tokens_cost_nothing() -> None:
    assert cost(HAIKU) == 0
    assert cost("unknown") == 0
