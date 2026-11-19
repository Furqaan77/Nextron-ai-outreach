"""The extraction eval's fixtures, harness and network allowance (Story 2.8), in the default run
with ``ScriptedLlm``: no request ever reaches Claude here. Rows: Eval ok, Eval bad."""

import json
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nextron import main
from nextron.domain.ai import RouteTuple, Urgency
from nextron.domain.segments import signal_catalogue
from nextron.ports.errors import AdapterError
from nextron.ports.llm import LlmOk, LlmRefused, LlmUsage
from tests.ai_eval import extraction
from tests.ai_eval import test_extraction_eval as opt_in
from tests.ai_eval.extraction import (
    CASES_DIR,
    Case,
    default_tuples,
    evaluate,
    fixture_problems,
    load_case,
    load_cases,
    shipped_segments,
)
from tests.conftest import AI_EVAL, allowed_hosts
from tests.fakes.llm import ScriptedLlm
from tests.live_guard import AI_EVAL_HOSTS, NetworkInTests, guard_network

HAIKU = "claude-haiku-5-5"
TEST_NET = "192.0.2.10"  # TEST-NET-1: never routed
OTHER_TEST_NET = "192.0.2.11"


@pytest.fixture(scope="module")
def tuples() -> dict[str, RouteTuple]:
    return default_tuples(*main.load_prompts())


def ok(parsed: dict[str, Any], cost: int = 7) -> LlmOk:
    return LlmOk(parsed, model=HAIKU, usage=LlmUsage(requests=1, cost_micros=cost))


def labelled_answer(case: Case) -> LlmOk:
    """The answer a perfect model gives: exactly the case's labels."""
    if case.route == "extract_people":
        people = [
            {"name": p.name, "role": p.role, "block_id": p.block_id, "quote": p.quote}
            for p in case.people
        ]
        return ok({"people": people})
    if case.route == "signal_inbox_parse":
        [label] = case.signals or [None]
        return ok(
            {
                "segment": case.segment if label else None,
                "signal_type": label.signal_type if label else None,
                "website": None,
                "author_name": None,
                "author_role": None,
                "event_date": None,
                "quote": label.quote if label else None,
            }
        )
    signals = [
        {
            "signal_type": label.signal_type,
            "block_id": label.block_id,
            "quote": label.quote,
            "event_date": None,
        }
        for label in case.signals
    ]
    return ok({"signals": signals, "profile_facts": [], "size_clues": [], "job_postings": []})


# The fixtures -----------------------------------------------------------------------------------


def test_the_fixtures_are_sound_and_cover_both_segments() -> None:
    cases = load_cases()

    assert len(cases) == len(list(CASES_DIR.glob("*.json"))) >= 10
    assert fixture_problems(cases, shipped_segments()) == []


def test_each_case_is_named_after_its_file_and_uses_test_domains_only() -> None:
    for path in sorted(CASES_DIR.glob("*.json")):
        case = load_case(path)
        assert case.case == path.stem
        text = path.read_text(encoding="utf-8")
        assert "@" not in text  # no email address, invented or not
        for word in text.replace("(", " ").replace(")", " ").split():
            if word.rstrip(".,").endswith((".com", ".org", ".net", ".co.uk")):
                pytest.fail(f"{path.name} names a real-looking domain: {word}")


def test_the_injection_fixtures_each_forbid_what_they_ask_for() -> None:
    by_injection = {case.injection: case for case in load_cases() if case.injection}

    assert set(by_injection) == {"hidden_instructions", "invented_quote", "dictated_tier"}
    for case in by_injection.values():
        assert case.forbidden_signals
        assert case.signals  # a genuine Signal sits next to the attack


def probe(tmp_path: Path, **changes: Any) -> Case:
    data = json.loads((CASES_DIR / "law_signals_hiring_and_expansion.json").read_text("utf-8"))
    expect = {**data["expect"], **changes.pop("expect", {})}
    path = tmp_path / "probe.json"
    path.write_text(json.dumps({**data, **changes, "expect": expect}), encoding="utf-8")
    return load_case(path)


def labels(signal_type: str, block_id: str, quote: str) -> dict[str, object]:
    """An ``expect`` change holding one labelled Signal."""
    return {"signals": [{"signal_type": signal_type, "block_id": block_id, "quote": quote}]}


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        (
            {"expect": labels("law_expansion", "p3", "opens two new offices")},
            "a label's quote is not in block p3",
        ),
        (
            {"expect": labels("law_expansion", "p9", "News")},
            "a label's quote is not in block p9",
        ),
        (
            {"expect": labels("ops_expansion", "p3", "News")},
            "ops_expansion is not a quotable Signal type of law_private_ai",
        ),
        (
            {"expect": {"forbidden_signals": ["law_contact_form_typo"]}},
            "law_contact_form_typo is not a quotable Signal type of law_private_ai",
        ),
        (  # rule-only (absence Evidence): the catalogue omits it, so the model can't propose it
            {"expect": labels("law_contact_form_only", "p3", "News")},
            "law_contact_form_only is not a quotable Signal type of law_private_ai",
        ),
        (
            {"expect": {"forbidden_signals": ["law_contact_form_only"]}},
            "law_contact_form_only is not a quotable Signal type of law_private_ai",
        ),
        ({"segment": "dentists"}, "unknown Segment dentists"),
        (
            {"blocks": [{"id": "signal_types", "source": "website", "text": "x"}]},
            "none is signal_types",
        ),
        (
            {"blocks": [{"id": "p1", "source": "signal_inbox", "text": "News"}]},
            "every block of a extract_signals case is website",
        ),
    ],
)
def test_the_fixture_check_catches_each_problem(
    changes: dict[str, Any], problem: str, tmp_path: Path
) -> None:
    case = probe(tmp_path, **changes)

    problems = fixture_problems([case, *load_cases()], shipped_segments())

    assert any(problem in found for found in problems), problems


def test_the_coverage_check_catches_a_thin_set() -> None:
    cases = [case for case in load_cases() if case.segment == "law_private_ai"]
    cases = [case for case in cases if case.route != "signal_inbox_parse" and not case.injection]

    problems = fixture_problems(cases, shipped_segments())

    assert "ops_automation has 0 extract_signals cases, fewer than 2" in problems
    assert "law_private_ai has 0 signal_inbox_parse cases, fewer than 2" in problems
    assert any("labelled Signals, fewer than 10" in found for found in problems)
    assert "no dictated_tier case" in problems


def test_a_malformed_case_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"case": "bad", "route": "extract_people"}), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly"):
        load_case(path)


# The harness ------------------------------------------------------------------------------------


# Row: Eval ok
def test_scripted_labelled_answers_score_full_marks(tuples: dict[str, RouteTuple]) -> None:
    cases = load_cases()
    llm = ScriptedLlm(*(labelled_answer(case) for case in cases))

    report = evaluate(llm, cases, tuples)

    assert (report.quote_share, report.signal_recall, report.people_recall) == (1.0, 1.0, 1.0)
    assert (report.violations, report.failed_calls) == ((), ())
    assert report.cases == len(cases)
    assert report.quotes_proposed == sum(len(c.signals) + len(c.people) for c in cases)
    assert report.signals_labelled >= 10
    assert report.cost_micros == 7 * len(cases)
    assert "cost: $0.0001" in report.summary()


def test_each_call_carries_the_right_blocks_tuple_and_urgency(
    tuples: dict[str, RouteTuple],
) -> None:
    cases = load_cases()
    llm = ScriptedLlm(*(labelled_answer(case) for case in cases))
    segments = shipped_segments()

    evaluate(llm, cases, tuples)

    for number, (case, (route_tuple, blocks, urgency)) in enumerate(zip(cases, llm.calls), 1):
        assert route_tuple is tuples[case.route]
        page_blocks = [b for b in blocks if b.source != "signal_types"]
        assert [(b.block_id, b.source, b.text) for b in page_blocks] == [
            (block.id, block.source, block.text) for block in case.blocks
        ]
        catalogue = [b for b in blocks if b.source == "signal_types"]
        if case.route == "extract_people":
            assert catalogue == []
            assert urgency is Urgency.BULK
            assert {b.subject for b in blocks} == {f"lead:case{number}"}
        elif case.route == "extract_signals":
            [own] = [s for s in segments if s.key == case.segment]
            assert [b.text for b in catalogue] == [signal_catalogue([own])]
            assert blocks[0] is catalogue[0]  # the catalogue comes first
            assert urgency is Urgency.BULK
        else:
            assert [b.text for b in catalogue] == [signal_catalogue(segments)]
            assert urgency is Urgency.SOON
            assert {b.subject for b in blocks} == {f"signal_item:case{number}"}


# Row: Eval bad
def test_an_invented_quote_a_forbidden_type_and_a_refusal_are_counted(
    tuples: dict[str, RouteTuple],
) -> None:
    by_name = {case.case: case for case in load_cases()}
    cases = [
        by_name["law_signals_invented_quote"],
        by_name["law_inbox_dictated_tier"],
        by_name["ops_people_leadership_page"],
    ]
    invented = {
        "signal_type": "law_hiring_legal_ops",
        "block_id": "p2",
        "quote": "We have hired a Knowledge Management Lawyer",  # not on the page
        "event_date": None,
    }
    obeyed = labelled_answer(cases[1]).parsed | {"signal_type": "law_hiring_legal_ops"}
    llm = ScriptedLlm(
        ok({"signals": [invented], "profile_facts": [], "size_clues": [], "job_postings": []}),
        ok(obeyed),
        LlmRefused("cyber", model=HAIKU, usage=LlmUsage(requests=1, cost_micros=3)),
    )

    report = evaluate(llm, cases, tuples)

    assert report.quote_share < 1.0
    assert (report.quotes_proposed, report.quotes_passed) == (2, 1)
    assert report.violations == ("law_inbox_dictated_tier: law_hiring_legal_ops",)
    assert report.failed_calls == ("ops_people_leadership_page: LlmRefused",)
    assert (report.signals_labelled, report.signals_found) == (2, 1)
    assert (report.people_labelled, report.people_found) == (3, 0)
    assert report.cost_micros == 7 + 7 + 3
    assert "injection violations: 1 (bar 0)" in report.summary()


def test_a_quote_citing_the_signal_types_block_or_no_block_fails(
    tuples: dict[str, RouteTuple],
) -> None:
    [case] = [c for c in load_cases() if c.case == "law_signals_hiring_and_expansion"]

    def signal(block_id: str, quote: object) -> dict[str, object]:
        return {
            "signal_type": "law_expansion",
            "block_id": block_id,
            "quote": quote,
            "event_date": None,
        }

    signals = [
        signal("signal_types", "law_expansion"),  # the catalogue block is never quoted
        signal("p7", "News"),  # no such block
        signal("p3", 7),  # not text
        signal("p3", "  News\n"),  # whitespace is normalised: passes
    ]
    answer = {"signals": signals, "profile_facts": [], "size_clues": [], "job_postings": []}

    report = evaluate(ScriptedLlm(ok(answer)), [case], tuples)

    assert (report.quotes_proposed, report.quotes_passed) == (4, 1)


def test_every_quoted_list_counts(tuples: dict[str, RouteTuple]) -> None:
    [case] = [c for c in load_cases() if c.case == "law_signals_hiring_and_expansion"]
    quoted = {"block_id": "p1", "quote": "14 lawyers in Leeds"}
    posting = {
        "title": "Legal Operations Manager",
        "url": None,
        "posted_date": None,
        "block_id": "p2",
        "quote": "Legal Operations Manager",
    }
    answer = {
        "signals": [],
        "profile_facts": [{"kind": "location", "value": "Leeds", **quoted}],
        "size_clues": [{"kind": "lawyers_firm_wide", "number": 14, **quoted}],
        "job_postings": [posting],
    }

    report = evaluate(ScriptedLlm(ok(answer)), [case], tuples)

    assert (report.quotes_proposed, report.quotes_passed) == (3, 3)
    assert report.signals_found == 0


def test_a_raised_adapter_error_is_a_failed_call(tuples: dict[str, RouteTuple]) -> None:
    [case] = [c for c in load_cases() if c.case == "ops_inbox_job_ad"]
    llm = ScriptedLlm(AdapterError(True, "llm_unavailable", "the API is down"))

    report = evaluate(llm, [case], tuples)

    assert report.failed_calls == ("ops_inbox_job_ad: llm_unavailable",)
    assert (report.signals_labelled, report.signals_found) == (1, 0)


def test_a_route_without_a_tuple_is_refused_before_any_call(
    tuples: dict[str, RouteTuple],
) -> None:
    cases = sorted(load_cases(), key=lambda case: case.route != "extract_people")
    assert cases[0].route == "extract_people"  # a call would come first, if any were made
    partial = {"extract_people": tuples["extract_people"]}
    llm = ScriptedLlm(labelled_answer(cases[0]))

    with pytest.raises(ValueError, match="no route tuple for extract_signals, signal_inbox_parse"):
        evaluate(llm, cases, partial)

    assert llm.calls == []


def test_an_inbox_type_under_the_wrong_segment_is_not_found_but_still_violates(
    tuples: dict[str, RouteTuple],
) -> None:
    by_name = {case.case: case for case in load_cases()}
    confidentiality = by_name["law_inbox_confidentiality_post"]
    dictated = by_name["law_inbox_dictated_tier"]
    right_type = labelled_answer(confidentiality).parsed | {"segment": "ops_automation"}
    obeyed = labelled_answer(dictated).parsed | {
        "segment": "ops_automation",
        "signal_type": "law_hiring_legal_ops",
    }

    report = evaluate(ScriptedLlm(ok(right_type), ok(obeyed)), [confidentiality, dictated], tuples)

    assert (report.signals_labelled, report.signals_found) == (2, 0)
    assert report.violations == ("law_inbox_dictated_tier: law_hiring_legal_ops",)
    assert report.quotes_passed == 2  # the quotes themselves are fine


def test_the_default_tuples_carry_the_prompt_files_text() -> None:
    files, specs = main.load_prompts()

    found = default_tuples(files, specs)

    assert set(found) == {"extract_signals", "extract_people", "signal_inbox_parse"}
    for prompt in (f for f in files if f.route in found):
        assert found[prompt.route].system_prompt == prompt.text
        assert found[prompt.route].prompt_sha256 == prompt.sha256


def test_the_opt_in_eval_is_marked_ai_eval() -> None:
    marks = getattr(opt_in.test_extraction_meets_the_bars, "pytestmark", [])

    assert AI_EVAL in [mark.name for mark in marks]
    assert extraction.CASES_DIR.is_dir()


# The network allowance --------------------------------------------------------------------------


def test_only_an_ai_eval_item_may_reach_the_claude_api(request: pytest.FixtureRequest) -> None:
    def item(marked: bool) -> Any:
        mark = SimpleNamespace(name=AI_EVAL) if marked else None
        return SimpleNamespace(get_closest_marker=lambda name: mark if name == AI_EVAL else None)

    assert AI_EVAL_HOSTS == frozenset({"api.anthropic.com"})
    assert allowed_hosts(item(True)) == AI_EVAL_HOSTS
    assert allowed_hosts(item(False)) == frozenset()
    assert allowed_hosts(request.node) == frozenset()  # this test isn't ai_eval
    with pytest.raises(NetworkInTests):
        socket.getaddrinfo("api.anthropic.com", 443)


@pytest.fixture
def resolver(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    """Stand-ins under a fresh guard: every lookup resolves to ``TEST_NET`` and every connect is
    only recorded, so nothing reaches a real resolver or address. The guard on top allows the
    Claude host, as an ``ai_eval`` item gets it."""
    seen: list[tuple[str, object]] = []

    def getaddrinfo(host: object, port: object, *args: object, **kwargs: object) -> list[Any]:
        seen.append(("lookup", host))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (TEST_NET, port))]

    def gethostbyname(host: object) -> str:
        seen.append(("lookup", host))
        return TEST_NET

    def gethostbyname_ex(host: object) -> tuple[object, list[str], list[str]]:
        seen.append(("lookup", host))
        return host, [], [TEST_NET]

    def connect(self: socket.socket, address: object) -> None:
        seen.append(("connect", address))

    def connect_ex(self: socket.socket, address: object) -> int:
        seen.append(("connect", address))
        return 0

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "gethostbyname", gethostbyname)
    monkeypatch.setattr(socket, "gethostbyname_ex", gethostbyname_ex)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    guard_network(monkeypatch, allowed_hosts=AI_EVAL_HOSTS)
    return seen


def test_an_allowed_host_resolves_and_only_its_addresses_connect(
    resolver: list[tuple[str, object]],
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkInTests):  # before any lookup, its address isn't allowed
            sock.connect((TEST_NET, 443))

    found = socket.getaddrinfo("API.Anthropic.com", 443)

    assert found[0][4] == (TEST_NET, 443)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((TEST_NET, 443))
        assert sock.connect_ex((TEST_NET, 443)) == 0
        with pytest.raises(NetworkInTests):
            sock.connect((OTHER_TEST_NET, 443))
    with pytest.raises(NetworkInTests):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(NetworkInTests):
        socket.gethostbyaddr(TEST_NET)
    assert resolver == [
        ("lookup", "API.Anthropic.com"),
        ("connect", (TEST_NET, 443)),
        ("connect", (TEST_NET, 443)),
    ]


def test_the_other_forward_lookups_remember_their_addresses_too(
    resolver: list[tuple[str, object]],
) -> None:
    assert socket.gethostbyname("api.anthropic.com") == TEST_NET
    assert socket.gethostbyname_ex("api.anthropic.com")[2] == [TEST_NET]
    with pytest.raises(NetworkInTests):
        socket.gethostbyname("api.anthropic.com.evil.test")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((TEST_NET, 443))
    assert resolver[-1] == ("connect", (TEST_NET, 443))


def test_loopback_still_works_under_the_allowance(resolver: list[tuple[str, object]]) -> None:
    assert socket.getaddrinfo("localhost", 80)[0][4] == (TEST_NET, 80)  # the stand-in answers
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect(("127.0.0.1", 80))
    assert resolver[-1] == ("connect", ("127.0.0.1", 80))
