"""``nextron.adapters.dns.DnspythonDns`` (Story 6.5, AD-9), offline: a recording resolver
answers with ``dns.rdata.from_text`` rdatas and raises real dnspython exceptions. TXT strings
are joined and sorted, MX records sorted with the null MX as ``""``; NXDOMAIN and NODATA answer
``()``; each error code; one metered call per query and none for a refused name; nothing built
until the first query, then kept; a built resolver rebuilt after a retryable failure (an
injected one kept); a resolver that can't be built retryable; and the guard refusing
``default_resolver``."""

from collections.abc import Iterable
from typing import Any

import dns.exception
import dns.message
import dns.name
import dns.rdata
import dns.rdatatype
import dns.resolver
import pytest

import nextron.adapters.dns as dns_adapter
from nextron.adapters.dns import DnspythonDns
from nextron.domain.budget import ApiCall
from nextron.ports.dns import DnsPort, MxRecord, is_dns_name
from nextron.ports.errors import AdapterError
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import LiveAdapterInTests

DOMAIN = "nextron-ai.com"


def txt(*texts: str) -> list[Any]:
    """TXT rdatas from zone-file text (each a quoted string, or several)."""
    return [dns.rdata.from_text("IN", "TXT", text) for text in texts]


def mx(*texts: str) -> list[Any]:
    return [dns.rdata.from_text("IN", "MX", text) for text in texts]


class RecordingResolver:
    """Answers ``resolve`` from ``answers`` by ``(name, type)``: a list of rdatas is returned,
    an exception raised; records every call."""

    def __init__(self, answers: dict[tuple[str, str], Any] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[tuple[dns.name.Name, Any, float | None, bool | None]] = []

    def resolve(
        self, qname: dns.name.Name, rdtype: Any, *, lifetime: float | None, search: bool | None
    ) -> Iterable[Any]:
        self.calls.append((qname, rdtype, lifetime, search))
        found = self.answers[(qname.to_text(omit_final_dot=True), dns.rdatatype.to_text(rdtype))]
        if isinstance(found, BaseException):
            raise found
        return found


def adapter(
    answers: dict[tuple[str, str], Any] | None = None, lifetime: float = 5.0
) -> tuple[DnspythonDns, RecordingResolver, InMemoryUsageMeter]:
    resolver = RecordingResolver(answers)
    meter = InMemoryUsageMeter()
    fake: Any = resolver
    return DnspythonDns(meter=meter, resolver=fake, lifetime=lifetime), resolver, meter


def ok(operation: str) -> ApiCall:
    return ApiCall("dns", operation, ok=True)


def failed(operation: str, code: str) -> ApiCall:
    return ApiCall("dns", operation, ok=False, error_code=code)


# Answers ----------------------------------------------------------------------------------------


def test_it_satisfies_the_port() -> None:
    assert isinstance(adapter()[0], DnsPort)


def test_txt_joins_each_records_strings_and_sorts_the_records() -> None:
    answers = {
        (DOMAIN, "TXT"): txt(
            '"v=spf1 " "include:_spf.google.com ~all"',
            '"google-site-verification=abc"',
            '"caf\\195\\169" "\\255x"',  # UTF-8, then a byte that isn't: replaced
        )
    }
    dns_port, resolver, meter = adapter(answers, lifetime=2.5)

    found = dns_port.txt(DOMAIN)

    assert found == (
        "café�x",
        "google-site-verification=abc",
        "v=spf1 include:_spf.google.com ~all",
    )
    [(qname, rdtype, lifetime, search)] = resolver.calls
    assert qname == dns.name.from_text(DOMAIN)
    assert qname.is_absolute()
    assert (rdtype, lifetime, search) == (dns.rdatatype.TXT, 2.5, False)
    assert meter.calls == [ok("txt")]


def test_mx_is_sorted_lower_cased_without_the_final_dot_and_a_null_mx_is_empty() -> None:
    answers = {
        (DOMAIN, "MX"): mx("20 Alt1.ASPMX.l.google.com.", "10 b.example.net.", "10 A.example.net."),
        ("null.example", "MX"): mx("0 ."),
    }
    dns_port, resolver, meter = adapter(answers)

    assert dns_port.mx(DOMAIN) == (
        MxRecord(10, "a.example.net"),
        MxRecord(10, "b.example.net"),
        MxRecord(20, "alt1.aspmx.l.google.com"),
    )
    assert dns_port.mx("null.example.") == (MxRecord(0, ""),)
    assert [call[1] for call in resolver.calls] == [dns.rdatatype.MX, dns.rdatatype.MX]
    assert meter.calls == [ok("mx"), ok("mx")]


@pytest.mark.parametrize(
    "error", [dns.resolver.NXDOMAIN(), dns.resolver.NoAnswer()], ids=["nxdomain", "nodata"]
)
def test_nxdomain_and_nodata_answer_nothing_and_are_metered_as_ok(
    error: dns.exception.DNSException,
) -> None:
    dns_port, _, meter = adapter({(DOMAIN, "TXT"): error, (DOMAIN, "MX"): error})

    assert (dns_port.txt(DOMAIN), dns_port.mx(DOMAIN)) == ((), ())
    assert meter.calls == [ok("txt"), ok("mx")]


# Errors -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (dns.resolver.LifetimeTimeout(timeout=5.0, errors=[]), "dns_timeout", True),
        (dns.exception.Timeout(), "dns_timeout", True),
        (
            dns.resolver.NoNameservers(
                request=dns.message.make_query(DOMAIN, "TXT"), errors=[]
            ),
            "dns_unavailable",
            True,
        ),
        (OSError("network unreachable"), "dns_unavailable", True),
        (dns.resolver.YXDOMAIN(), "dns_failed", False),
        (dns.exception.SyntaxError(), "dns_failed", False),
    ],
    ids=["lifetime", "timeout", "no_nameservers", "os_error", "yxdomain", "syntax"],
)
def test_each_failure_is_an_adapter_error_with_its_code_and_metered(
    error: BaseException, code: str, retryable: bool
) -> None:
    dns_port, resolver, meter = adapter({(DOMAIN, "TXT"): error})

    with pytest.raises(AdapterError) as caught:
        dns_port.txt(DOMAIN)

    assert (caught.value.code, caught.value.retryable) == (code, retryable)
    assert DOMAIN not in str(caught.value)  # the adapter's own text, never the name
    assert caught.value.__cause__ is None
    assert len(resolver.calls) == 1  # exactly one query: the adapter never retries
    assert meter.calls == [failed("txt", code)]


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "a..b",
        ".leading.com",
        "café.com",
        "white space.com",
        "star*.com",
        "x" * 64 + ".com",
        ("a" * 63 + ".") * 4 + "com",  # 256 characters
        "under\nline.com",
    ],
)
def test_a_name_that_isnt_a_dns_name_is_refused_before_any_query(name: str) -> None:
    dns_port, resolver, meter = adapter()

    with pytest.raises(AdapterError) as caught:
        dns_port.txt(name)
    with pytest.raises(AdapterError):
        dns_port.mx(name)

    assert (caught.value.code, caught.value.retryable) == ("dns_invalid_name", False)
    assert (resolver.calls, meter.calls) == ([], [])


@pytest.mark.parametrize("name", [None, 7, b"example.com"])
def test_a_name_that_isnt_a_str_raises_type_error(name: object) -> None:
    dns_port, resolver, meter = adapter()

    with pytest.raises(TypeError):
        dns_port.txt(name)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        dns_port.mx(name)  # type: ignore[arg-type]

    assert (resolver.calls, meter.calls) == ([], [])


@pytest.mark.parametrize(
    "name",
    [
        "google._domainkey.nextron-ai.com",
        "_dmarc.nextron-ai.com.",
        "x" * 63 + ".com",
        ("a" * 62 + ".") * 4 + "abc",  # 255 characters: over 253 without the dot
    ],
)
def test_the_name_rule_takes_underscores_a_final_dot_and_up_to_253_characters(name: str) -> None:
    expected = len(name.removesuffix(".")) <= 253
    assert is_dns_name(name) is expected


# Building ---------------------------------------------------------------------------------------


def test_nothing_is_built_until_the_first_query_then_the_resolver_is_built_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = RecordingResolver({(DOMAIN, "TXT"): txt('"a"'), (DOMAIN, "MX"): mx("10 m.x.")})
    built: list[RecordingResolver] = []

    def default_resolver() -> RecordingResolver:
        built.append(resolver)
        return resolver

    monkeypatch.setattr(dns_adapter, "default_resolver", default_resolver)
    meter = InMemoryUsageMeter()

    dns_port = DnspythonDns(meter=meter)
    assert built == []
    with pytest.raises(AdapterError):
        dns_port.txt("bad name")  # a refused name builds nothing either
    assert built == []

    dns_port.txt(DOMAIN)
    dns_port.mx(DOMAIN)

    assert built == [resolver]
    assert len(resolver.calls) == 2


@pytest.mark.parametrize(
    "error", [dns.resolver.NoResolverConfiguration(), OSError("no network yet")]
)
def test_a_resolver_that_cant_be_built_is_retryable_unmetered_and_not_kept(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    """At logon the network may not be up yet: the job retries, and the next query tries to
    build the resolver again."""
    attempts: list[int] = []
    resolver = RecordingResolver({(DOMAIN, "TXT"): txt('"a"')})

    def default_resolver() -> Any:
        attempts.append(1)
        if len(attempts) == 1:
            raise error
        return resolver

    monkeypatch.setattr(dns_adapter, "default_resolver", default_resolver)
    meter = InMemoryUsageMeter()
    dns_port = DnspythonDns(meter=meter)

    with pytest.raises(AdapterError) as caught:
        dns_port.txt(DOMAIN)

    assert (caught.value.code, caught.value.retryable) == ("dns_unavailable", True)
    assert meter.calls == []
    assert dns_port.txt(DOMAIN) == ("a",)  # built again on the next query
    assert (len(attempts), meter.calls) == (2, [ok("txt")])


@pytest.mark.parametrize(
    "error",
    [
        dns.resolver.LifetimeTimeout(timeout=5.0, errors=[]),
        dns.resolver.NoNameservers(),
        OSError("network unreachable"),
    ],
    ids=["timeout", "no_nameservers", "os_error"],
)
def test_after_a_retryable_failure_a_built_resolver_is_replaced_on_the_next_query(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    """The PC may have changed network or VPN: the next query reads the configuration again."""
    stale = RecordingResolver({(DOMAIN, "TXT"): error})
    fresh = RecordingResolver({(DOMAIN, "TXT"): txt('"a"')})
    built = [stale, fresh]
    monkeypatch.setattr(dns_adapter, "default_resolver", lambda: built.pop(0))
    dns_port = DnspythonDns(meter=InMemoryUsageMeter())

    with pytest.raises(AdapterError):
        dns_port.txt(DOMAIN)
    assert dns_port.txt(DOMAIN) == ("a",)
    assert dns_port.txt(DOMAIN) == ("a",)  # the fresh one is kept while it works

    assert (len(stale.calls), len(fresh.calls), built) == (1, 2, [])


def test_a_non_retryable_failure_keeps_the_built_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = RecordingResolver({(DOMAIN, "TXT"): dns.resolver.YXDOMAIN()})
    built: list[RecordingResolver] = []

    def default_resolver() -> RecordingResolver:
        built.append(resolver)
        return resolver

    monkeypatch.setattr(dns_adapter, "default_resolver", default_resolver)
    dns_port = DnspythonDns(meter=InMemoryUsageMeter())

    for _ in range(2):
        with pytest.raises(AdapterError):
            dns_port.txt(DOMAIN)

    assert (built, len(resolver.calls)) == ([resolver], 2)


def test_an_injected_resolver_is_kept_after_a_retryable_failure() -> None:
    dns_port, resolver, meter = adapter({(DOMAIN, "TXT"): dns.exception.Timeout()})

    for _ in range(2):  # the guard would refuse a rebuild through default_resolver
        with pytest.raises(AdapterError) as caught:
            dns_port.txt(DOMAIN)
        assert caught.value.code == "dns_timeout"

    assert len(resolver.calls) == 2
    assert meter.calls == [failed("txt", "dns_timeout")] * 2


def test_the_guard_refuses_the_live_resolver_and_nothing_is_metered() -> None:
    meter = InMemoryUsageMeter()

    with pytest.raises(LiveAdapterInTests):
        dns_adapter.default_resolver()
    with pytest.raises(LiveAdapterInTests):
        DnspythonDns(meter=meter).txt(DOMAIN)  # its first query looks the resolver up

    assert meter.calls == []
