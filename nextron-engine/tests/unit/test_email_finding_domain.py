"""Story 2.13: the pure rules of finding a Contact's email address."""

from datetime import UTC, datetime

import pytest

from nextron.domain import email_finding as ef
from nextron.domain.budget import QUOTAS, BudgetClass
from nextron.domain.contacts import ProvenanceSource
from nextron.domain.email_finding import (
    NameTokens,
    PageAddresses,
    Pattern,
    PatternSource,
    PublishedRule,
)
from nextron.domain.quotes import quote_in

AT = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
JANE = NameTokens("jane", "roe")
A = PublishedRule.NAME_IN_ADDRESS
B = PublishedRule.SAME_LINE


def page(text: str | None, mailto: tuple[str, ...] = (), page_id: str = "p1") -> PageAddresses:
    return PageAddresses(page_id, f"https://roe.law/{page_id}", AT, text, mailto)


def matches(pages, name="Jane Roe", tokens=JANE, mail_domain="roe.law"):
    return ef.published_matches(tokens, name, pages, mail_domain=mail_domain)


# Constants ------------------------------------------------------------------------------------


def test_constants() -> None:
    names = {spec.name for spec in QUOTAS}
    assert ef.HUNTER_QUOTA in names and ef.PROSPEO_QUOTA in names
    assert ef.FIND_CONTACT_EMAIL_JOB == "find_contact_email"
    assert ef.BUDGET_CLASS is BudgetClass.BLOCKABLE
    assert ef.MAX_QUOTE == 300
    assert ef.MAILTO_QUOTE_PREFIX == "mailto:"
    assert {"info", "contact", "office", "admin", "team", "sales"} <= ef.ROLE_LOCAL_PARTS
    assert len(ef.ROLE_LOCAL_PARTS) == 31


# Name tokens ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Jane Roe", ("jane", "roe")),
        ("  JANE   ROE ", ("jane", "roe")),
        ("José García", ("jose", "garcia")),
        ("Zoë Brontë-Smith", ("zoe", "brontesmith")),
        ("Liam O'Neil", ("liam", "oneil")),
        ("Liam O’Neil", ("liam", "oneil")),
        ("Anna Smith-Jones", ("anna", "smithjones")),
        ("Anna Smith - Jones", ("anna", "jones")),
        ("Dr. Jane Roe", ("jane", "roe")),
        ("Mrs Jane Roe", ("jane", "roe")),
        ("Prof Sir John Smith", ("john", "smith")),
        ("John Smith Jr.", ("john", "smith")),
        ("John Smith, Esq.", ("john", "smith")),
        ("Jane Roe KC", ("jane", "roe")),
        ("John Smith III", ("john", "smith")),
        ("Jane Q. Roe", ("jane", "roe")),
        ("Jane Q R Roe", ("jane", "roe")),
        ("Jane Mary Roe", ("jane", "roe")),
        ("J. Roe", ("j", "roe")),
        ("Jane R", ("jane", "r")),
        ("Straße Weiß", ("strasse", "weiss")),
        ("Madonna", ("madonna", None)),
        ("Dr Madonna", ("madonna", None)),
        ("Jane2Roe", ("jane", "roe")),
    ],
)
def test_name_tokens(name: str, expected: tuple[str, str | None]) -> None:
    tokens = ef.name_tokens(name)
    assert tokens == NameTokens(*expected)


@pytest.mark.parametrize("name", ["", "   ", "Dr.", "Mr Jr", "1234", "-'"])
def test_name_tokens_none(name: str) -> None:
    assert ef.name_tokens(name) is None


def test_name_tokens_type_and_repr() -> None:
    with pytest.raises(TypeError):
        ef.name_tokens(None)  # type: ignore[arg-type]
    assert "jane" not in repr(JANE)


# Patterns -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "local"),
    [
        (Pattern.FIRST_DOT_LAST, "jane.roe"),
        (Pattern.FLAST, "jroe"),
        (Pattern.FIRST, "jane"),
        (Pattern.FIRSTLAST, "janeroe"),
        (Pattern.FIRST_UNDERSCORE_LAST, "jane_roe"),
        (Pattern.LAST_DOT_FIRST, "roe.jane"),
        (Pattern.F_DOT_LAST, "j.roe"),
    ],
)
def test_local_part_and_fitting(pattern: Pattern, local: str) -> None:
    assert ef.local_part(pattern, JANE) == local
    assert ef.fitting(local, JANE) == {pattern}
    assert ef.fitting(local.upper() + "+news", JANE) == {pattern}
    assert ef.address_for(pattern, JANE, "Roe.LAW") == f"{local}@roe.law"


def test_patterns_without_last() -> None:
    only = NameTokens("jane", None)
    for pattern in Pattern:
        expected = "jane" if pattern is Pattern.FIRST else None
        assert ef.local_part(pattern, only) == expected
    assert ef.fitting("jane", only) == {Pattern.FIRST}
    assert ef.fitting("jane", JANE) == {Pattern.FIRST}
    assert ef.address_for(Pattern.FLAST, only, "roe.law") is None
    assert ef.address_for(Pattern.FIRST, only, "roe.law") == "jane@roe.law"


def test_fitting_ambiguous_and_none() -> None:
    one_letter = NameTokens("j", "roe")
    assert ef.fitting("jroe", one_letter) == {Pattern.FLAST, Pattern.FIRSTLAST}
    assert ef.fitting("j.roe", one_letter) == {Pattern.FIRST_DOT_LAST, Pattern.F_DOT_LAST}
    assert ef.fitting("someone", JANE) == frozenset()


def test_address_for_refused() -> None:
    assert ef.address_for(Pattern.FIRST, JANE, "localhost") is None
    assert ef.address_for(Pattern.FIRST, JANE, "bad_domain.com") is None


def test_pattern_type_errors() -> None:
    with pytest.raises(TypeError):
        ef.local_part("first", JANE)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ef.fitting("jane", ("jane", "roe"))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ef.fitting(None, JANE)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{first}.{last}", Pattern.FIRST_DOT_LAST),
        ("{f}{last}", Pattern.FLAST),
        ("{first}", Pattern.FIRST),
        ("{first}{last}", Pattern.FIRSTLAST),
        ("{first}_{last}", Pattern.FIRST_UNDERSCORE_LAST),
        ("{last}.{first}", Pattern.LAST_DOT_FIRST),
        ("{f}.{last}", Pattern.F_DOT_LAST),
        (" {first}.{last} ", Pattern.FIRST_DOT_LAST),
        ("{last}", None),
        ("{first}-{last}", None),
        ("{l}{first}", None),
        ("first.last", None),
        ("", None),
        (None, None),
    ],
)
def test_hunter_pattern(text: str | None, expected: Pattern | None) -> None:
    assert ef.hunter_pattern(text) is expected


def test_hunter_map_covers_every_pattern() -> None:
    assert set(ef.HUNTER_PATTERNS.values()) == set(Pattern)


# Role addresses -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "role"),
    [
        ("info@roe.law", True),
        ("INFO@roe.law", True),
        ("Info+web@roe.law", True),
        ("no-reply@roe.law", True),
        ("admissions@roe.law", True),
        ("info.desk@roe.law", False),
        ("jane.roe@roe.law", False),
        ("information@roe.law", False),
        ("sales", True),
    ],
)
def test_is_role_address(email: str, role: bool) -> None:
    assert ef.is_role_address(email) is role


# Addresses in text ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Write to Jane.Roe@Roe.LAW.", ("jane.roe@roe.law",)),
        ("a@roe.law, b@roe.law and a@roe.law", ("a@roe.law", "b@roe.law")),
        ("mailto:jane@roe.law", ("jane@roe.law",)),
        ("(jane+site@roe.co.uk)", ("jane+site@roe.co.uk",)),
        ("jane [at] roe.law", ()),
        ("jane at roe dot law", ()),
        ("bad@under_score.com", ()),
        ("me@localhost", ()),
        ("", ()),
    ],
)
def test_addresses_in(text: str, expected: tuple[str, ...]) -> None:
    assert ef.addresses_in(text) == expected


# Published matches ----------------------------------------------------------------------------

LINE = "Jane Roe, Managing Partner – jane.roe@roe.law"


def test_matrix_text_line_rule_a_wins() -> None:
    text = f"Our team\n{LINE}\nCall us"
    found = matches([page(text)])
    assert len(found) == 1
    (only,) = found
    assert (only.address, only.rule, only.quote) == ("jane.roe@roe.law", A, LINE)
    assert (only.page_id, only.url, only.fetched_at) == ("p1", "https://roe.law/p1", AT)
    assert quote_in(only.quote, text)
    assert "jane" not in repr(only)


def test_matrix_mailto() -> None:
    (only,) = matches([page("Email", ("jane.roe@roe.law",))])
    assert only.rule is A
    assert only.quote == "mailto:jane.roe@roe.law"


def test_text_and_mailto_uses_text_quote() -> None:
    (only,) = matches([page(LINE, ("Jane.Roe@roe.law",))])
    assert only.quote == LINE


def test_mailto_only_after_purge() -> None:
    (only,) = matches([page(None, ("jane.roe@roe.law", "info@roe.law"))])
    assert only.quote == "mailto:jane.roe@roe.law"


def test_matrix_initial_on_another_line() -> None:
    assert matches([page("Jane Roe, Partner\njroe@roe.law")]) == ()
    assert matches([page(None, ("jroe@roe.law",))]) == ()


def test_matrix_initial_on_same_line_is_rule_b() -> None:
    (only,) = matches([page("Jane Roe, Partner: jroe@roe.law")])
    assert (only.address, only.rule) == ("jroe@roe.law", B)
    assert only.quote == "Jane Roe, Partner: jroe@roe.law"


def test_matrix_role_never() -> None:
    assert matches([page("Jane Roe: info@roe.law", ("info@roe.law",))]) == ()


def test_matrix_two_addresses_refuse_rule_b() -> None:
    assert matches([page("Jane Roe: jroe@roe.law or office2@roe.law")]) == ()
    # the same address twice is one address
    (only,) = matches([page("Jane Roe: jroe@roe.law (jroe@roe.law)")])
    assert only.rule is B
    # a role address on the line doesn't count
    (only,) = matches([page("Jane Roe: jroe@roe.law, info@roe.law")])
    assert only.address == "jroe@roe.law"


def test_two_addresses_rule_a_still_matches() -> None:
    found = matches([page("Jane Roe: jane.roe@roe.law or sam@roe.law")])
    assert [(m.address, m.rule) for m in found] == [("jane.roe@roe.law", A)]


@pytest.mark.parametrize(
    ("local", "fits"),
    [
        ("jane.roe", True),
        ("roe.jane", True),
        ("jane_roe", True),
        ("jane-roe", True),
        ("janeroe", True),
        ("roejane", True),
        ("jane.q.roe", True),
        ("Jane.Roe+web", True),
        ("jane.r", False),
        ("j.roe", False),
        ("jroe", False),
        ("jane", False),
        ("janet.roe", False),
        ("jane.roebuck", False),
    ],
)
def test_rule_a(local: str, fits: bool) -> None:
    found = matches([page(None, (f"{local}@roe.law",))])
    assert bool(found) is fits


def test_rule_a_needs_last() -> None:
    only = NameTokens("jane", None)
    assert matches([page(None, ("jane@roe.law",))], name="Jane", tokens=only) == ()
    (hit,) = matches([page("Jane: jane@roe.law")], name="Jane", tokens=only)
    assert hit.rule is B


def test_rule_b_whole_words_and_normalised_name() -> None:
    assert matches([page("Jane Roebuck: jr@roe.law")]) == ()
    assert matches([page("Mary-Jane Roe: jr@roe.law")]) != ()  # "jane roe" as whole words
    (hit,) = matches([page("JANE  ROE — jr@roe.law")])
    assert hit.rule is B
    garcia = NameTokens("jose", "garcia")
    (hit,) = matches([page("José García: jg@roe.law")], name="José García", tokens=garcia)
    assert hit.address == "jg@roe.law"


def test_ordering() -> None:
    pages = [
        page("Jane Roe: jr@other.com\nzz jane.roe@other.com", page_id="p1"),
        page("Jane Roe: roe@mail.roe.law\nJane Roe: j@roe.law", ("roe.jane@roe.law",), "p2"),
        page("roe_jane@roe.law", page_id="p3"),
    ]
    found = matches(pages)
    assert [(m.address, m.rule, m.page_id) for m in found] == [
        ("roe.jane@roe.law", A, "p2"),
        ("roe_jane@roe.law", A, "p3"),
        ("jane.roe@other.com", A, "p1"),
        ("roe@mail.roe.law", B, "p2"),
        ("j@roe.law", B, "p2"),
        ("jr@other.com", B, "p1"),
    ]
    found = matches(pages, mail_domain=None)
    assert [m.address for m in found][:3] == [
        "jane.roe@other.com",
        "roe.jane@roe.law",
        "roe_jane@roe.law",
    ]


def test_one_match_per_address_its_best() -> None:
    pages = [
        page("Jane Roe: jroe@roe.law", page_id="p1"),
        page("Jane Roe: jroe@roe.law", page_id="p2"),
        page("x jane.roe@roe.law", page_id="p3"),
        page("Jane Roe, jane.roe@roe.law", page_id="p4"),
    ]
    found = matches(pages)
    assert [(m.address, m.page_id, m.rule) for m in found] == [
        ("jane.roe@roe.law", "p3", A),
        ("jroe@roe.law", "p1", B),
    ]


def test_position_within_page() -> None:
    text = "x roe.jane@roe.law\ny jane.roe@roe.law"
    found = matches([page(text, ("jane_roe@roe.law", "jane.roe@roe.law"))])
    assert [m.address for m in found] == [
        "roe.jane@roe.law",
        "jane.roe@roe.law",
        "jane_roe@roe.law",
    ]


def test_exclude() -> None:
    pages = [page("roe.jane@roe.law\njane.roe@roe.law")]
    first = ef.published_match(JANE, "Jane Roe", pages, mail_domain="roe.law")
    assert first is not None and first.address == "roe.jane@roe.law"
    second = ef.published_match(
        JANE, "Jane Roe", pages, mail_domain="roe.law", exclude={"ROE.JANE@roe.law"}
    )
    assert second is not None and second.address == "jane.roe@roe.law"
    none = ef.published_match(
        JANE,
        "Jane Roe",
        pages,
        mail_domain="roe.law",
        exclude=("roe.jane@roe.law", "jane.roe@roe.law"),
    )
    assert none is None
    with pytest.raises(TypeError):
        ef.published_match(JANE, "Jane Roe", pages, mail_domain=None, exclude="x@roe.law")


def test_long_line_rule_a_window() -> None:
    text = "intro\n" + "a" * 400 + " jane.roe@roe.law " + "b" * 400
    (hit,) = matches([page(text)])
    assert hit.rule is A
    assert len(hit.quote) <= 300
    assert "jane.roe@roe.law" in hit.quote
    assert quote_in(hit.quote, text)


def test_long_line_rule_b_window() -> None:
    text = "x " * 300 + "Jane Roe, Partner. " + "y " * 50 + "jr@roe.law" + " z" * 300
    (hit,) = matches([page(text)])
    assert hit.rule is B
    assert len(hit.quote) <= 300
    assert "Jane Roe" in hit.quote and "jr@roe.law" in hit.quote
    assert quote_in(hit.quote, text)


def test_long_line_rule_b_too_far_apart() -> None:
    text = "Jane Roe, Partner. " + "y " * 200 + "jr@roe.law"
    assert matches([page(text)]) == ()


def test_short_line_quote_is_the_stripped_line() -> None:
    text = "head\n\t  Jane Roe  \t jr@roe.law  \nfoot"
    (hit,) = matches([page(text)])
    assert hit.quote == "Jane Roe  \t jr@roe.law"
    assert quote_in(hit.quote, text)


def test_unmatchable_name_skips_rule_b() -> None:
    assert matches([page("--: jr@roe.law")], name="--") == ()


def test_published_type_errors() -> None:
    with pytest.raises(TypeError):
        ef.published_matches("jane", "Jane", [], mail_domain=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ef.published_matches(JANE, "Jane", ["text"], mail_domain=None)  # type: ignore[list-item]
    with pytest.raises(TypeError):
        PageAddresses("p", "u", AT, None, ["a@b.com"])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        PageAddresses("p", "u", "2026", None, ())  # type: ignore[arg-type]
    assert "secret" not in repr(page("secret", ("secret@roe.law",)))


# Pattern learning -----------------------------------------------------------------------------

SAM = NameTokens("sam", "lee")
ANN = NameTokens("ann", "kay")
BOB = NameTokens("bob", "fox")


@pytest.mark.parametrize(
    ("examples", "expected"),
    [
        ([("sam.lee@roe.law", SAM), ("ann.kay@roe.law", ANN)], Pattern.FIRST_DOT_LAST),
        ([("sam.lee", SAM), ("ann.kay", ANN)], Pattern.FIRST_DOT_LAST),
        ([("sam.lee@roe.law", SAM)], None),
        ([("sam.lee@roe.law", SAM), ("akay@roe.law", ANN)], None),
        (
            [("sam.lee@roe.law", SAM), ("ann.kay@roe.law", ANN), ("bfox@roe.law", BOB)],
            Pattern.FIRST_DOT_LAST,
        ),
        (
            [
                ("sam.lee@roe.law", SAM),
                ("ann.kay@roe.law", ANN),
                ("bfox@roe.law", BOB),
                ("jroe@roe.law", JANE),
            ],
            None,
        ),
        ([("sam@roe.law", SAM), ("ann@roe.law", ANN)], Pattern.FIRST),
        ([("x@roe.law", SAM), ("y@roe.law", ANN)], None),
        ([], None),
        # one-letter first names fit two patterns each: a tie
        (
            [("jlee@roe.law", NameTokens("j", "lee")), ("akay@roe.law", NameTokens("a", "kay"))],
            None,
        ),
    ],
)
def test_learn_pattern(examples, expected) -> None:
    assert ef.learn_pattern(examples) is expected


def test_learn_pattern_type_errors() -> None:
    with pytest.raises(TypeError):
        ef.learn_pattern(["sam.lee@roe.law"])  # type: ignore[list-item]


# Provenance -----------------------------------------------------------------------------------


def test_provenance_for() -> None:
    step = ef.FinderStep
    assert ef.provenance_for(step.PUBLISHED, page_url="https://roe.law/team") == ef.ProvenanceFor(
        ProvenanceSource.WEBSITE, "https://roe.law/team"
    )
    learned = ef.provenance_for(
        step.PATTERN, pattern=Pattern.FIRST_DOT_LAST, pattern_source=PatternSource.PUBLISHED
    )
    assert learned == ef.ProvenanceFor(
        ProvenanceSource.PATTERN, "first.last pattern learned from published addresses"
    )
    hunted = ef.provenance_for(
        step.PATTERN, pattern=Pattern.FLAST, pattern_source=PatternSource.HUNTER_DOMAIN_SEARCH
    )
    assert hunted.detail == "flast pattern from Hunter Domain Search"
    for finder, detail in [
        (step.HUNTER_DOMAIN_SEARCH, "Hunter Domain Search"),
        (step.HUNTER, "Hunter Email Finder"),
        (step.PROSPEO, "Prospeo"),
    ]:
        assert ef.provenance_for(finder) == ef.ProvenanceFor(ProvenanceSource.FINDER, detail)


def test_provenance_for_missing_arguments() -> None:
    with pytest.raises(ValueError):
        ef.provenance_for(ef.FinderStep.PUBLISHED)
    with pytest.raises(ValueError):
        ef.provenance_for(ef.FinderStep.PATTERN, pattern=Pattern.FIRST)
    with pytest.raises(ValueError):
        ef.provenance_for(ef.FinderStep.PATTERN, pattern_source=PatternSource.PUBLISHED)
    with pytest.raises(TypeError):
        ef.provenance_for("published", page_url="u")  # type: ignore[arg-type]


def test_enums() -> None:
    assert [o.value for o in ef.EmailSearchOutcome] == [
        "found",
        "not_found",
        "quota_reached",
        "suppressed",
        "lead_closed",
    ]
    assert [s.value for s in ef.FinderStep] == [
        "published",
        "pattern",
        "hunter_domain_search",
        "hunter",
        "prospeo",
    ]
    assert [s.value for s in PatternSource] == ["published", "hunter_domain_search"]


# Tried providers ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, ()),
        ("", ()),
        ("hunter", ("hunter",)),
        ("hunter,prospeo", ("hunter", "prospeo")),
        (" hunter , ,prospeo,hunter ", ("hunter", "prospeo")),
    ],
)
def test_tried_of(text: str | None, expected: tuple[str, ...]) -> None:
    assert ef.tried_of(text) == expected


def test_tried_text() -> None:
    assert ef.tried_text(()) is None
    assert ef.tried_text(["hunter"]) == "hunter"
    assert ef.tried_text(["hunter", "prospeo", "hunter"]) == "hunter,prospeo"
    assert ef.tried_of(ef.tried_text(["hunter", "prospeo"])) == ("hunter", "prospeo")
    with pytest.raises(ValueError):
        ef.tried_text(["apollo"])
    with pytest.raises(TypeError):
        ef.tried_text("hunter")
