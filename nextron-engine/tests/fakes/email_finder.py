"""``ScriptedFinder`` and ``ScriptedDomainSearch``: the scripted fakes of
``nextron.ports.email_finder.EmailFinderPort`` and ``DomainSearchPort``.

``ScriptedFinder(*script)`` answers each ``find_email`` with the next item of ``script``, in
order: a ``FinderAnswer`` is returned as it is, an ``AdapterError`` is raised. Once the script is
exhausted every call answers ``FinderAnswer(None)`` (nothing found), so a test that expects no
address needs no script. ``calls`` records every ``FinderQuery`` it was asked, answered or not.

``ScriptedDomainSearch(*script)`` does the same for ``domain_search``, with
``DomainSearchAnswer``s; once its script is exhausted it answers ``DomainSearchAnswer(None,
())`` (no pattern, nobody). ``calls`` records every domain it was asked.

Both build with no arguments. Unlike the real adapters they read no key and meter nothing, so a
service test checks the budget and the call counts itself.
"""

from collections import deque

from nextron.ports.email_finder import DomainSearchAnswer, FinderAnswer, FinderQuery
from nextron.ports.errors import AdapterError


class ScriptedFinder:
    def __init__(self, *script: FinderAnswer | AdapterError) -> None:
        self.script: deque[FinderAnswer | AdapterError] = deque(script)
        self.calls: list[FinderQuery] = []

    def find_email(self, query: FinderQuery) -> FinderAnswer:
        if not isinstance(query, FinderQuery):
            raise TypeError("find_email takes a FinderQuery")
        self.calls.append(query)
        if not self.script:
            return FinderAnswer(None)
        answer = self.script.popleft()
        if isinstance(answer, AdapterError):
            raise answer
        return answer


class ScriptedDomainSearch:
    def __init__(self, *script: DomainSearchAnswer | AdapterError) -> None:
        self.script: deque[DomainSearchAnswer | AdapterError] = deque(script)
        self.calls: list[str] = []

    def domain_search(self, domain: str) -> DomainSearchAnswer:
        if not isinstance(domain, str):
            raise TypeError("domain_search takes a str domain")
        self.calls.append(domain)
        if not self.script:
            return DomainSearchAnswer(None, ())
        answer = self.script.popleft()
        if isinstance(answer, AdapterError):
            raise answer
        return answer
