"""``nextron.adapters.email_syntax`` (FR-15, FR-59): the real email-validator adapter on valid and
invalid addresses, offline (the network guard would raise on any DNS lookup), and the fake."""

import socket

import pytest

from nextron.adapters.email_syntax import EmailValidatorSyntax
from nextron.ports.email_syntax import EmailSyntaxPort
from tests.fakes.email_syntax import FakeEmailSyntax


@pytest.mark.parametrize(
    "address",
    [
        "jane@smith-law.co.uk",
        "jane.doe+x@example.com",
        "j@xn--bcher-kva.de",
        "jöse@bücher.de",
        "a_b-c@firm-name.law",
    ],
)
def test_valid_addresses(address: str) -> None:
    assert EmailValidatorSyntax().is_valid(address)


@pytest.mark.parametrize(
    "address",
    [
        "a,b@firm.com",
        "a;b@firm.com",
        "jane",
        "@firm.com",
        "jane@",
        "jane@@firm.com",
        "jane@firm",  # one label
        "jane@example.test",  # a special-use domain
        "jane@firm.local",
        "jane@localhost",
        '"jane doe"@firm.com',  # a quoted local part
        "jane@[192.0.2.1]",  # a domain literal
        "Jane <jane@firm.com>",  # a display name
        "jane..doe@firm.com",
        "jane doe@firm.com",
        "jane@-firm.com",
    ],
)
def test_invalid_addresses(address: str) -> None:
    assert not EmailValidatorSyntax().is_valid(address)


def test_it_makes_no_dns_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a DNS lookup was made")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "gethostbyname", refuse)

    assert EmailValidatorSyntax().is_valid("jane@no-such-domain-anywhere-zq.com")


@pytest.mark.parametrize("value", [None, b"jane@firm.com", 7])
def test_a_non_str_raises_type_error(value: object) -> None:
    with pytest.raises(TypeError):
        EmailValidatorSyntax().is_valid(value)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        FakeEmailSyntax().is_valid(value)  # type: ignore[arg-type]


def test_both_satisfy_the_port() -> None:
    assert isinstance(EmailValidatorSyntax(), EmailSyntaxPort)
    assert isinstance(FakeEmailSyntax(), EmailSyntaxPort)


def test_the_fake_refuses_listed_addresses_and_odd_characters() -> None:
    fake = FakeEmailSyntax(refused=["no@firm.com"])

    assert fake.is_valid("jane@firm.com")
    assert not fake.is_valid("no@firm.com")
    assert not any(fake.is_valid(f"a{ch}b@firm.com") for ch in ',;<>()[]\\" ')
    assert fake.checked[:2] == ["jane@firm.com", "no@firm.com"]
