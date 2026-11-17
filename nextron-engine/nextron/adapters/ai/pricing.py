"""What a Claude request costs, in integer micro-dollars (AD-8, FR-57, NFR-11). Standard library
only.

**Rate cards** are integer micro-dollars per million tokens: input, output, cache read, 5-minute
cache write and 1-hour cache write. They come from the Claude API pricing reference (cached
2026-11-03); the planning docs record only the input and output rates of Haiku 5.5 up to 100K
tokens and of Opus 5.5. Check them against Anthropic's pricing page.

- ``claude-haiku-5-5``, a prompt of 100,000 tokens or fewer: 100,000 / 500,000 / 10,000 /
  125,000 / 200,000 ($0.10 and $0.50 per million input and output tokens);
- ``claude-haiku-5-5``, a prompt over 100,000 tokens: 500,000 / 2,500,000 / 50,000 / 625,000 /
  1,000,000;
- ``claude-opus-5-5``: 4,000,000 / 20,000,000 / 200,000 / 5,000,000 / 8,000,000.

**``cost_micros(model, *, input_tokens, output_tokens, cache_read_tokens,
cache_write_5m_tokens, cache_write_1h_tokens)``** is ``ceil(Σ tokens × rate / 10^6)``, so any
billed token costs at least 1 micro-dollar. The prompt is the input, cache-read and cache-write
tokens together. A model that isn't listed is priced at the dearest card, field by field
(``FALLBACK``), so the budget errs toward stopping early. The counts are the caller's
non-negative ints; this module checks nothing.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

PER_MILLION: Final = 1_000_000
HAIKU_LONG_PROMPT: Final = 100_000  # Haiku 5.5's long-prompt card starts above this


@dataclass(frozen=True, slots=True)
class RateCard:
    """Micro-dollars per million tokens of each kind."""

    input: int
    output: int
    cache_read: int
    cache_write_5m: int
    cache_write_1h: int


@dataclass(frozen=True, slots=True)
class ModelPrices:
    """A model's card, and the card for prompts over ``long_after`` tokens (if it has one)."""

    standard: RateCard
    long: RateCard | None = None
    long_after: int = 0


HAIKU_STANDARD: Final = RateCard(100_000, 500_000, 10_000, 125_000, 200_000)
HAIKU_LONG: Final = RateCard(500_000, 2_500_000, 50_000, 625_000, 1_000_000)
OPUS: Final = RateCard(4_000_000, 20_000_000, 200_000, 5_000_000, 8_000_000)

PRICES: Final[Mapping[str, ModelPrices]] = MappingProxyType(
    {
        "claude-haiku-5-5": ModelPrices(HAIKU_STANDARD, HAIKU_LONG, HAIKU_LONG_PROMPT),
        "claude-opus-5-5": ModelPrices(OPUS),
    }
)


def _cards() -> list[RateCard]:
    found = []
    for prices in PRICES.values():
        found.append(prices.standard)
        if prices.long is not None:
            found.append(prices.long)
    return found


FALLBACK: Final = RateCard(
    input=max(card.input for card in _cards()),
    output=max(card.output for card in _cards()),
    cache_read=max(card.cache_read for card in _cards()),
    cache_write_5m=max(card.cache_write_5m for card in _cards()),
    cache_write_1h=max(card.cache_write_1h for card in _cards()),
)


def rate_card(model: str, prompt_tokens: int) -> RateCard:
    """The card that prices a request to ``model`` with ``prompt_tokens`` of prompt."""
    prices = PRICES.get(model)
    if prices is None:
        return FALLBACK
    if prices.long is not None and prompt_tokens > prices.long_after:
        return prices.long
    return prices.standard


def cost_micros(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_5m_tokens: int,
    cache_write_1h_tokens: int,
) -> int:
    """What the request cost, in micro-dollars, rounded up."""
    prompt = input_tokens + cache_read_tokens + cache_write_5m_tokens + cache_write_1h_tokens
    card = rate_card(model, prompt)
    total = (
        input_tokens * card.input
        + output_tokens * card.output
        + cache_read_tokens * card.cache_read
        + cache_write_5m_tokens * card.cache_write_5m
        + cache_write_1h_tokens * card.cache_write_1h
    )
    return -(-total // PER_MILLION)
