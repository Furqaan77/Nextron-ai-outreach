"""The opt-in AI evaluation suite (AD-12, AD-14, AD-18): tests marked ``ai_eval`` that call the
real Claude API. A plain ``pytest`` run skips them; ``uv run pytest -m ai_eval`` runs them. The
harnesses here (``extraction``, Story 2.8) use no database, so an eval can never activate a
tuple."""
