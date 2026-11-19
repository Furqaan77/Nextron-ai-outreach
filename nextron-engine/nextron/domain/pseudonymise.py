"""Pseudonymise a Reply's text before it joins the Reply set (FR-40, FR-73, AD-12, AD-17, Story
4.6). Standard library only.

``pseudonymise(text, *, names=(), organisations=()) -> str`` is what
``Playbook.add_override_case`` stores of an Operator's class override: the Reply's own words
with the personal details replaced by placeholders. It **over-replaces rather than
under-replaces** (a placeholder where none was needed costs the classifier little; a name left
in costs privacy), and it is **idempotent**: pseudonymising its own output changes nothing.

0. CRLF and CR line ends become LF, and no-break, figure and narrow no-break spaces become
   plain spaces, so a number or postcode split by one is still found.
1. **Quoted history is dropped**: every line starting with ``>`` (after any indent), and
   everything from the first history start onwards: an ``On … wrote:`` line (the ``wrote:`` may
   end the next line, as mail clients wrap it), ``-----Original Message-----``, ``----------
   Forwarded message ---------``, a ``From:`` line followed by a ``Sent:`` or ``Date:`` line (an
   Outlook or Gmail header block), or a line of 10 or more underscores.
2. Then, in this order, each kind is replaced by its placeholder; text already a placeholder is
   never touched again:

   - email addresses, Unicode letters included (``josé@…``): ``[email]``;
   - URLs (``http://``, ``https://``, ``www.``) and bare domain names: ``[url]``;
   - phone numbers, any run of 7 or more digits with spaces, dots, hyphens, en or em dashes,
     slashes or brackets between them (a leading ``+`` kept with it), unless the run is a date
     (``2026-11-16``, ``16/11/2026``, ``16.11.26``…): ``[phone]``;
   - postal addresses: UK postcodes, US street lines (a house number, capitalised words and a
     street suffix, with any suite), a ZIP code after a state name or its two-letter code, and
     PO boxes: ``[address]``;
   - organisations: each of ``organisations`` (case-insensitive, as whole words), and every
     capitalised run of words ending in a company suffix (``COMPANY_SUFFIXES``: Ltd, Limited,
     LLP, LLC, Inc, PLC, Solicitors, Partners, Group, & Co, …), less any allowed words that
     start the run ("Thanks Hale Ltd" keeps "Thanks"; a dot after the suffix stays):
     ``[organisation]``;
   - names, as ``[name]``:

     - each of ``names`` whole and each of its parts, in any case, as whole words, with any
       ``'s`` (``dana's``);
     - an honorific and the words after it: ``mr``, ``mrs``, ``ms``, ``mx``, ``dr`` and ``prof``
       in any case, ``Miss``, ``Sir``, ``Dame``, ``Lord`` and ``Lady`` capitalised, and the next
       word in any case (``ask mr jones``; not "or", "and", "the", "to" or "of");
     - the word after a greeting at a true line start, when punctuation or the line's end
       follows it and it may be a name: never a lowercase, all-capitals, allowed or common word
       (``Hello, unsubscribe.``, ``Hi STOP!`` and ``Hi there,`` stay);
     - the whole line after a line that is only a sign-off (``Thanks,`` then ``Jane Smith``,
       blank lines between allowed) when it is 1 to 4 capitalised words, one of them a possible
       name, or all in capitals with none allowed (``REGARDS,`` then ``JANE SMITH``);
     - every line that holds only 1 to 3 capitalised words or initials, none of them allowed
       (``Jane``, ``J. Smith``);
     - every capitalised run of 2 or more words, and a lone capitalised word in mid-sentence
       (``Thanks, Jane``);
     - a capitalised word starting the text, a line or a sentence when the next word is a name
       verb (``NAME_VERBS``: ``is``, ``will``, ``handles``…), when ``, `` or `` from `` or
       `` here`` follows it (``Tom, our COO``, ``Sarah from accounts``, ``Bethan here``), or when
       it ends in ``'s``. A colon or dot between digits (``10:30``, ``3.5``) ends no sentence.

     Neighbouring name placeholders become one ``[name]``.

``ALLOWED_WORDS`` keeps a small set of capitalised words that are never a name (compared in
their own case, capitalised or in capitals): days, months that aren't also first names, ``I``,
``AI``, pronouns and common sentence openers, the words of an opt-out (``Me``, ``Us``, ``Take``,
``Off``, ``List``, ``Stop``, ``Remove``, ``Unsubscribe``, ``Email``, ``Contact``, ``Again``,
``Please``, ``Your``…), common one-word replies, acronyms and the tools prospects name (Clio,
Copilot, Excel…), but no first name (Harvey, Sage, May, Will, April, June and August are taken
for names; a tool or month replaced by mistake is the over-replacement this module accepts). An
all-capitals word (``STOP``, ``ERP``) is taken for a name only on a signature line, so an
opt-out keeps its words. A word of ``names`` is replaced even when allowed.

``mask_contact_details(text) -> str`` (Story 4.15) replaces only email addresses (``[email]``)
and phone numbers (``[phone]``) as step 2 does, but a phone number there must also hold at most
15 digits and not be a shape ``nextron.domain.grounding_rules`` doesn't take for a phone number
(a date, a year range such as ``2019-2024`` or ``2019 - 2024``, or a number grouped in thousands
such as ``10 000 000`` or ``1.250.000``), so a figure reaches the model unchanged. It is
idempotent.
"""

import re
from collections.abc import Callable, Iterable
from typing import Final

EMAIL: Final = "[email]"
URL: Final = "[url]"
PHONE: Final = "[phone]"
ADDRESS: Final = "[address]"
ORGANISATION: Final = "[organisation]"
NAME: Final = "[name]"
PLACEHOLDERS: Final = (EMAIL, URL, PHONE, ADDRESS, ORGANISATION, NAME)
MIN_PHONE_DIGITS: Final = 7
MAX_PHONE_DIGITS: Final = 15  # mask_contact_details only, as grounding_rules
MAX_SIGNATURE_WORDS: Final = 4
MIN_PART_LENGTH: Final = 2

_PLACEHOLDER_SPLIT: Final = re.compile(
    "(" + "|".join(re.escape(placeholder) for placeholder in PLACEHOLDERS) + ")"
)
_QUOTED_LINE: Final = re.compile(r"^[ \t]*>.*(?:\n|$)", re.MULTILINE)
_HISTORY: Final = re.compile(
    r"^[ \t]*(?:"
    r"On\b[^\n]*(?:\n[^\n]*)?\bwrote:[ \t]*$"  # Gmail and Apple Mail
    r"|-{2,}[ \t]*Original Message[ \t]*-{2,}"  # Outlook
    r"|-{2,}[ \t]*Forwarded message[ \t]*-{2,}"  # Gmail forwards
    r"|From:[^\n]*\n[ \t]*(?:Sent|Date):"  # an Outlook or Gmail header block
    r"|_{10,}[ \t]*$"  # Outlook's rule above the quoted message
    r")",
    re.MULTILINE | re.IGNORECASE,
)
_ODD_SPACES: Final = re.compile("[   ]")  # no-break, figure, narrow no-break
_EMAIL_PATTERN: Final = re.compile(r"[\w.%+'-]+@[\w-]+(?:\.[\w-]+)+")
_URL_PATTERN: Final = re.compile(
    r"(?:\b(?:https?://|www\.)[^\s<>\"']+)"
    r"|(?<![\w@.-])(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s<>\"']*)?(?![\w-])",
    re.IGNORECASE,
)
_URL_TRAIL: Final = ".,;:!?)]}'\""
_PHONE_PATTERN: Final = re.compile("\\+?\\(?\\d[\\d \\t().\\-/–—]{5,}\\d\\)?")
_DATES: Final = (
    re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"),
    re.compile(r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"),
)
# mask_contact_details only: grounding_rules' _NOT_PHONE shapes (dates, year ranges, numbers
# grouped in thousands), kept in step with it by tests/unit/test_step_drafting.py.
_NOT_PHONE: Final = re.compile(
    r"\d{1,2}([./-])\d{1,2}\1\d{4}"
    r"|\d{4}([./-])\d{1,2}\2\d{1,2}"
    r"|(?:19|20)\d{2}\s*-\s*(?:19|20)\d{2}"
    r"|\d{1,3}([.,])\d{3}(?:\3\d{3})+"
    r"|\d{1,2}( )\d{3}(?:\4\d{3})+"
)
_STREET_SUFFIX: Final = (
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Place|Pl"
    r"|Parkway|Pkwy|Highway|Hwy|Terrace|Circle|Square|Sq|Row|Close|Crescent|Gardens|Mews|Walk"
    r"|Grove|Plaza|Park)"
)
_STATES: Final = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ"
    "|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC|Alabama|Alaska|Arizona"
    "|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois"
    "|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota"
    "|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York"
    "|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island"
    "|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington"
    "|West Virginia|Wisconsin|Wyoming"
)
_ADDRESS_PATTERNS: Final = (
    re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?[ \t]*\d[A-Z]{2}\b", re.IGNORECASE),  # a UK postcode
    re.compile(
        r"\b\d{1,6}[A-Za-z]?[ \t]+(?:[A-Z0-9][\w'.-]*[ \t]+){0,4}" + _STREET_SUFFIX + r"\b\.?"
        r"(?:,?[ \t]*(?:Suite|Ste|Unit|Floor|Fl|Apt|#)\.?[ \t]*[\w-]+)?"
    ),
    re.compile(r"\b(?:" + _STATES + r"),?[ \t]+\d{5}(?:-\d{4})?\b"),
    re.compile(r"\bP\.?[ \t]*O\.?[ \t]*Box[ \t]+\d+\b", re.IGNORECASE),
)
COMPANY_SUFFIXES: Final = (
    "Ltd",
    "Limited",
    "LLP",
    "LLC",
    "Inc",
    "Incorporated",
    "PLC",
    "Plc",
    "plc",
    "Corp",
    "Corporation",
    "Company",
    "Co",
    "GmbH",
    "LP",
    "PC",
    "PLLC",
    "Solicitors",
    "Partners",
    "Partnership",
    "Group",
    "Holdings",
    "Associates",
    "Chambers",
)
_WORD: Final = r"[A-Z][\w'’-]*(?:\.[A-Z][\w'’-]*)*"  # "J.R." keeps its last dot outside
_ORGANISATION_PATTERN: Final = re.compile(  # a dot after the suffix stays: it may end a sentence
    r"\b" + _WORD + r"(?:[ \t]+(?:" + _WORD + r"|&|and|of|the))*"
    r"[ \t]+(?:&[ \t]+Co|" + "|".join(COMPANY_SUFFIXES) + r")\b"
)
_HONORIFIC: Final = re.compile(  # the abbreviations in any case; the next word in any case
    r"\b(?:(?i:mr|mrs|ms|mx|dr|prof)\b\.?|(?:Miss|Sir|Dame|Lord|Lady)\b)[ \t]+"
    r"(?!(?i:or|and|the|to|of)\b)[^\W\d_][\w'’-]*(?:[ \t]+" + _WORD + r")*"
)
_GREETING: Final = re.compile(  # at a true line start; the name is taken when _greeted allows
    r"^([ \t]*(?i:hi|hello|hey|dear|morning|good morning|good afternoon|good evening"
    r"|greetings)\b[ \t,]*)([^\W\d_][\w'’-]*)(?=[ \t]*(?:[,!.:;–—-]|$))",
    re.MULTILINE,
)
_SIGN_OFFS: Final = (
    r"(?:thanks|thank you|many thanks|cheers|regards|best|best regards|kind regards"
    r"|warm regards|all the best|sincerely|yours sincerely|yours faithfully|yours truly"
    r"|take care|talk soon)"
)
_SIGN_OFF_LINE: Final = re.compile(
    r"[ \t]*" + _SIGN_OFFS + r"[ \t]*[,.!–—-]*[ \t]*", re.IGNORECASE
)
_NAME_RUN: Final = re.compile(re.escape(NAME) + r"(?:[ \t]+" + re.escape(NAME) + r")+")
_TOKEN: Final = re.compile(r"[^\W\d_][\w'’-]*")
_SENTENCE_END: Final = re.compile(r"[!?\n]|(?<!\d)[.:]|[.:](?!\d)")  # "10:30" and "3.5" aren't
_LEAD_IN: Final = re.compile(r",[ \t]|[ \t]+(?:from|here)\b")  # "Tom, our COO", "Sarah from …"
_INITIAL: Final = re.compile(r"[A-Z]\.?")
_LONE_NAME_WORDS: Final = 3
NAME_VERBS: Final = frozenset(
    {
        "is", "was", "will", "would", "can", "could", "should", "may", "might", "has", "had",
        "does", "did", "handles", "handled", "manages", "managed", "runs", "ran", "leads", "led",
        "looks", "deals", "covers", "owns", "oversees", "heads", "takes", "took", "said", "says",
        "asked", "mentioned", "suggested", "recommended", "knows", "wants", "needs", "joined",
        "left", "works", "worked", "sits", "reports", "isn't", "wasn't", "won't", "can't",
    }
)  # fmt: skip
_NOT_A_NAME: Final = frozenset(
    {
        "there", "all", "team", "everyone", "folks", "both", "again", "everybody", "y'all",
        "sir", "madam", "you",
    }
)  # fmt: skip
ALLOWED_WORDS: Final = frozenset(
    {
        # days and months
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Mon",
        "Tue", "Tues", "Wed", "Thu", "Thurs", "Fri", "Sat", "Sun", "January", "February",
        "March", "July", "September", "October", "November",
        "December", "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov",
        "Dec", "Christmas", "Easter", "Today", "Tomorrow", "Yesterday",
        # pronouns, determiners and common openers
        "I", "I'm", "I’m", "I'll", "I’ll", "I've", "I’ve", "I'd", "I’d", "We", "We're", "We'll",
        "We've", "We’re", "We’ll", "You", "You're", "Your", "He", "She", "They", "It", "It's",
        "It’s", "This", "That", "That's", "That’s", "These", "Those", "There", "There's", "Here",
        "Here's", "What", "Which", "Who", "Where", "When", "Why", "How", "My", "Our", "His", "Her",
        "Their", "Its", "The", "A", "An", "Yes", "No", "Not", "Nope", "Yep", "Sure", "Thanks",
        "Thank", "Please", "Sorry", "Hi", "Hello", "Hey", "Dear", "Best", "Regards", "Cheers",
        "Kind", "Many", "Warm", "All", "Also", "And", "But", "Or", "So", "If", "As", "At", "In",
        "On", "For", "From", "To", "With", "Without", "Let", "Let's", "Happy", "Great", "Good",
        "Unfortunately", "However", "Honestly", "Actually", "Right", "Okay", "OK", "Ok", "Just",
        "Now", "Then", "Maybe", "Perhaps", "Do", "Does", "Did", "Don't", "Don’t", "Can", "Could",
        "Would", "Should", "Is", "Are", "Was", "Were", "Have", "Has", "Had", "Any", "Some",
        "Every", "Each", "Nothing", "Everything", "Someone", "Anyone", "Everyone", "None", "Re",
        "Fwd", "Fw", "Subject", "Sent", "Note", "Out", "Office", "Mr", "Mrs", "Ms", "Dr",
        "Stop", "Unsubscribe", "Remove", "Interested", "Back", "Away",
        # the words of an opt-out, and one-word replies
        "Me", "Us", "Take", "Off", "List", "Email", "Emailing", "Emails", "Contact", "Again",
        "Unsubscribed", "Removed", "Pass", "Done", "Agreed", "Noted", "Understood",
        # sentence openers that a comma often follows
        "Currently", "Frankly", "Personally", "Ideally", "Otherwise", "Typically", "Luckily",
        "Sadly", "Basically", "Fortunately", "Firstly", "Secondly", "Finally", "Meanwhile",
        "Additionally", "Overall", "Next", "Lastly", "Absolutely", "Definitely", "Certainly",
        "Apologies", "Well", "Look", "Thankfully", "Alternatively", "Instead",
        "Respectfully", "Ultimately", "Anyway",
        "Sir", "Madam",
        # acronyms and regulators
        "AI", "UK", "US", "USA", "EU", "GDPR", "SRA", "ABA", "ICO", "PECR", "CAN-SPAM", "AP", "AR",
        "IT", "HR", "CEO", "COO", "CFO", "CTO", "VP", "FYI", "PS", "ASAP", "ETA", "NDA", "API",
        "ERP", "CRM", "PDF", "SaaS", "Q1", "Q2", "Q3", "Q4", "LLM", "GPT", "B2B",
        # the tools prospects name
        "Clio", "Copilot", "Excel", "Outlook", "Teams", "Zoom", "Slack", "Google",
        "Gmail", "Microsoft", "Dynamics", "GP", "SAP", "NetSuite", "QuickBooks", "Xero",
        "Salesforce", "HubSpot", "LinkedIn", "Calendly", "Word", "ChatGPT", "OpenAI", "iManage",
        "NetDocuments", "LexisNexis", "Westlaw", "Intapp", "Actionstep", "Smokeball", "MyCase",
    }
)  # fmt: skip


def _outside(text: str, replace: Callable[[str], str]) -> str:
    """``replace`` applied to each part of ``text`` that isn't a placeholder."""
    parts = _PLACEHOLDER_SPLIT.split(text)
    return "".join(part if index % 2 else replace(part) for index, part in enumerate(parts))


def _sub(pattern: re.Pattern[str], placeholder: str) -> Callable[[str], str]:
    return lambda text: pattern.sub(placeholder, text)


def _drop_history(text: str) -> str:
    found = _HISTORY.search(text)
    kept = text if found is None else text[: found.start()]
    return _QUOTED_LINE.sub("", kept)


def _urls(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        found = match.group(0)
        trail = len(found) - len(found.rstrip(_URL_TRAIL))
        return URL + found[len(found) - trail :] if trail else URL

    return _URL_PATTERN.sub(replace, text)


def _phones(text: str, *, numbers_kept: bool = False) -> str:
    """``text`` with its phone numbers replaced; ``numbers_kept`` also keeps what
    ``grounding_rules`` doesn't take for a phone number (``mask_contact_details``)."""

    def replace(match: re.Match[str]) -> str:
        run = match.group(0)
        digits = sum(ch.isdigit() for ch in run)
        bare = run.strip("() \t")
        if digits < MIN_PHONE_DIGITS or any(date.fullmatch(bare) for date in _DATES):
            return run
        if numbers_kept and (digits > MAX_PHONE_DIGITS or _NOT_PHONE.fullmatch(bare)):
            return run
        trail = run[len(run.rstrip(" \t")) :]
        return PHONE + trail

    return _PHONE_PATTERN.sub(replace, text)


def _addresses(text: str) -> str:
    for pattern in _ADDRESS_PATTERNS:
        text = _outside(text, _sub(pattern, ADDRESS))
    return text


def _organisations(text: str) -> str:
    """Capitalised runs ending in a company suffix, less any allowed words that start them
    ("Thanks Hale Ltd" keeps "Thanks")."""

    def replace(match: re.Match[str]) -> str:
        found = match.group(0)
        words = found.split()
        kept = 0
        while kept < len(words) - 2 and words[kept] in ALLOWED_WORDS:
            kept += 1
        if not kept:
            return ORGANISATION
        lead = found[: found.index(words[kept], len(" ".join(words[:kept])))]
        return lead + ORGANISATION

    return _ORGANISATION_PATTERN.sub(replace, text)


def _whole_words(values: Iterable[str]) -> re.Pattern[str] | None:
    """The given values as whole words in any case, each with an optional ``'s``."""
    chosen = sorted(
        {" ".join(value.split()) for value in values if value.strip()} - set(PLACEHOLDERS),
        key=len,
        reverse=True,
    )
    if not chosen:
        return None
    alternatives = "|".join(r"[ \t]+".join(map(re.escape, value.split())) for value in chosen)
    return re.compile(
        r"(?<![\w'’])(?:" + alternatives + r")(?:['’]s)?(?![\w'’])", re.IGNORECASE
    )


def _given(text: str, values: Iterable[str], placeholder: str) -> str:
    pattern = _whole_words(values)
    return text if pattern is None else _outside(text, _sub(pattern, placeholder))


def _parts(names: Iterable[str]) -> list[str]:
    parts = []
    for name in names:
        parts += [part for part in re.split(r"[\s,]+", name) if len(part) >= MIN_PART_LENGTH]
    return parts


def _shouted(word: str) -> bool:
    """An all-capitals word of 2 or more letters: an acronym or shouting ("STOP")."""
    letters = [ch for ch in word if ch.isalpha()]
    return len(letters) >= MIN_PART_LENGTH and all(ch.isupper() for ch in letters)


def _allowed(word: str) -> bool:
    """Whether ``word`` (its punctuation and any ``'s`` dropped) is an allowed word, in its own
    case, capitalised or in capitals ("STOP" is "Stop"; "COO" is "COO")."""
    bare = _base(word.strip(",.;:!?\"'()"))
    return any(form in ALLOWED_WORDS for form in (bare, bare.capitalize(), bare.upper()))


def _capitalised(word: str) -> bool:
    """Whether ``word`` may be a name: capitalised, not allowed and not shouted."""
    return word[:1].isupper() and not _allowed(word) and not _shouted(word)


def _greetings(text: str) -> str:
    """The word after a greeting at the start of a line, when it may be a name: never a
    lowercase, shouted, allowed or common word ("Hello, unsubscribe." and "Hi STOP!" stay)."""

    def replace(match: re.Match[str]) -> str:
        word = match.group(2)
        if word.casefold() in _NOT_A_NAME or not _capitalised(word):
            return match.group(0)
        return match.group(1) + NAME

    return _GREETING.sub(replace, text)


def _words(line: str) -> list[str]:
    return [word.strip(",.;:!") for word in line.split()]


def _signature(line: str) -> bool:
    """Whether a line after a sign-off reads as a name: 1 to 4 capitalised words, at least one
    of them a possible name (``Stop`` after ``Thanks,`` stays), or all in capitals and none
    allowed ("JANE SMITH")."""
    words = _words(line)
    if not 0 < len(words) <= MAX_SIGNATURE_WORDS or not all(w[:1].isupper() for w in words):
        return False
    if any(_capitalised(word) for word in words):
        return True
    return all(_shouted(word) for word in words) and not any(_allowed(word) for word in words)


def _lone_name(line: str) -> bool:
    """Whether a line holds only 1 to 3 capitalised words or initials, none allowed ("Jane",
    "J. Smith")."""
    words = _words(line)
    return (
        0 < len(words) <= _LONE_NAME_WORDS
        and all(_INITIAL.fullmatch(word + ".") or _capitalised(word) for word in words)
        and not any(_allowed(word) for word in words)
    )


def _name_lines(text: str) -> str:
    """The line after a line that is only a sign-off (blank lines between) when it reads as a
    signature, and every line that is only a name, as ``[name]``."""
    lines = text.split("\n")
    previous: int | None = None
    for index, line in enumerate(lines):
        if not line.strip() or line.strip() == NAME:
            previous = index if line.strip() else previous
            continue
        after_sign_off = previous is not None and _SIGN_OFF_LINE.fullmatch(lines[previous])
        if (after_sign_off and _signature(line)) or (
            _PLACEHOLDER_SPLIT.search(line) is None and _lone_name(line)
        ):
            lines[index] = line[: len(line) - len(line.lstrip())] + NAME
        previous = index
    return "\n".join(lines)


def _base(word: str) -> str:
    for suffix in ("'s", "’s"):
        if word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _starts_sentence(text: str, start: int, previous_end: int | None, at_start: bool) -> bool:
    if previous_end is None:
        return at_start or _SENTENCE_END.search(text, 0, start) is not None
    return _SENTENCE_END.search(text, previous_end, start) is not None


def _marked(text: str, tokens: list[re.Match[str]], at_start: bool) -> list[bool]:
    """Which tokens are name words; see the module docstring."""
    marked = [False] * len(tokens)
    for index, token in enumerate(tokens):
        word = token.group(0)
        if not _capitalised(word):
            continue
        previous_end = tokens[index - 1].end() if index else None
        if not _starts_sentence(text, token.start(), previous_end, at_start):
            marked[index] = True  # mid-sentence, a run of 2 or more included
            continue
        if word != _base(word) or _LEAD_IN.match(text, token.end()):
            marked[index] = True  # "Sarah's away", "Tom, our COO", "Sarah from accounts"
            continue
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        if following is not None and not _SENTENCE_END.search(
            text, token.end(), following.start()
        ):
            next_word = following.group(0)
            marked[index] = _capitalised(next_word) or next_word.casefold() in NAME_VERBS
    return marked


def _name_words(text: str, *, at_start: bool) -> str:
    """The capitalised-word rules, on text with no placeholder in it; ``at_start`` is whether it
    starts the whole text (otherwise it follows a placeholder, mid-sentence)."""
    tokens = list(_TOKEN.finditer(text))
    marked = _marked(text, tokens, at_start)
    found = []
    last = 0
    for index, token in enumerate(tokens):
        if marked[index]:
            found.append(text[last : token.start()] + NAME)
            last = token.end()
    return "".join(found) + text[last:]


def _names(text: str) -> str:
    parts = _PLACEHOLDER_SPLIT.split(text)
    named = "".join(
        part if index % 2 else _name_words(part, at_start=index == 0)
        for index, part in enumerate(parts)
    )
    return _NAME_RUN.sub(NAME, named)


def mask_contact_details(text: str) -> str:
    """``text`` with its email addresses and phone numbers replaced; see the module
    docstring."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    found = _outside(text, _sub(_EMAIL_PATTERN, EMAIL))
    return _outside(found, lambda part: _phones(part, numbers_kept=True))


def pseudonymise(text: str, *, names: Iterable[str] = (), organisations: Iterable[str] = ()) -> str:
    """``text`` with its personal details replaced; see the module docstring."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    if isinstance(names, str) or isinstance(organisations, str):
        raise TypeError("names and organisations must be collections of strs, not a str")
    names = tuple(names)
    organisations = tuple(organisations)
    if not all(isinstance(value, str) for value in (*names, *organisations)):
        raise TypeError("names and organisations must be strs")
    found = _ODD_SPACES.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n"))
    found = _drop_history(found)
    found = _outside(found, _sub(_EMAIL_PATTERN, EMAIL))
    found = _outside(found, _urls)
    found = _outside(found, _phones)
    found = _addresses(found)
    found = _given(found, organisations, ORGANISATION)
    found = _outside(found, _organisations)
    found = _given(found, names, NAME)
    found = _given(found, _parts(names), NAME)
    found = _outside(found, _sub(_HONORIFIC, NAME))
    found = _greetings(found)
    found = _name_lines(found)
    return _names(found)
