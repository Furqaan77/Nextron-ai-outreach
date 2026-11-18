"""``ScriptedPlaces``: the scripted fake of ``nextron.ports.places.PlacesPort``.

``ScriptedPlaces(pages=None)`` answers each ``text_search(query, location, page_token)`` from a
script: ``pages`` maps the ``(query, location, page_token)`` triple to the answer (a
``PlaceSearchPage``), an ``AdapterError`` (raised) or a no-argument callable returning or raising
one of those. The script is kept, so a key answers the same every time; a key with no answer
raises ``AssertionError``, so a test can't make a call it didn't plan.

``calls`` records every call's triple, in order. Unlike ``GooglePlaces`` it reads no key and
meters nothing.
"""

from collections.abc import Callable, Mapping

from nextron.ports.errors import AdapterError
from nextron.ports.places import PlaceSearchPage

type PageKey = tuple[str, str, str | None]
type PageAnswer = PlaceSearchPage | AdapterError | Callable[[], PlaceSearchPage]


class ScriptedPlaces:
    def __init__(self, pages: Mapping[PageKey, PageAnswer] | None = None) -> None:
        self.pages: dict[PageKey, PageAnswer] = dict(pages or {})
        self.calls: list[PageKey] = []

    def text_search(
        self, query: str, location: str, page_token: str | None = None
    ) -> PlaceSearchPage:
        key = (query, location, page_token)
        self.calls.append(key)
        if key not in self.pages:
            raise AssertionError("ScriptedPlaces: no text_search answer is scripted")
        answer = self.pages[key]
        if callable(answer):
            answer = answer()
        if isinstance(answer, AdapterError):
            raise answer
        if not isinstance(answer, PlaceSearchPage):
            raise AssertionError("ScriptedPlaces: an answer must be a PlaceSearchPage")
        return answer
