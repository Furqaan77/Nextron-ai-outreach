"""Ports: the Protocols through which app services reach the outside world (AD-9).

Import rule: ``nextron.domain`` and the standard library only. Adapters in ``nextron.adapters``
implement these Protocols, ``nextron.main`` injects them, and every port has an in-memory fake
in ``tests/fakes``. ``UsageMeterPort`` (``usage_meter``) is the exception: ``nextron.db.metering``
implements it, because metering writes the database, and every HTTP adapter takes one.

``LlmPort`` (``llm``) is the one way to call Claude: ``run(route_tuple, inputs, urgency)``
returns a typed ``LlmResult`` (ok, refused, truncated, invalid or failed) carrying the call's
metered usage. Its values live in ``nextron.domain.ai``; ``nextron.adapters.ai`` implements it.

``CrawlerPort`` (``crawler``) is the one way to fetch the web (Story 2.5): ``fetch_robots`` and
``fetch_page`` pass every hop through ``nextron.domain.fetch_policy`` and return parsed, visible
text; ``nextron.adapters.crawler`` implements it.

``RegistryPort`` (``registry``) is the one way to read the UK companies register (Story 2.12):
``search_companies(name)`` and ``company(number)`` return ``RegistryCompany`` values (number,
registered name, company type, company status, registered office postcode);
``nextron.adapters.companies_house`` implements it over the Companies House API, metered and
self rate-limited.

``EmailFinderPort`` and ``DomainSearchPort`` (``email_finder``, Story 2.13) ask a paid finder
for one person's address and Hunter's Domain Search for a domain's pattern and people;
``nextron.adapters.hunter`` implements both and ``nextron.adapters.prospeo`` the first.
"""
