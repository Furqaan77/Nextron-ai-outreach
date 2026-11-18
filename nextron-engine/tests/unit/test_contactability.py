"""Suppression keys (``nextron.domain.contactability``, FR-50, FR-63, AD-16, Story 3.3): the AC's
three hash cases; every kind's normalised value and exact SHA-256; the person pair; the argument
errors (unknown kind ``ValueError``, wrong value type ``TypeError``, ``IdentityError``
propagated); ``suppression_key`` and ``SuppressionKey``'s checks; ``ContactSubject``; the key-set
rules of ``person_keys`` and ``firm_domain_keys`` (order, partial keys, values that can't be
normalised, free webmail never a key); and Story 3.5's ``AddressSubject`` and ``DomainSubject``
(normalised values only, no free-webmail domain or shared platform, no value in a message or
the repr)."""

import hashlib

import pytest

from nextron.domain.contactability import (
    HASH_LENGTH,
    AddressSubject,
    ContactSubject,
    DomainSubject,
    KeyKind,
    SuppressionKey,
    SuppressionScope,
    SuppressionSubject,
    firm_domain_keys,
    is_hash,
    key_hash,
    normalised_key,
    person_keys,
    suppression_key,
)
from nextron.domain.identity import IdentityError

ULID = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
EMAIL = "jane.doe@firm.co.uk"
LINKEDIN = "https://www.linkedin.com/in/jane-doe"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# The AC's three cases ---------------------------------------------------------------------------


def test_an_address_hashes_the_same_whatever_its_case_and_spacing() -> None:
    assert key_hash("address", "Jane.Doe@Firm.co.uk") == key_hash("address", " jane.doe@firm.co.uk")


def test_a_www_domain_hashes_the_same_as_the_registrable_domain() -> None:
    assert key_hash("domain", "www.firm.co.uk") == key_hash("domain", "firm.co.uk")


def test_plus_tags_and_dots_are_not_stripped() -> None:
    assert key_hash("address", "jane.doe+x@firm.com") != key_hash("address", "jane.doe@firm.com")
    assert key_hash("address", "janedoe@firm.com") != key_hash("address", "jane.doe@firm.com")


# Every kind -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "value", "normalised"),
    [
        ("address", " Jane.Doe+News@Firm.CO.UK ", "jane.doe+news@firm.co.uk"),
        ("domain", "https://WWW.Firm.co.uk/team", "firm.co.uk"),
        ("domain", "mail.firm.co.uk", "firm.co.uk"),
        ("person", ("Jane  DOE", "https://www.firm.co.uk/"), "jane doe|firm.co.uk"),
        ("profile", "linkedin.com/in/Jane-Doe/?trk=x", LINKEDIN),
    ],
)
def test_each_kind_normalises_through_identity_and_hashes_kind_colon_value(
    kind: str, value: str | tuple[str, str], normalised: str
) -> None:
    assert normalised_key(kind, value) == normalised
    assert key_hash(kind, value) == sha256(f"{kind}:{normalised}")
    assert key_hash(KeyKind(kind), value) == key_hash(kind, value)


def test_the_hash_is_64_lowercase_hex_and_three_8_decodes_32_bytes() -> None:
    found = key_hash("address", EMAIL)

    assert len(found) == HASH_LENGTH == 64
    assert found == found.lower()
    assert is_hash(found)
    assert len(bytes.fromhex(found)) == 32  # Story 3.8's addr_hash


def test_the_kind_is_part_of_the_hash() -> None:
    assert key_hash("domain", "firm.co.uk") == sha256("domain:firm.co.uk")
    assert key_hash("domain", "firm.co.uk") != sha256("firm.co.uk")
    assert key_hash("domain", "firm.co.uk") != sha256("address:firm.co.uk")


def test_a_person_pair_cannot_be_split_wrongly() -> None:
    # "|" in a name folds to a space, so it can't shift the name's end into the domain
    assert normalised_key("person", ("Jane|Doe", "firm.co.uk")) == "jane doe|firm.co.uk"
    with pytest.raises(IdentityError):
        normalised_key("person", ("Jane", "Doe|firm.co.uk"))


def test_the_same_person_at_another_firm_is_another_key() -> None:
    assert key_hash("person", ("Jane Doe", "firm.co.uk")) != key_hash(
        "person", ("Jane Doe", "other.co.uk")
    )


# Argument errors --------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["email", "Address", "", None, 3, ("address",)])
def test_an_unknown_kind_raises_value_error(kind: object) -> None:
    with pytest.raises(ValueError, match="kind must be"):
        key_hash(kind, EMAIL)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="kind must be"):
        normalised_key(kind, EMAIL)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("person", "Jane Doe|firm.co.uk"),
        ("person", ["Jane Doe", "firm.co.uk"]),
        ("person", ("Jane Doe",)),
        ("person", ("Jane Doe", "firm.co.uk", "x")),
        ("person", (None, "firm.co.uk")),
        ("person", ("Jane Doe", None)),
        ("address", (EMAIL, "firm.co.uk")),
        ("domain", ("firm.co.uk", "x")),
        ("profile", (LINKEDIN, "x")),
        ("address", None),
        ("domain", 42),
        ("profile", b"https://www.linkedin.com/in/x"),
    ],
)
def test_a_wrong_value_type_raises_type_error(kind: str, value: object) -> None:
    with pytest.raises(TypeError):
        key_hash(kind, value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("address", "not an address"),
        ("address", "jane@localhost"),
        ("domain", "co.uk"),
        ("domain", "ftp://firm.co.uk"),
        ("person", ("!!!", "firm.co.uk")),
        ("person", ("Jane Doe", "co.uk")),
        ("profile", "https://example.com/in/jane"),
        ("profile", "https://www.linkedin.com/company/firm"),
    ],
)
def test_a_value_identity_refuses_raises_its_identity_error(
    kind: str, value: str | tuple[str, str]
) -> None:
    with pytest.raises(IdentityError):
        key_hash(kind, value)


def test_an_identity_error_never_repeats_the_value() -> None:
    with pytest.raises(IdentityError) as caught:
        key_hash("address", "secret-person@@firm.co.uk")

    assert "secret" not in str(caught.value)


# suppression_key and SuppressionKey -------------------------------------------------------------


def test_suppression_key_keeps_the_plain_domain_only_for_the_domain_kind() -> None:
    assert suppression_key("domain", "www.Firm.co.uk") == SuppressionKey(
        KeyKind.DOMAIN, key_hash("domain", "firm.co.uk"), "firm.co.uk"
    )
    assert suppression_key("address", EMAIL) == SuppressionKey(
        KeyKind.ADDRESS, key_hash("address", EMAIL), None
    )
    assert suppression_key("person", ("Jane Doe", "firm.co.uk")).domain_plain is None
    assert suppression_key("profile", LINKEDIN).domain_plain is None


@pytest.mark.parametrize(
    ("kind", "value_hash", "domain_plain", "error"),
    [
        ("address", "a" * 64, None, TypeError),
        (KeyKind.ADDRESS, "A" * 64, None, ValueError),
        (KeyKind.ADDRESS, "a" * 63, None, ValueError),
        (KeyKind.ADDRESS, "g" * 64, None, ValueError),
        (KeyKind.ADDRESS, None, None, ValueError),
        (KeyKind.ADDRESS, "a" * 64, "firm.co.uk", ValueError),
        (KeyKind.DOMAIN, "a" * 64, None, ValueError),
        (KeyKind.DOMAIN, "a" * 64, 5, TypeError),
    ],
)
def test_a_suppression_key_refuses_a_bad_field(
    kind: object, value_hash: object, domain_plain: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        SuppressionKey(kind, value_hash, domain_plain)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["a" * 64, "0123456789abcdef" * 4])
def test_is_hash_accepts_64_lowercase_hex(value: str) -> None:
    assert is_hash(value)


@pytest.mark.parametrize("value", ["a" * 63, "a" * 65, "A" * 64, "z" * 64, None, 1, b"a" * 64])
def test_is_hash_refuses_anything_else(value: object) -> None:
    assert not is_hash(value)


# The subject and the scope ----------------------------------------------------------------------


def test_a_contact_subject_holds_a_ulid() -> None:
    assert ContactSubject(ULID).contact_id == ULID


@pytest.mark.parametrize("value", ["", "not-a-ulid", ULID.lower(), None, 7, "8" + ULID[1:]])
def test_a_contact_subject_refuses_anything_but_a_ulid(value: object) -> None:
    with pytest.raises(ValueError) as caught:
        ContactSubject(value)  # type: ignore[arg-type]

    assert str(caught.value) == "ContactSubject.contact_id must be a ULID"  # never the value


def test_the_kinds_and_scopes_are_closed() -> None:
    assert [str(kind) for kind in KeyKind] == ["address", "domain", "person", "profile"]
    assert [str(scope) for scope in SuppressionScope] == ["person", "firm"]


# person_keys ------------------------------------------------------------------------------------


def keys_of(*pairs: tuple[str, str | tuple[str, str]]) -> tuple[SuppressionKey, ...]:
    return tuple(suppression_key(kind, value) for kind, value in pairs)


def test_person_keys_gives_address_then_person_then_profile() -> None:
    found = person_keys(
        email=EMAIL, name="Jane Doe", linkedin_url=LINKEDIN, web_domain="firm.co.uk"
    )

    assert found == keys_of(
        ("address", EMAIL), ("person", ("Jane Doe", "firm.co.uk")), ("profile", LINKEDIN)
    )
    assert [key.kind for key in found] == [KeyKind.ADDRESS, KeyKind.PERSON, KeyKind.PROFILE]


@pytest.mark.parametrize(
    ("known", "kinds"),
    [
        ({"email": EMAIL}, ["address"]),
        ({"name": "Jane Doe"}, []),
        ({"web_domain": "firm.co.uk"}, []),
        ({"name": "Jane Doe", "web_domain": "firm.co.uk"}, ["person"]),
        ({"linkedin_url": LINKEDIN}, ["profile"]),
        ({"email": EMAIL, "linkedin_url": LINKEDIN}, ["address", "profile"]),
        ({"email": EMAIL, "name": "Jane Doe"}, ["address"]),
        ({}, []),
    ],
)
def test_person_keys_gives_only_the_known_keys(known: dict[str, str], kinds: list[str]) -> None:
    arguments: dict[str, str | None] = dict.fromkeys(
        ("email", "name", "linkedin_url", "web_domain")
    )
    arguments.update(known)

    found = person_keys(**arguments)  # type: ignore[arg-type]

    assert [str(key.kind) for key in found] == kinds


@pytest.mark.parametrize(
    ("bad", "kinds"),
    [
        ({"email": "not an address"}, ["person", "profile"]),
        ({"name": "!!!"}, ["address", "profile"]),
        ({"web_domain": "co.uk"}, ["address", "profile"]),
        ({"linkedin_url": "https://example.com/in/jane"}, ["address", "person"]),
    ],
)
def test_a_value_that_cannot_be_normalised_is_no_key_and_never_raises(
    bad: dict[str, str], kinds: list[str]
) -> None:
    arguments = {
        "email": EMAIL,
        "name": "Jane Doe",
        "linkedin_url": LINKEDIN,
        "web_domain": "firm.co.uk",
    } | bad

    found = person_keys(**arguments)

    assert [str(key.kind) for key in found] == kinds


def test_person_keys_hold_only_hashes() -> None:
    found = person_keys(
        email=EMAIL, name="Jane Doe", linkedin_url=LINKEDIN, web_domain="firm.co.uk"
    )

    text = repr(found)
    for plain in ("jane", "doe", "firm", "linkedin"):
        assert plain not in text


# firm_domain_keys -------------------------------------------------------------------------------


def domains(found: tuple[SuppressionKey, ...]) -> list[str | None]:
    assert all(key.kind is KeyKind.DOMAIN for key in found)
    assert all(key.value_hash == key_hash("domain", str(key.domain_plain)) for key in found)
    return [key.domain_plain for key in found]


@pytest.mark.parametrize(
    ("email", "web_domain", "expected"),
    [
        ("jane@firm.co.uk", "firm.co.uk", ["firm.co.uk"]),
        ("jane@mail.firm.co.uk", "firm.co.uk", ["firm.co.uk"]),
        ("jane@firm-mail.com", "firm.co.uk", ["firm-mail.com", "firm.co.uk"]),
        ("jane@gmail.com", "firm.co.uk", ["firm.co.uk"]),
        ("jane@mail.gmail.com", "firm.co.uk", ["firm.co.uk"]),
        ("jane@HOTMAIL.co.uk", "firm.co.uk", ["firm.co.uk"]),
        ("bob@comcast.net", "firm.co.uk", ["firm.co.uk"]),  # a US ISP mailbox domain
        ("bob@nc.rr.com", None, []),  # a subdomain of one
        ("jane@firm.co.uk", "gmail.com", ["firm.co.uk"]),
        ("jane@gmail.com", "yahoo.com", []),
        (None, "firm.co.uk", ["firm.co.uk"]),
        ("jane@firm.co.uk", None, ["firm.co.uk"]),
        (None, None, []),
        ("not an address", "firm.co.uk", ["firm.co.uk"]),
        ("jane@firm.co.uk", "co.uk", ["firm.co.uk"]),
        ("jane@notgmail.com", None, ["notgmail.com"]),
    ],
)
def test_firm_domain_keys_follow_the_address_then_a_differing_web_domain(
    email: str | None, web_domain: str | None, expected: list[str]
) -> None:
    assert domains(firm_domain_keys(email=email, web_domain=web_domain)) == expected


def test_firm_domain_keys_take_only_keyword_strings() -> None:
    with pytest.raises(TypeError):
        firm_domain_keys(email=3, web_domain=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        firm_domain_keys("jane@firm.co.uk", "firm.co.uk")  # type: ignore[misc]


# Story 3.5: the typed subjects ------------------------------------------------------------------


def test_an_address_subject_takes_a_normalised_address_and_hides_it() -> None:
    subject = AddressSubject("jane.doe+x@firm.co.uk")

    assert subject.address == "jane.doe+x@firm.co.uk"
    assert "jane" not in repr(subject)
    assert isinstance(subject, SuppressionSubject)


@pytest.mark.parametrize(
    "value",
    [
        "Jane.Doe@firm.co.uk",  # not lowercased
        " jane@firm.co.uk",  # not stripped
        "jane@Firm.co.uk",
        "jane@",
        "jane",
        "",
        "jane@@firm.co.uk",
        "jane doe@firm.co.uk",
    ],
)
def test_an_address_subject_refuses_a_value_that_is_not_normalised(value: str) -> None:
    with pytest.raises(ValueError) as caught:
        AddressSubject(value)

    assert str(caught.value) == "AddressSubject.address must be a normalised email address"


@pytest.mark.parametrize("value", [None, 3, b"jane@firm.co.uk", ("jane@firm.co.uk",)])
def test_an_address_subject_takes_only_a_str(value: object) -> None:
    with pytest.raises(TypeError) as caught:
        AddressSubject(value)  # type: ignore[arg-type]

    assert "firm" not in str(caught.value)


def test_a_domain_subject_takes_a_registrable_domain() -> None:
    subject = DomainSubject("firm.co.uk")

    assert subject.domain == "firm.co.uk"
    assert isinstance(subject, SuppressionSubject)
    assert DomainSubject("xn--bcher-kva.de").domain == "xn--bcher-kva.de"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("www.firm.co.uk", "DomainSubject.domain must be a registrable domain"),
        ("mail.firm.co.uk", "DomainSubject.domain must be a registrable domain"),
        ("Firm.co.uk", "DomainSubject.domain must be a registrable domain"),
        ("https://firm.co.uk", "DomainSubject.domain must be a registrable domain"),
        ("co.uk", "DomainSubject.domain must be a registrable domain"),
        ("firm", "DomainSubject.domain must be a registrable domain"),
        ("", "DomainSubject.domain must be a registrable domain"),
        ("gmail.com", "DomainSubject.domain must not be a free webmail domain"),
        ("yahoo.co.uk", "DomainSubject.domain must not be a free webmail domain"),
        ("comcast.net", "DomainSubject.domain must not be a free webmail domain"),
        ("rr.com", "DomainSubject.domain must not be a free webmail domain"),
        ("facebook.com", "DomainSubject.domain must not be a shared platform"),
        ("facebook.co.uk", "DomainSubject.domain must not be a shared platform"),
        ("google.com", "DomainSubject.domain must not be a shared platform"),
        ("lawsociety.org.uk", "DomainSubject.domain must not be a shared platform"),
        ("wordpress.com", "DomainSubject.domain must not be a shared platform"),
    ],
)
def test_a_domain_subject_refuses_anything_else_and_every_free_webmail_domain(
    value: str, message: str
) -> None:
    with pytest.raises(ValueError) as caught:
        DomainSubject(value)

    assert str(caught.value) == message


@pytest.mark.parametrize("value", [None, 3, b"firm.co.uk"])
def test_a_domain_subject_takes_only_a_str(value: object) -> None:
    with pytest.raises(TypeError):
        DomainSubject(value)  # type: ignore[arg-type]


def test_the_contact_subject_is_a_suppression_subject_too() -> None:
    assert isinstance(ContactSubject(ULID), SuppressionSubject)
    assert not isinstance("jane@firm.co.uk", SuppressionSubject)
