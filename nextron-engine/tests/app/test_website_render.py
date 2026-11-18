"""Story 2.6's detection and rendering through the ``research`` service on a temporary database at
head (FR-10, AD-9, AD-10, AD-18): ``WebsiteCrawls.enrich_lead`` storing each page's contact
methods and queueing ``render_pages``, and ``WebsiteCrawls.render_pages`` with
``ScriptedRenderer``, on the world of ``tests/app/test_website_crawl.py``.

Every row of the spec's matrix that the service owns is here: the rich page, the near-empty page,
the render, a failed render, the budget, more than three shells, a stale or merged run and the
generic signals. So is where Story 2.9's ``extract_lead`` is queued: by the crawl when no page
needs rendering, else by the render's apply, once per crawl run either way. The adapter's own
rows (private and denylisted sub-resources, off-site navigation, disallowed traffic) are in
``tests/unit/test_renderer_adapter.py``."""

import contextvars
import json
import logging
from datetime import timedelta
from typing import Any

import pytest

from nextron import main
from nextron.app.queries import research as research_queries
from nextron.domain.budget import AiCall
from nextron.domain.crawl import text_hash
from nextron.domain.job import DEFERRED, Job, Lane
from nextron.domain.widgets import ContactMethod, decode
from nextron.ports.errors import AdapterError
from nextron.ports.renderer import RenderedPage
from tests.app.test_website_crawl import (  # noqa: F401 - world is the shared fixture
    ALLOW_ALL,
    HOME,
    ORIGIN,
    SITE,
    T0,
    World,
    no_jobs,
    world,
)
from tests.fakes.crawler import html_page, robots_txt
from tests.fakes.renderer import RenderCall, rendered_page

FILLER = "We advise families and businesses across the north of England. "  # 63 characters
RICH_HOME = (
    "<html><head><script src='https://widget.intercom.io/widget/abc123'></script></head><body>"
    f"<h1>Smith Law LLP</h1><p>{FILLER * 32}</p>"
    "<a href='tel:+441612345678'>Call us</a><a href='/team'>Our team</a></body></html>"
)
SHELL = "<html><body><div id='root'>Smith Law LLP: loading our website for you</div></body>"
RENDERED_HOME = (
    f"<html><body><h1>Smith Law LLP</h1><p>{FILLER * 18}</p>"
    "<iframe src='https://calendly.com/smith-law/consultation'></iframe></body></html>"
)
TEAM = f"{ORIGIN}/team"


def render(world: World, lead: str, run_id: str | None = None) -> bool:
    found = run_id if run_id is not None else world.run(lead).id
    with world.units.open():
        return world.crawls.render_pages(lead, found)


def methods(world: World, lead: str) -> list[tuple[str, str, str, str, str]]:
    pages = {page.id: page.url for page in world.pages(lead).values()}
    rows = world.read(lambda: research_queries.page_contact_methods(lead))
    return [(pages[r.page_id], r.method, r.signature, r.source, r.matched) for r in rows]


def profile(world: World, lead: str) -> Any:
    return world.read(lambda: research_queries.contact_methods(lead))


def spend_the_budget(world: World) -> None:
    world.meter.record(
        AiCall("r", "t", "m", input_tokens=1, output_tokens=1, cost_micros=20_000_000)
    )


def shell_site(world: World) -> None:
    world.site({HOME: html_page(HOME, SHELL)})


def rich_site(world: World) -> None:
    """A home of over 2,000 characters with an Intercom script, a tel: link and a team page."""
    world.site({HOME: html_page(HOME, RICH_HOME), TEAM: html_page(TEAM, f"<p>{FILLER * 9}</p>")})


# Row: Rich page ---------------------------------------------------------------------------------


def test_a_rich_page_stores_its_methods_queues_no_render_and_sets_contact_methods(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    lead = world.lead()
    rich_site(world)

    with caplog.at_level(logging.INFO, logger="nextron.app.research.api"):
        assert world.crawl(lead) is True

    assert finished(caplog, "crawl_finished").render_queued is False  # type: ignore[attr-defined]
    assert len(world.pages(lead)[HOME].text) > 2000
    assert methods(world, lead) == [
        (HOME, "chat", "intercom", "html", "widget.intercom.io"),
        (HOME, "chat", "intercom", "script", "widget.intercom.io"),
        (HOME, "phone", "tel_link", "html", "tel:+441612345678"),
    ]
    # no near-empty page: nothing to render, and the Signals are extracted at once (Story 2.9)
    assert [row[0] for row in world.jobs()] == ["extract_lead"]
    assert world.renderer.calls == []
    value = profile(world, lead)
    assert (value.run_id, value.checked_at) == (world.run(lead).id, T0)
    assert value.pages_checked == (HOME, TEAM)
    assert dict(value.found) == {
        ContactMethod.FORM: (),
        ContactMethod.CHAT: (HOME,),
        ContactMethod.BOOKING: (),
        ContactMethod.PHONE: (HOME,),
        ContactMethod.AFTER_HOURS: (),
    }
    [row] = world.sql("SELECT contact_methods FROM leads WHERE id = ?", (lead,))
    assert decode(row[0]) == value


def test_detections_are_stored_with_their_time_and_matched_stays_out_of_the_repr(
    world: World,
) -> None:
    lead = world.lead()
    rich_site(world)

    world.crawl(lead)

    rows = world.read(lambda: research_queries.page_contact_methods(lead))
    assert {row.detected_at for row in rows} == {T0}
    assert all("tel:" not in repr(row) for row in rows)
    assert world.read(lambda: research_queries.page_contact_methods("not-a-ulid")) == ()
    assert world.read(lambda: research_queries.contact_methods("not-a-ulid")) is None


# Row: Generic signals ---------------------------------------------------------------------------


def test_the_generic_signals_are_detected_from_text_and_html(world: World) -> None:
    lead = world.lead()
    home = (
        f"<p>{FILLER * 9}</p><p>24/7 emergency line</p><p>Live chat</p>"
        "<p>Call (212) 555-0147</p><form><textarea name='message'></textarea></form>"
    )
    world.site({HOME: html_page(HOME, home)})

    world.crawl(lead)

    assert methods(world, lead) == [
        (HOME, "after_hours", "after_hours_text", "text", "24/7"),
        (HOME, "chat", "chat_text", "text", "Live chat"),
        (HOME, "form", "form_tag", "html", "form textarea"),
        (HOME, "phone", "phone_text", "text", "(212) 555-0147"),
    ]
    found = profile(world, lead).found
    assert {method for method, urls in found.items() if urls} == {
        ContactMethod.AFTER_HOURS,
        ContactMethod.CHAT,
        ContactMethod.FORM,
        ContactMethod.PHONE,
    }


def test_a_recrawl_replaces_a_pages_methods(world: World) -> None:
    lead = world.lead()
    rich_site(world)
    world.crawl(lead)
    world.clock.advance(timedelta(days=1))
    world.crawler.pages[HOME] = html_page(HOME, f"<p>{FILLER * 9}</p><p>Live chat</p>")

    world.crawl(lead)

    assert methods(world, lead) == [(HOME, "chat", "chat_text", "text", "Live chat")]
    value = profile(world, lead)
    assert (value.run_id, value.checked_at) == (world.run(lead).id, T0 + timedelta(days=1))
    assert value.found[ContactMethod.PHONE] == ()
    assert value.pages_checked == (HOME,)  # the team page wasn't fetched by this run


# Row: Near-empty page ---------------------------------------------------------------------------


def test_a_near_empty_page_queues_render_pages_on_the_bulk_lane_at_priority_40(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    lead = world.lead()
    shell_site(world)

    with caplog.at_level(logging.INFO, logger="nextron.app.research.api"):
        world.crawl(lead)

    assert finished(caplog, "crawl_finished").render_queued is True  # type: ignore[attr-defined]
    assert len(world.pages(lead)[HOME].text) < 500
    run = world.run(lead)
    [job] = world.sql(
        "SELECT type, idempotency_key, concurrency_key, payload, lane, priority FROM jobs"
    )
    assert job == (
        "render_pages",
        f"render_pages:{lead}:2026-11-17",
        f"domain:{SITE}",
        json.dumps({"lead_id": lead, "run_id": run.id, "v": 1}, separators=(",", ":")),
        "bulk",
        40,
    )
    assert world.renderer.calls == []  # only the job renders
    assert profile(world, lead) is None  # not until the render


def test_a_run_that_stored_nothing_keeps_the_previous_contact_methods(world: World) -> None:
    lead = world.lead()
    rich_site(world)
    world.crawl(lead)
    before = profile(world, lead)
    world.clock.advance(timedelta(days=1))
    world.crawler.pages[HOME] = AdapterError(False, "fetch_dns_failed", "no such host")

    world.crawl(lead)

    assert world.run(lead).outcome == "unreachable"
    assert profile(world, lead) == before
    assert [row[0] for row in world.jobs()] == ["extract_lead"]  # the first run's only


# Row: Render ------------------------------------------------------------------------------------


def test_a_render_replaces_the_text_adds_its_methods_and_sets_contact_methods(
    world: World,
) -> None:
    lead = world.lead()
    world.site(
        {
            HOME: html_page(HOME, f"{SHELL}<a href='/team'>Team</a><a href='tel:0161'>Call</a>"),
            TEAM: html_page(TEAM, f"<p>{FILLER * 9}</p>"),
        }
    )
    world.crawl(lead)
    shell = world.pages(lead)[HOME]
    world.clock.advance(timedelta(minutes=3))
    world.renderer.pages[HOME] = rendered_page(HOME, RENDERED_HOME)

    assert render(world, lead) is True

    assert world.renderer.calls == [RenderCall(HOME, SITE, 1.0)]
    home = world.pages(lead)[HOME]
    assert len(home.text) > 1000
    assert home.text.startswith("Smith Law LLP\nWe advise")
    assert (home.content_hash, home.changed_at) == (text_hash(home.text), T0 + timedelta(minutes=3))
    assert (home.fetched_at, home.text_purge_after) == (shell.fetched_at, shell.text_purge_after)
    assert methods(world, lead) == [
        (HOME, "booking", "calendly", "iframe", "calendly.com"),
        (HOME, "phone", "tel_link", "html", "tel:0161"),
    ]
    run = world.run(lead)
    assert (run.renders, run.errors) == (1, 0)
    value = profile(world, lead)
    assert (value.run_id, value.checked_at) == (run.id, T0 + timedelta(minutes=3))
    assert value.pages_checked == (HOME, TEAM)  # every page checked, rendered or not
    assert value.found[ContactMethod.BOOKING] == (HOME,)
    assert value.found[ContactMethod.PHONE] == (HOME,)


def test_a_render_detecting_what_the_crawl_did_adds_no_duplicate(world: World) -> None:
    lead = world.lead()
    world.site({HOME: html_page(HOME, f"{SHELL}<p>Live chat</p>")})
    world.crawl(lead)
    world.renderer.pages[HOME] = rendered_page(HOME, f"<p>{FILLER * 9}</p><p>Live chat now</p>")

    render(world, lead)

    assert methods(world, lead) == [(HOME, "chat", "chat_text", "text", "Live chat")]


def test_an_unchanged_or_empty_render_keeps_changed_at_or_the_text(world: World) -> None:
    lead = world.lead()
    world.site(
        {
            HOME: html_page(HOME, f"{SHELL}<a href='/team'>Team</a>"),
            TEAM: html_page(TEAM, "<p>Team</p>"),
        }
    )
    world.crawl(lead)
    before = world.pages(lead)
    world.clock.advance(timedelta(minutes=1))
    # a DOM that shows no text, only a chat loader; and a page that renders as it was crawled
    world.renderer.pages[HOME] = rendered_page(
        HOME, "<script src='https://embed.tawk.to/abc'></script>"
    )
    world.renderer.pages[TEAM] = rendered_page(TEAM, "<p>Team</p>")

    render(world, lead)

    after = world.pages(lead)
    assert after[HOME].text == before[HOME].text  # an empty render keeps the crawled text
    assert (after[HOME].content_hash, after[HOME].changed_at) == (
        before[HOME].content_hash,
        before[HOME].changed_at,
    )
    assert after[TEAM].changed_at == before[TEAM].changed_at  # the same text: same hash
    assert methods(world, lead) == [  # what the render shows is added all the same
        (HOME, "chat", "tawk_to", "html", "embed.tawk.to"),
        (HOME, "chat", "tawk_to", "script", "embed.tawk.to"),
    ]
    assert world.run(lead).renders == 2


# Row: Render fails ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        AdapterError(False, "render_unavailable", "the browser could not start"),
        AdapterError(True, "render_timeout", "the page did not render in time"),
        AdapterError(False, "fetch_refused", "the fetch was refused (private)"),
    ],
)
def test_a_failed_render_keeps_the_text_counts_an_error_and_still_sets_contact_methods(
    world: World, error: AdapterError
) -> None:
    lead = world.lead()
    world.site({HOME: html_page(HOME, f"{SHELL}<p>Live chat</p>")})
    world.crawl(lead)
    before = world.pages(lead)[HOME]
    world.renderer.pages[HOME] = error

    assert render(world, lead) is True

    after = world.pages(lead)[HOME]
    assert (after.text, after.content_hash, after.changed_at) == (
        before.text,
        before.content_hash,
        before.changed_at,
    )
    run = world.run(lead)
    assert (run.renders, run.errors) == (0, 1)
    assert profile(world, lead).found[ContactMethod.CHAT] == (HOME,)


def test_render_finished_logs_ids_counts_and_codes_only(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    lead = world.lead()
    shell_site(world)
    world.crawl(lead)
    world.renderer.pages[HOME] = AdapterError(False, "render_failed", "the page failed")

    with caplog.at_level(logging.INFO, logger="nextron.app.research.api"):
        render(world, lead)

    [record] = [r for r in caplog.records if r.getMessage() == "render_finished"]
    assert (record.lead_id, record.run_id) == (lead, world.run(lead).id)  # type: ignore[attr-defined]
    assert (record.pages, record.renders, record.errors) == (1, 0, 1)  # type: ignore[attr-defined]
    assert record.error_codes == ["render_failed"]  # type: ignore[attr-defined]
    assert SITE not in str(vars(record))


# Row: Budget refused ----------------------------------------------------------------------------


def test_no_render_and_nothing_written_when_the_budget_is_spent(world: World) -> None:
    lead = world.lead()
    shell_site(world)
    world.crawl(lead)
    world.renderer.pages[HOME] = rendered_page(HOME, RENDERED_HOME)
    spend_the_budget(world)

    assert render(world, lead) is False

    assert world.renderer.calls == []
    assert (world.run(lead).renders, profile(world, lead)) == (0, None)
    assert extractions(world) == []  # not until the render
    handler = main.build_job_types(
        alerts=no_jobs,
        backups=no_jobs,
        restore_tests=no_jobs,
        recovery=no_jobs,
        contact_markets=no_jobs,
        crawls=lambda: world.crawls,
        discovery=no_jobs,
        research=no_jobs,
        extractions=no_jobs,
        primary=no_jobs,
        legal_forms=no_jobs,
    ).get("render_pages").handler
    payload = {"v": 1, "lead_id": lead, "run_id": world.run(lead).id}
    job = Job("01JD0000000000000000000099", "render_pages", Lane.BULK, payload, 1)
    with world.units.open():
        assert handler(job) is DEFERRED


# Row: More than 3 shells ------------------------------------------------------------------------


def test_at_most_three_shells_are_rendered_home_first_then_by_kind_and_url(world: World) -> None:
    lead = world.lead()
    links = "".join(
        f"<a href='/{path}'>{path}</a>" for path in ("news", "contact", "about", "careers", "team")
    )
    world.site({HOME: html_page(HOME, f"{SHELL}{links}")})
    for path in ("news", "contact", "about", "careers", "team"):
        url = f"{ORIGIN}/{path}"
        world.crawler.pages[url] = html_page(url, f"<p>{path}</p>")
        world.renderer.pages[url] = rendered_page(url, f"<p>{FILLER * 9}</p>")
    world.renderer.pages[HOME] = rendered_page(HOME, f"<p>{FILLER * 9}</p>")
    world.crawl(lead)
    assert len(world.pages(lead)) == 6

    render(world, lead)

    assert world.renderer.urls() == [HOME, f"{ORIGIN}/team", f"{ORIGIN}/about"]
    assert world.run(lead).renders == 3
    assert profile(world, lead).pages_checked == tuple(sorted(world.pages(lead)))


def test_the_render_is_pinned_to_the_runs_final_domain(world: World) -> None:
    lead = world.lead()
    landed = "https://www.new-firm.co.uk/"
    world.site({HOME: html_page(HOME, SHELL, final_url=landed)})
    robots = "https://www.new-firm.co.uk/robots.txt"
    world.crawler.robots[robots] = robots_txt(robots, ALLOW_ALL)
    world.crawl(lead)
    world.renderer.pages[landed] = rendered_page(landed, f"<p>{FILLER * 9}</p>")

    render(world, lead)

    assert world.renderer.calls == [RenderCall(landed, "new-firm.co.uk", 1.0)]


def test_a_purged_page_is_never_rendered(world: World) -> None:
    lead = world.lead()
    shell_site(world)
    world.crawl(lead)
    world.clock.set(T0 + timedelta(days=31))
    with world.units.open():
        world.research.purge_page_text()

    assert render(world, lead) is True

    assert world.renderer.calls == []
    assert (world.run(lead).renders, world.run(lead).errors) == (0, 0)
    assert profile(world, lead).pages_checked == (HOME,)


# Row: Stale or merged ---------------------------------------------------------------------------


def test_a_stale_run_renders_nothing(world: World, caplog: pytest.LogCaptureFixture) -> None:
    lead = world.lead()
    shell_site(world)
    world.crawl(lead)
    stale = world.run(lead).id
    world.clock.advance(timedelta(hours=1))
    world.crawl(lead)
    world.renderer.pages[HOME] = rendered_page(HOME, RENDERED_HOME)

    with caplog.at_level(logging.INFO, logger="nextron.app.research.api"):
        assert render(world, lead, stale) is True

    assert world.renderer.calls == []
    assert world.sql("SELECT SUM(renders), SUM(errors) FROM crawl_runs") == [(0, 0)]
    [record] = [r for r in caplog.records if r.getMessage() == "render_skipped"]
    assert record.reason == "stale_run"  # type: ignore[attr-defined]
    assert extractions(world) == []  # a skipped render queues no extraction


def skip_reasons(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.reason  # type: ignore[attr-defined]
        for record in caplog.records
        if record.getMessage() == "render_skipped"
    ]


def finished(caplog: pytest.LogCaptureFixture, event: str) -> logging.LogRecord:
    [record] = [r for r in caplog.records if r.getMessage() == event]
    return record


def test_an_unknown_run_another_leads_run_or_a_merged_lead_is_skipped(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    older = world.lead(web_domain="older-firm.com")
    world.clock.advance(timedelta(minutes=1))
    lead = world.lead()
    shell_site(world)
    world.crawl(lead)
    run_id = world.run(lead).id

    with caplog.at_level(logging.INFO, logger="nextron.app.research.api"):
        assert render(world, older, run_id) is True  # another Lead's run
        assert render(world, lead, "01JD0000000000000000000000") is True  # unknown
        assert render(world, lead, "not-a-ulid") is True
        assert world.renderer.calls == []
        world.transact(lambda: world.discovery.merge(older, lead))
        # the merged Lead's queued job: its run moved to the survivor
        assert render(world, lead, run_id) is True

    assert skip_reasons(caplog) == ["other_lead", "unknown_run", "unknown_run", "other_lead"]
    assert world.renderer.calls == []
    # the one render_pages job queued is the merged Lead's own; the merge queued none
    assert [row[0] for row in world.jobs()].count("render_pages") == 1

    # No code queues render_pages for the survivor after a merge. Only a job run by hand for
    # the survivor with the moved run (now the survivor's latest) would render it, as here.
    world.renderer.pages[HOME] = rendered_page(HOME, f"<p>{FILLER * 9}</p>")
    assert render(world, older, run_id) is True
    assert world.renderer.calls == [RenderCall(HOME, SITE, 1.0)]
    assert profile(world, older).pages_checked == (HOME,)


def test_a_merged_leads_own_run_is_skipped_as_merged_lead(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    older = world.lead(web_domain="older-firm.com")
    world.clock.advance(timedelta(minutes=1))
    lead = world.lead()

    def merge_meanwhile() -> Any:  # the Lead is merged while its crawl runs
        contextvars.Context().run(world.transact, lambda: world.discovery.merge(older, lead))
        return html_page(HOME, SHELL)

    world.site({HOME: html_page(HOME, SHELL)})
    world.crawler.pages[HOME] = merge_meanwhile
    world.crawl(lead)
    [(run_id, outcome)] = world.sql("SELECT id, outcome FROM crawl_runs WHERE lead_id = ?", (lead,))
    assert outcome == "lead_merged"

    with caplog.at_level(logging.INFO, logger="nextron.app.research.api"):
        assert render(world, lead, run_id) is True

    assert skip_reasons(caplog) == ["merged_lead"]
    assert world.renderer.calls == []


def test_a_crawl_finished_during_the_renders_keeps_its_own_results(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    lead = world.lead()
    shell_site(world)
    world.crawl(lead)
    first = world.run(lead).id

    def crawl_meanwhile() -> RenderedPage:  # another unit of work, as the next crawl would be
        world.clock.advance(timedelta(minutes=5))
        rich_site(world)
        contextvars.Context().run(world.crawl, lead)
        return rendered_page(HOME, RENDERED_HOME)

    world.renderer.pages[HOME] = crawl_meanwhile

    with caplog.at_level(logging.INFO, logger="nextron.app.research.api"):
        assert render(world, lead, first) is True

    newer = world.run(lead)
    assert newer.id != first
    assert world.sql("SELECT SUM(renders), SUM(errors) FROM crawl_runs") == [(0, 0)]
    home = world.pages(lead)[HOME]
    assert home.run_id == newer.id
    assert home.text.startswith("Smith Law LLP\nWe advise")  # the newer crawl's, not the render's
    assert len(home.text) > 2000
    assert "calendly" not in str(methods(world, lead))  # the stale render added nothing
    assert [row[2] for row in methods(world, lead)] == ["intercom", "intercom", "tel_link"]
    value = profile(world, lead)
    assert (value.run_id, value.pages_checked) == (newer.id, (HOME, TEAM))
    assert finished(caplog, "render_finished").applied is False  # type: ignore[attr-defined]
    # the newer (rich) run queued its own extraction; the stale render queued none
    assert extractions(world) == [extraction_job(lead, 2, newer.id)]


def test_a_lead_merged_during_the_render_gets_nothing_written(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    older = world.lead(web_domain="older-firm.com")
    world.clock.advance(timedelta(minutes=1))
    lead = world.lead()
    shell_site(world)
    world.crawl(lead)

    def merge_meanwhile() -> RenderedPage:  # another unit of work, as another job would be
        contextvars.Context().run(world.transact, lambda: world.discovery.merge(older, lead))
        return rendered_page(HOME, RENDERED_HOME)

    world.renderer.pages[HOME] = merge_meanwhile

    with caplog.at_level(logging.INFO, logger="nextron.app.research.api"):
        assert render(world, lead) is True

    assert finished(caplog, "render_finished").applied is False  # type: ignore[attr-defined]
    assert world.renderer.urls() == [HOME]
    assert world.sql("SELECT renders, errors FROM crawl_runs") == [(0, 0)]
    assert world.sql("SELECT COUNT(*) FROM page_contact_methods") == [(0,)]
    assert world.sql("SELECT contact_methods FROM leads WHERE contact_methods IS NOT NULL") == []
    [home] = world.sql("SELECT text FROM crawled_pages")
    assert home[0] == "Smith Law LLP: loading our website for you"
    assert extractions(world) == []


def test_a_merge_moves_the_pages_with_their_methods(world: World) -> None:
    older = world.lead(web_domain="older-firm.com")
    world.clock.advance(timedelta(minutes=1))
    lead = world.lead()
    rich_site(world)
    world.crawl(lead)

    world.transact(lambda: world.discovery.merge(older, lead))

    assert [row[1:4] for row in methods(world, older)] == [
        ("chat", "intercom", "html"),
        ("chat", "intercom", "script"),
        ("phone", "tel_link", "html"),
    ]
    assert methods(world, lead) == []


# Signal extraction (Stories 2.6 and 2.9) --------------------------------------------------------


def extractions(world: World) -> list[tuple[Any, ...]]:
    """The ``extract_lead`` jobs queued: key, concurrency key, payload and lane."""
    return [row[1:] for row in world.jobs() if row[0] == "extract_lead"]


def extraction_job(lead: str, revision: int, run_id: str) -> tuple[Any, ...]:
    return (
        f"extract_lead:{lead}:{revision}",
        f"domain:{SITE}",
        json.dumps({"lead_id": lead, "run_id": run_id, "v": 1}, separators=(",", ":")),
        "bulk",
    )


def test_a_run_with_no_near_empty_page_queues_its_extraction_with_the_crawl(world: World) -> None:
    lead = world.lead()
    rich_site(world)

    world.crawl(lead)

    assert extractions(world) == [extraction_job(lead, 1, world.run(lead).id)]
    assert "render_pages" not in [row[0] for row in world.jobs()]


def test_a_near_empty_page_makes_the_render_queue_the_extraction_once(world: World) -> None:
    lead = world.lead()
    shell_site(world)
    world.crawl(lead)
    assert extractions(world) == []  # the extraction waits for the render
    world.renderer.pages[HOME] = rendered_page(HOME, RENDERED_HOME)

    render(world, lead)
    render(world, lead)  # the job again while its extraction is still queued

    assert extractions(world) == [extraction_job(lead, 1, world.run(lead).id)]
    assert [row[0] for row in world.jobs()] == ["render_pages", "extract_lead"]
    assert len(world.pages(lead)[HOME].text) > 1000  # what the extraction will read


def test_a_failed_render_still_queues_the_extraction_of_the_crawled_text(world: World) -> None:
    lead = world.lead()
    shell_site(world)
    world.crawl(lead)
    world.renderer.pages[HOME] = AdapterError(False, "render_unavailable", "no browser")

    render(world, lead)

    assert extractions(world) == [extraction_job(lead, 1, world.run(lead).id)]


def test_each_run_gets_one_extraction_whichever_path_queues_it(world: World) -> None:
    lead = world.lead()
    shell_site(world)
    world.crawl(lead)
    first = world.run(lead).id
    world.renderer.pages[HOME] = rendered_page(HOME, RENDERED_HOME)
    render(world, lead)
    world.clock.advance(timedelta(days=1))
    rich_site(world)

    world.crawl(lead)

    second = world.run(lead).id
    assert extractions(world) == [extraction_job(lead, 1, first), extraction_job(lead, 2, second)]
