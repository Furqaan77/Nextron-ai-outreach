---
title: "Story 2.6: Render near-empty pages and detect chat and booking widgets"
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '689874306572671ac41950cdc25624632505a078'
review_loop_iteration: 0
story_key: '2-6-render-near-empty-pages-and-detect-chat-and-booking-widgets'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-5-crawl-a-lead-s-website-politely-inside-the-egress-filter.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The Story 2.5 crawl stores only what raw HTML shows. A site that builds its content in JavaScript stores almost no text, and chat or booking widgets that scripts inject are never seen. A false "no chat" result then becomes the score-only Signal "contact form only" in Story 2.9.

Requirements: FR-10 · AD-9, AD-10, AD-18, INT-12 · NFR-34.

**Approach:**
- **Detection.** `domain/widgets.py` detects contact methods (form, chat, booking, phone, after-hours line) from a page's raw HTML, its script, iframe and anchor URLs, and its visible text.
- **Per page.** The crawl stores each page's detections in `page_contact_methods`.
- **Queueing.** When a stored page has under 500 characters of text, the crawl's apply transaction enqueues `render_pages`.
- **Rendering.** That job renders up to 3 pages through a new `RendererPort` (the Playwright adapter `adapters/renderer`). Every request the browser makes is fetched through the crawler's egress-filtered client. The job replaces the page text and adds what the render detects.
- **Profile.** Once detection is complete, the Lead's profile group gains `contact_methods` (migration `0022_2_6_contact_methods`).

The acceptance criteria are Story 2.6 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **`nextron/domain/widgets.py`** (stdlib only; `html.parser` allowed).
  - **`ContactMethod`:** `form`, `chat`, `booking`, `phone`, `after_hours`.
  - **`MatchSource`:** `script`, `iframe`, `link` (an `a` href), `html` (a marker in the raw HTML) and `text` (visible text).
  - **`Signature(key, method, hosts, markers, phrases)`:**
    - `hosts` match a URL's host (equal, or a subdomain of it), optionally with a path prefix (`docs.google.com/forms`);
    - `markers` are case-insensitive substrings of the raw HTML;
    - `phrases` are case-insensitive whole phrases (no letter or digit on either side) in the visible text.

    Every chat and booking signature has at least one host and one phrase.
  - **Chat signatures** (`CHAT_SIGNATURES`):
    - `intercom`, `drift`, `livechat`, `tawk_to`, `zendesk_chat`, `hubspot_chat`, `tidio`, `smith_ai`;
    - their widget hosts, for example `widget.intercom.io`, `js.intercomcdn.com`, `js.driftt.com`, `cdn.livechatinc.com`, `embed.tawk.to`, `static.zdassets.com`, `zopim.com`, `js.usemessages.com`, `code.tidio.co` and `smith.ai`;
    - their loader markers (`intercomSettings`, `drift.load(`, `__lc.license`, `Tawk_API`, `zESettings`, `HubSpotConversations`, `tidioChatApi` and the hosts themselves);
    - phrases such as "powered by intercom", "livechat", "tawk.to", "zendesk chat", "hubspot chat", "tidio" and "smith.ai".
  - **Booking signatures** (`BOOKING_SIGNATURES`):
    - `calendly`, `cal_com`, `acuity`, `clio_grow`, `lawtap`, `ms_bookings`, `hubspot_meetings`;
    - their hosts: `calendly.com`, `cal.com`, `acuityscheduling.com`, `as.me`, `grow.clio.com`, `lawtap.com`, `outlook.office365.com/owa/calendar`, `outlook.office365.com/book`, `outlook.office.com/book` and `meetings.hubspot.com`;
    - phrases: the product names, for example "calendly", "acuity scheduling", "clio grow", "lawtap", "microsoft bookings" and "hubspot meetings".
  - **Form signatures:**
    - vendor forms by host: HubSpot forms, Typeform, Jotform, Wufoo, Formstack, Cognito Forms, Microsoft Forms and Google Forms;
    - markers `gform_wrapper`, `wpcf7` and `wpforms-form`.
  - **Which sources a signature checks:**
    - `script` and `iframe` URLs check every signature;
    - `link` URLs check only booking and form signatures;
    - `html` checks markers, and `text` checks phrases.
  - **Generic rules:**
    - `form_tag`: a `<form>` holding a `<textarea>` or an `<input type="tel">`;
    - `tel_link`: an `href="tel:..."` in the raw HTML;
    - `phone_text`: a US number (10 digits, optional `+1`, separators space, `.`, `-` or parentheses) or a UK number (`+44` or `0`, then 9 or 10 digits with optional spaces or hyphens) in the text;
    - `chat_text`: "live chat", "chat with us", "chat now" or "start a chat";
    - `booking_text`: "book online", "online booking", "schedule online", "book an appointment online" or "book a consultation online";
    - `after_hours_text`: "24/7", "24x7", "24 hours a day", "24-hour", "around the clock", "after hours", "after-hours", "out of hours", "out-of-hours", "evenings and weekends" or "emergency line".
  - **Detection functions.**
    - `detect(html, embeds, text) -> tuple[Detection, ...]`. `embeds` is an iterable of `(tag, url)` pairs, `tag` one of `a`, `iframe` or `script`.
    - A `Detection(method, signature, source, matched)` keeps the first match only. `matched` is the host matched (never the full URL), the marker, or the text as written, at most 200 characters (`MAX_MATCHED`).
    - There is one detection per method, signature and source, in a deterministic order. Garbage input never raises.
  - **The Lead's profile value.**
    - `ContactMethods(run_id, checked_at, pages_checked, found)`.
    - `summarize(run_id, checked_at, pages)` takes `pages` as `(url, methods)` pairs. Every page listed is checked; `found` maps each of the five methods to the sorted URLs where it was detected (possibly none).
    - `encode` writes canonical JSON (sorted keys, `{"v":1,...}`), and `decode` reads it back.
- **`nextron/domain/crawl.py` additions:**
  - `RENDER_THRESHOLD = 500`; `needs_render(text)` is `len(text) < 500`;
  - `MAX_RENDERS = 3`;
  - `RENDER_TIMEOUT_S = 20.0`;
  - `RENDER_PAGES_JOB = "render_pages"`.
- **Port `nextron/ports/renderer.py`** (`@runtime_checkable RendererPort`).
  - `render(url, *, site, min_interval_s) -> RenderedPage(url, final_url, text, links, html)`, always a 2xx HTML page. `site` is `None` or the registrable domain every main-frame navigation must stay on, as in `fetch_page`. Text, links and HTML are hidden from the `repr`, and `links` is a tuple of `PageLink`.
  - **Failures** raise `AdapterError` with:
    - a `FETCH_*` code from the main document's fetch;
    - `render_failed`: the main document is not 2xx HTML, or anything else fails;
    - `render_timeout` (retryable): the navigation passes the time limit;
    - `render_unavailable`: the browser can't start.
- **Crawler adapter** (`nextron/adapters/crawler/__init__.py`, additive; existing behaviour unchanged). A public `HttpCrawler.fetch_resource(url, *, site, accepts, accept_header, operation, deadline, min_interval_s) -> Resource(final_url, status, media_type, charset, headers, body)`:
  - It runs the same hops as `_fetch` (every check, DNS pinning, at most 5 redirects, the 5 MiB cap and one metered `ApiCall` per request).
  - `min_interval_s=None` means unpaced.
  - `deadline` (monotonic) caps the fetch's own 30 s deadline.
  - A 2xx media type must satisfy `accepts`.
  - `headers` holds only `access-control-allow-origin` and `access-control-allow-credentials`.
- **Adapter `nextron/adapters/renderer/__init__.py`.** `PlaywrightRenderer(*, meter, fetcher=None, browser=None, monotonic=time.monotonic)`; `fetcher` defaults to `HttpCrawler(meter=meter)`.
  - **The live constructor.** `default_browser()` returns a context manager that starts `sync_playwright()` and launches headless Chromium with `CHROMIUM_ARGS`:
    - `--host-resolver-rules=MAP * ~NOTFOUND`;
    - `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`;
    - the launch `proxy` set to the dead loopback proxy `http://127.0.0.1:9`;
    - `handle_sigint`, `handle_sigterm` and `handle_sighup` all off.

    It closes the browser and Playwright on exit. It is looked up at each `render`, so the live guard can refuse it.
  - **`render`, in order:**
    1. `request_interval(min_interval_s)`, then the first URL and the `site` pin. A refusal is `fetch_refused` before anything is launched.
    2. One browser per call. The fresh context has `accept_downloads=False`, `service_workers="block"`, no storage state and `user_agent=USER_AGENT`.
    3. `context.route("**/*")` handles every request, and `route_web_socket("**/*")` closes every socket.
    4. The 20 s deadline starts at navigation. `goto(wait_until="domcontentloaded")` gets the time left. Then `load`, and `networkidle` capped at 5 s, are awaited, and their timeouts are ignored.
    5. `page.content()` is parsed by `nextron.adapters.crawler.parsing.parse_html` against the main document's final URL.
  - **The request handler.** It aborts unless all of these hold:
    - the method is GET;
    - the request is within 100 requests and 20 MiB per render, before the deadline;
    - its type is a main-frame `document` (the rendered page's own main frame, pinned to `site`, at most 6 navigations), `script` (JavaScript media types), `stylesheet` (`text/css`), or `xhr`/`fetch` (JSON, `+json`, `text/plain`, HTML).

    An allowed request is fetched with `fetch_resource`:
    - operation `render`;
    - the main document paced by `min_interval_s`, sub-resources unpaced;
    - no browser header forwarded.

    It is fulfilled with the final status, the body, a rebuilt `content-type` and the two CORS headers, and no `set-cookie`.
  - **Errors.**
    - Every fetch failure aborts the request. A main-document failure is re-raised after navigation as its `AdapterError`.
    - Playwright's errors (`playwright.sync_api.Error`) and any other exception become `render_*` codes, raised `from None` with fixed messages. Only `AdapterError` leaves `render`, and no URL, header or page text is in an error.
- **Persistence.** Migration `0022_2_6_contact_methods` (down `0021_4_2_campaigns`):
  - **`leads.contact_methods`.** A nullable `Text`: the profile group, owner `research`, PII `none`, written only by the new `db/repos/research/profile.py`. It is added in place, with no rebuild.
  - **`page_contact_methods`** (owner `research`; model in `db/models/crawl.py`, repo functions in `db/repos/research/crawl.py`):
    - `id`;
    - `page_id`: FK to `crawled_pages`, indexed;
    - `method`: CHECK;
    - `signature`;
    - `source`: CHECK;
    - `matched` (200);
    - `detected_at`.

    It is unique on `(page_id, method, signature, source)`. Only `matched` is PII `redact`. With no `lead_id`, the rows follow their page on a merge.
- **`WebsiteCrawls(clock, meter, crawler, renderer, queue)`** (`app/research/api.py`):
  - **`enrich_lead`.**
    - Each stored page carries `widgets.detect(fetched.html, links, fetched.text)`.
    - In the apply transaction, each upserted page's detections are replaced.
    - If any stored page `needs_render`, `render_pages` is enqueued. The payload is `{v, lead_id, run_id}`, with the discriminator and concurrency key from `lead_job_keys`.
    - Otherwise, when the run stored at least one page, the Lead's `contact_methods` is set to `summarize` over the run's pages.
    - The renderer is never called here.
  - **`@use_case render_pages(lead_id, run_id) -> bool`:**
    1. **First transaction.** The budget check (`BLOCKABLE`) returns `False` when it fails. An unknown run, a run of another Lead, a merged Lead, or a run that isn't the Lead's latest logs `render_skipped` (with a reason) and returns `True`. The candidates are the run's pages with text where `needs_render` holds, in `PageKind` order then by URL, the first `MAX_RENDERS`.
    2. **Rendering**, with no transaction open. Each candidate's `final_url` is rendered with `site` set to the run's `final_domain`, else its `site_domain`, at `MIN_INTERVAL_S`. A success is detected (`widgets.detect` over the rendered HTML, links and text). An `AdapterError` adds 1 to the errors.
    3. **Apply transaction.** If the Lead was merged meanwhile, nothing is written. Otherwise:
       - non-empty rendered text replaces the page's text and `content_hash`, and `changed_at` moves when the hash changed;
       - the render's detections not already stored for the page are added;
       - the run's `renders` (successes) and `errors` are increased;
       - the Lead's `contact_methods` is set to `summarize` over the run's pages.
    4. It logs `render_finished` with ids, counts and error codes only.
- **The `render_pages` job.**

  | Lanes | Priority | Max runtime | Retries | Group |
  |---|---|---|---|---|
  | bulk only | 40, the `enrich_lead` chain's | 5 min | 2 | ENRICHMENT |

  - Handler: `handlers.render_pages(crawls)`. A missing or non-ULID `lead_id` or `run_id` raises `InvalidJob`, and a `False` result is `DEFERRED`.
  - `handlers.Crawling` gains `render_pages`.
- **Queries** (`app/queries/research.py`): `contact_methods(lead_id) -> ContactMethods | None` and `page_contact_methods(lead_id)`.
- **Wiring and guards.**
  - `build_app(renderer=None)` builds `PlaywrightRenderer(meter=meter)` unless a renderer is injected. Building it launches nothing.
  - `tests/live_guard.py` gains `renderer: Guarded(("default_browser",))` and its `LIVE_CONSTRUCTORS` entry.
  - c5 lists `nextron.adapters.renderer`.
  - `PORT_FAKES` maps `RendererPort` to `tests/fakes/renderer.py` `ScriptedRenderer`.
  - `FETCHING_ADAPTERS` gains a `renderer` driver, built on the recorder's `HttpCrawler` and the fake browser.

**Never:**
- **No real browser.** No test launches one, and no browser binary is installed or downloaded during this build. Adapter tests use the fake Playwright objects in `tests/fakes/browser.py`.
- **No direct connections from Chromium.** The browser never makes a request itself. There is no `route.continue_()`, no `route.fetch()` and no unrouted request type.
- **No new dependency,** and no setting or parameter that loosens the egress filter.
- **Nothing from later stories:** no extraction, Evidence or Signals (Story 2.9), no `extract_lead` job, and no Lead page change.
- **Nothing logged or raised that shouldn't be.** No URL, page text, HTML or matched text appears in logs, events, job payloads or errors.

### Decisions made overnight (pending user review)

1. **Chromium isn't installed by this build.** The spine pins `playwright==1.65.2` (already in `uv.lock`) with bundled Chromium, but the browser binary is a separate download. Mac installs it once with `uv run playwright install chromium` (see the README). Until then each render fails as `render_unavailable`, and the page keeps its crawled text. Nothing alerts on this.
2. **The browser never touches the network.** Every request it makes is intercepted, fetched by the crawler's own egress-filtered client (DNS pinned, every redirect re-checked, metered) and fulfilled. The browser itself can't resolve a name, has a dead proxy, sends no WebRTC UDP, has service workers blocked and has every socket closed. This closes the DNS-rebinding gap that a check-then-continue approach would leave.
3. **What a render may load** (this extends AD-9's crawler media types, for sub-resources only):
   - GET only;
   - the main document (HTML, on the Lead's site);
   - scripts, stylesheets and XHR/fetch data.

   Images, fonts, media, iframes, popups, beacons and sockets are refused. A render makes at most 100 requests and reads at most 20 MiB. No cookie, referrer or origin is forwarded.
4. **Pacing.** The main document is paced like the crawler (at least 1 s per site). Sub-resources load unpaced, as a visitor's browser would load them.
5. **The 20 s limit** runs from navigation. The browser launch has Playwright's own limit. Each render starts and closes its own browser.
6. **AD-18 on rendered pages.** The rendered DOM goes through the crawler's static stripping rules; computed CSS isn't read. Rendered text replaces the stored text only when it isn't empty. A failed render keeps the crawled text, counts as an error, and is never retried.
7. **Which pages are rendered.** Up to 3 of the run's near-empty pages, in page-kind order (home first), then by URL. `render_pages` runs on the bulk lane only, at `enrich_lead`'s priority (40), because priority is set per job type in this engine.
8. **Detection rules.**
   - The signature lists above, plus generic chat, booking, after-hours, phone and form rules.
   - The first match is stored per page, method, signature and source.
   - The stored match is a host, never the full URL, which may carry account ids.
9. **When `contact_methods` is written.**
   - It is written only for a run that stored pages: by `enrich_lead` when no render is needed, else by `render_pages`.
   - An unreachable run leaves the previous value.
   - A stale run (not the Lead's latest) renders nothing.
10. **`page_contact_methods.matched`** may hold a phone number, so it is PII class `redact`. It isn't purged with the page text.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Rich page | home of 2,000 characters with an Intercom script and a `tel:` link | chat (`intercom`, `script`) and phone (`tel_link`) stored; no `render_pages`; `contact_methods` set | N/A |
| Near-empty page | home shell of 61 characters | `render_pages` queued (bulk lane, priority 40); renderer not called by `enrich_lead`; `contact_methods` not yet set | N/A |
| Render | shell renders to 1,200 characters with a Calendly iframe | text replaced; booking (`calendly`, `iframe`) added; `renders=1`; `contact_methods` lists every page checked | N/A |
| Render fails | `render_unavailable`, a timeout or a refused document | crawled text kept; `errors+1`; `contact_methods` still set | job completes |
| Budget refused | AI spend at budget | no render, nothing written | `DEFERRED` |
| More than 3 shells | 5 near-empty pages | only 3 rendered (home first) | N/A |
| Private sub-resource | a script on a host that resolves to 10.0.0.5, or `http://127.0.0.1/` | aborted with no request to it | `fetch_refused` |
| Denylisted sub-resource or redirect | a script redirecting to `maps.google.com`; a fetch to `linkedin.com` | aborted after at most one request | `fetch_refused` |
| Off-site navigation | JavaScript navigates the main frame to another domain | aborted | N/A |
| Disallowed traffic | POST, image, font, iframe document, websocket, request 101 | aborted, or the socket closed | N/A |
| Stale or merged | a newer run exists, or the Lead was merged | `render_skipped`; nothing written | N/A |
| Generic signals | text "24/7 emergency line", "Live chat", "(212) 555-0147", a form with a textarea | after-hours, chat, phone and form detected from `text`/`html` | N/A |

</frozen-after-approval>

## Code Map

- **Patterns to reuse:**
  - `nextron/adapters/crawler/__init__.py`: `_fetch`, `_target`, `_address`, `_pace`, `_request` and `_read` (add the `pace` and `deadline` options, and the `_Answer.headers` capture);
  - `parsing.py`: `parse_html`, `content_type`;
  - `ports/crawler.py`: `PageLink`, the `FETCH_*` codes, and `FetchedPage` for the `RenderedPage` style.
- **Playwright 1.65.2** (checked in `.venv/.../playwright/sync_api/_generated.py`):
  - `chromium.launch(args, headless, proxy, timeout, handle_sig*)`;
  - `browser.new_context(accept_downloads, service_workers, storage_state, user_agent, java_script_enabled)`;
  - `context.route(url, handler(route, request))` and `route_web_socket(url, handler(ws_route))`; `ws_route.close()`;
  - `request.method`, `.url`, `.resource_type`, `.is_navigation_request()` and `.frame` (may raise for popups);
  - `route.fulfill(status, headers, body)` and `route.abort(error_code)` (`"blockedbyclient"`, `"failed"`);
  - `page.goto(url, wait_until, timeout)`, `wait_for_load_state(state, timeout)`, `content()` and `main_frame`;
  - exceptions `playwright.sync_api.Error` and `TimeoutError`.
- **Service:** `nextron/app/research/api.py` (`WebsiteCrawls`):
  - `_Page`;
  - `_stored` (add detections; `FetchedPage.html` is available there);
  - `_apply` (around the `canonicalize_lead` enqueue);
  - `_upsert_page`;
  - `KIND_ORDER`.
- **Persistence:**
  - `db/repos/research/crawl.py` (`run`, `latest_run`, `pages`, `update_page`): add `run_pages(run_id)`, `replace_contact_methods`, `add_contact_methods`, `contact_methods_of(lead_id)` and `add_render_counts`;
  - `db/models/crawl.py`, `db/models/leads.py` (docstring: a third, profile group);
  - `owners.py`, `pii_registry.py`;
  - migration precedent `versions/0020_2_5_crawl.py`.
- **Jobs:**
  - `nextron/main.py:1005` (the `enrich_lead` `JobType`), `:820` (`WebsiteCrawls` wiring) and `:762` (`build_app` keyword injection);
  - `jobs/handlers.py:155-205` (`Crawling`, `_ulid_field`).
- **Tests to extend:**
  - the `tests/app/test_website_crawl.py` fixture (constructor at line 178);
  - `tests/fakes/crawler.py` (the style for `tests/fakes/renderer.py`);
  - `tests/contracts/test_fetch_policy_contracts.py` (`FETCHING_ADAPTERS`, `Recorder`);
  - `test_adapter_boundary.py` (the crawler driver adds `fetch_resource`);
  - `test_test_isolation.py` (port names; `adapter_packages`; the probe list at 449);
  - `tests/fakes/__init__.py`.
- **Lists that name the job types:** grep `canonicalize_lead` under `tests/`: `test_engine_health.py`, `test_health_page.py`, `test_job_contracts.py` (`names()`, `specs()`), `test_runner_lifespan.py` and `tests/dashboard.py`. `build_job_types` needs no new keyword: `render_pages` uses the `crawls` provider.
- **Head bumps** (`0021_4_2_campaigns` → `0022_2_6_contact_methods`):
  - `test_persistence_contracts.py:81` and its chain probes at 790–810 (`0022_*` → `0023_*`, `0023_b` → `0024_b`);
  - `test_migrations.py:39`;
  - `test_serve_process.py:296`;
  - `test_serve.py:762,776` (`0022_more` → `0023_more`);
  - `test_source_tree.py` `FILES`.
- **`pyproject.toml` c5** (line 288): add `nextron.adapters.renderer`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/widgets.py`, `nextron/domain/crawl.py` -- signatures, `detect`, `summarize`/`encode`/`decode`, and the render constants.
- [x] `nextron/ports/renderer.py`, `nextron/adapters/crawler/__init__.py` (`fetch_resource`), `nextron/adapters/renderer/__init__.py` -- the port, the shared filtered fetch and `PlaywrightRenderer`.
- [x] `nextron/db/alembic/versions/0022_2_6_contact_methods.py`, `db/models/crawl.py`, `db/models/leads.py`, `owners.py`, `pii_registry.py`, `db/repos/research/crawl.py`, `db/repos/research/profile.py` -- the column, the table and their repos.
- [x] `nextron/app/research/api.py`, `app/queries/research.py`, `nextron/jobs/handlers.py`, `nextron/main.py` -- detection in the crawl, the enqueue, `render_pages`, the job type and the wiring.
- [x] `tests/live_guard.py`, `tests/fakes/renderer.py`, `tests/fakes/browser.py`, `tests/fakes/__init__.py`, `pyproject.toml` -- the guard, the fakes and c5.
- [x] Tests:
  - `tests/unit/test_widgets.py`: each signature from a script or iframe source, a marker and a phrase; each generic rule; the source restrictions; caps and garbage; `summarize`/`encode`/`decode`.
  - `tests/unit/test_renderer_adapter.py`:
    - the handler matrix rows;
    - context options, routes, sockets and close;
    - `default_browser`'s launch arguments, with a monkeypatched `sync_playwright` after `allow_live_constructor`;
    - errors map to `AdapterError`;
    - deadline and caps;
    - metering.
  - The crawler adapter tests gain `fetch_resource` (unpaced, deadline cap, accepts, headers).
  - `tests/app/test_website_crawl.py` (or a new `test_website_render.py`): every matrix row through the service, with `ScriptedRenderer`.
  - The fetch-policy contract, the head bumps, `FILES` and the job-list assertions.
- [x] `nextron-engine/README.md` -- a "Rendering and contact methods" section after "Website crawling", with the one-time `uv run playwright install chromium` step.

**Acceptance Criteria:**
- Given the full suite, when `pytest` and `lint-imports` run, then everything passes, no test launches a browser or reaches the network, and every contract is kept.
- Given `serve` on a fresh data dir, when it starts, then the migration reaches `0022_2_6_contact_methods`.

## Implementation Notes

Built as specified. What the spec left open, and where the code had to choose:

- **`nextron/domain/widgets.py`.**
  - **The lists.** Chat phrases: "powered by intercom"; "powered by drift" and "chat by drift"
    (Drift's own badge; "drift" alone is an ordinary word); "livechat"; "tawk.to"; "zendesk chat";
    "hubspot chat"; "tidio"; "smith.ai". Every chat signature's hosts are also its markers, as the
    spec asks (`smith.ai` too, a plain substring). Booking signatures have no markers (a host such
    as `cal.com` or `as.me` as a raw-HTML substring would match `medical.com` or `has.me`). Form
    hosts: `hsforms.net`, `hsforms.com`, `typeform.com`, `jotform.com`, `jotfor.ms`, `wufoo.com`,
    `formstack.com`, `cognitoforms.com`, `forms.office.com`, `forms.microsoft.com`,
    `docs.google.com/forms`, `forms.gle`; markers are the keys `gravity_forms`,
    `contact_form_7` and `wpforms`. The generic phrase rules are `GENERIC_SIGNATURES`
    (`chat_text`, `booking_text`, `after_hours_text`); `form_tag`, `tel_link` and `phone_text`
    are code. `RULE_KEYS` orders every key (the detection order within a method).
  - **Matching.** A host with a path matches the URL's lowercased path when it is that path or
    starts with it followed by `/` (`docs.google.com/formsx` doesn't). A host's `matched` is the
    URL's own host (lowercased, no trailing dot), never its path or query. A marker's `matched`
    is the marker as the signature writes it, the earliest found in the HTML (the first listed
    on a tie); a phrase's is the text as written, the leftmost (a phrase's spaces match any run
    of whitespace, so a phrase split across lines still matches). `Detection.matched` is hidden
    from the `repr`.
  - **Generic rules.** `form_tag` and `tel_link` read the raw HTML with `html.parser` (entities
    decoded, so `&#116;el:` counts); `tel_link`'s `matched` is the stripped `href`, any element's
    (`<a>`, `<area>`). `form_tag`'s `matched` is `form textarea` or `form input[type=tel]`.
    `phone_text` also takes `+44 (0)20 ...` (the usual UK format), an extension of the spec's
    rule.
  - **Garbage.** A non-`str` HTML or text is read as `""`; `embeds` that is a `str`, `bytes` or
    `None` is no embeds; a pair that isn't a 2-tuple or 2-list of a known tag and a `str` URL is
    skipped; an iterable that raises keeps what it gave; a parser error keeps what was read.
  - **`ContactMethods`** checks its values (`run_id` a non-empty `str`, an aware `checked_at`,
    sorted distinct `pages_checked`, all five methods in `found`, each a sorted distinct subset
    of the pages checked) and stores `found` as a read-only mapping in `ContactMethod` order.
    `summarize` takes methods as `ContactMethod` or their `str` values (`ValueError` otherwise).
    `encode`'s `checked_at` is `YYYY-MM-DDTHH:MM:SS.ffffffZ` (UTC); `decode` raises
    `ValueError` for bad JSON, other fields, another version, a bad stamp, a missing or unknown
    method, or values `ContactMethods` refuses.
- **`nextron/domain/crawl.py`** adds `RENDER_PAGES_JOB`, `RENDER_THRESHOLD`, `MAX_RENDERS`,
  `RENDER_TIMEOUT_S` and `needs_render` (a non-`str` raises `TypeError`).
- **The port** (`nextron/ports/renderer.py`) holds `RENDER_FAILED`, `RENDER_TIMEOUT`,
  `RENDER_UNAVAILABLE` and `RENDER_CODES`; `RenderedPage` type-checks its fields.
- **The crawler adapter** (additive).
  - `check_target(url, site)` is now public (the hop's step 1; `HttpCrawler._target` calls it)
    so the renderer checks its first URL exactly as the crawler does.
  - `Resource` lives in `nextron.adapters.crawler`; its `headers` is a read-only mapping of the
    `CORS_HEADERS` the final answer carried (a non-2xx answer's too); its `body` is hidden from
    the `repr`.
  - `_fetch` takes the interval (`None`: unpaced, and then no pacing slot is reserved, so an
    unpaced fetch never delays a later paced one) and an optional `limit`, checked before the
    first hop too, that caps the 30 s deadline.
- **The renderer adapter.**
  - `fetcher` is typed by a `ResourceFetcher` protocol; `browser` is a no-argument callable
    returning a context manager that yields a Playwright `Browser`.
  - **The allowed types** (`ALLOWED`): scripts take `application/javascript`, `text/javascript`,
    `application/x-javascript`, `application/ecmascript` and `text/ecmascript`; stylesheets
    `text/css`; `xhr` and `fetch` `application/json`, any `*/*+json`, `text/plain`, `text/html`
    and `application/xhtml+xml`; the main document `text/html` or `application/xhtml+xml`
    (another 2xx type is the fetch's `fetch_unsupported_type`). The `Accept` sent per type:
    document `text/html,application/xhtml+xml;q=0.9`, script `*/*`, stylesheet
    `text/css,*/*;q=0.1`, data `application/json,text/plain,*/*;q=0.1`.
  - **The caps.** `MAX_REQUESTS` (100) counts the requests actually fetched (a request aborted
    before its fetch doesn't count). `MAX_BYTES` (20 MiB) counts bodies read: a request is
    refused once the total reached it, and a fetched body that takes the total past it is
    aborted, not fulfilled. `MAX_NAVIGATIONS` (6) counts main-frame document fetches.
  - **The main document.** Only the first navigation's fetch failure is raised after `goto`; a
    later navigation that fails is just aborted. The status checked after `goto` and the URL
    the DOM is parsed against are those of the last main document fulfilled. The browser is
    fulfilled at the URL it asked for (redirects are followed by the fetch, as the spec says), so
    it resolves the page's own relative sub-resource URLs against that URL, while the DOM's
    links are parsed against the final URL.
  - **Waits.** A wait is skipped when no time is left; a Playwright timeout is never passed as
    0 (that means "none"): at least 1 ms. A wait failing with anything but a timeout is
    `render_failed`.
  - **The handler** never raises: anything unexpected aborts the request, and an abort or a
    close that raises is ignored. It aborts with `"blockedbyclient"` (`"aborted"` for a later
    main-frame navigation: see the review fixes). A lock guards the counters
    (Playwright calls the handler on the render's thread; the lock is never held across a call
    out).
- **Persistence** (`0022_2_6_contact_methods`). Lengths: `method` 16, `signature` 32, `source` 8,
  `matched` 200. Names: `ix_page_contact_methods_page_id` (kept as the spec asks, beside the
  unique index that starts with `page_id`), `uq_page_contact_methods_page_id_method_signature_source`,
  `ck_page_contact_methods_method`, `ck_page_contact_methods_source` and
  `fk_page_contact_methods_page_id_crawled_pages`. `leads.contact_methods` is added with
  `op.add_column` (no rebuild). The repo adds `set_page_text` (a render's text, hash and
  `changed_at`), and `contact_methods_of(lead_id)` orders by page URL, then method, signature and
  source; `StoredContactMethod.matched` is hidden from the `repr`.
- **The service.**
  - The profile value lists each page by its `url` (the stored key), not its `final_url`;
    `checked_at` is the apply transaction's time. It is computed from the stored
    `page_contact_methods` of the run's pages (`crawled_pages.run_id`), in both paths.
  - A render's detections and its `changed_at` use the time the render finished; `render_pages`
    re-reads the run's pages in its apply transaction, and a render whose page no longer belongs
    to the run counts as a render but writes nothing for it. A render with empty text still
    counts and still adds its detections.
  - Logs: `render_skipped` (`lead_id`, `run_id`, `reason`: `unknown_run`, `other_lead`,
    `merged_lead` or `stale_run`), `render_finished` (`lead_id`, `run_id`, `pages`, `renders`,
    `errors`, sorted distinct `error_codes`, `applied`), and `crawl_finished` gains
    `render_queued`.
  - After a merge the run belongs to the survivor. The merged Lead's queued `render_pages` job
    is skipped (`other_lead`, or `merged_lead` for a run the merge left with it), and no code
    queues one for the survivor: the moved run is rendered only if a job for the survivor is
    run with it (it is then the survivor's latest), which nothing does.
  - **Residual risk (for review):** `render_pages` is keyed `render_pages:<lead>:<date>`, as
    specified. When a Lead is crawled twice on one day while the first `render_pages` job is
    still queued, the second enqueue is absorbed by it; that job then finds a stale run and
    renders nothing, so the second run's pages aren't rendered (and the profile isn't set) until
    the next day's crawl.
- **Jobs.** `render_pages` is registered after `canonicalize_lead` (so the Enrichment group lists
  `enrich_lead`, `canonicalize_lead`, `render_pages`).
- **Wiring (extends the spec's `PlaywrightRenderer(meter=meter)`).** `build_app` builds one
  `HttpCrawler(meter=meter)` and, unless a `renderer=` is injected, passes it to the renderer as
  `fetcher` (it is also the crawl's crawler unless a `crawler=` is injected). One pacing table
  then serves the crawl and the renders, so a render's page fetch keeps at least 1 s from the
  crawl's last request to the site even when `render_pages` runs straight after `enrich_lead`
  (`tests/unit/test_runner_lifespan.py` checks the wiring).
- **Tests and fakes.**
  - `tests/fakes/browser.py`: `FakeBrowsers` (the `browser=` factory), `FakeSite` (what a page
    does: sub-requests as `Sub`, sockets, the DOM, `goto` and wait errors, an `on_goto` hook),
    the routes recording `Fulfilled` or `Aborted` and refusing `continue_`, `fetch` and
    `fallback`, and `FakePlaywright` for `default_browser`. The renderer tests drive the real
    `HttpCrawler` on `httpx2.MockTransport`.
  - `tests/fakes/renderer.py`: `ScriptedRenderer` and `rendered_page`; `tests/fakes/crawler.py`'s
    first-URL check is now the public `first_url_refusal`, shared by both fakes.
  - The fetch-policy contract's crawler driver also drives `fetch_resource`, and its renderer
    driver is a `PlaywrightRenderer` on the recorder's `HttpCrawler` and `FakeBrowsers`; the
    adapter-boundary crawler driver gains `fetch_resource`.
  - `tests/app/test_website_render.py` holds the service's matrix rows (on the
    `test_website_crawl.py` world, whose `WebsiteCrawls` now takes a `ScriptedRenderer`); three
    crawl tests now expect the `render_pages` job their near-empty pages queue.
  - Head bumps, `FILES`, the job lists (`names()`, shapes, handlers), the health panel and
    engine-health lists, `PORT_FAKES`, the port-name set, the adapter packages and the
    classification probe all list the new parts.

**Implementation verification** (targeted; the full suite is the orchestrator's gate):
`tests/unit/test_widgets.py`, `test_renderer_adapter.py`, `test_crawler_adapter.py`,
`test_crawl_domain.py`, `test_runner_lifespan.py`, `tests/app/test_website_crawl.py` and
`test_website_render.py`: 719 passed. `tests/contracts`: 1108 passed and 1 skipped (the opt-in
`ai_eval` check) with `-n 4`; the one other test, in `test_backup_contracts.py`, lost its xdist
worker to a crash under the PC's load and passed on a serial re-run of that file (23 passed; an
earlier serial run of all of `tests/contracts` passed too). The other touched files pass
(`test_migrations`, `test_health_page`, `test_engine_health`, `test_lead_queries`, `test_leads`,
`test_leads_page`, `test_serve`, `test_serve_process`, `test_snapshot`). `lint-imports`: 29 kept,
0 broken. No browser was launched and nothing reached the network.

**Review fixes (pass 1)** (each with its tests):

- **A crawl that finished during the renders.** `render_pages`' apply transaction also re-checks
  the Lead's latest run: when it isn't the run, nothing is written and `render_finished` logs
  `applied: False`, so the newer run's text, methods, counts and profile stand.
- **Refused later navigations.** Once a main-frame document has been fulfilled, a refused
  main-frame navigation (off site, past `MAX_NAVIGATIONS`, a failed fetch, or any other refusal)
  is aborted with `"aborted"` (`ERR_ABORTED` keeps the current document) instead of
  `"blockedbyclient"` (whose error page Chromium would commit); every other refusal keeps
  `"blockedbyclient"`. A main frame left on `chrome-error:` is `render_failed`.
- **Client-side redirects.** A `goto` that fails with Playwright's `Error` (not a timeout) while
  no main-document fetch failed, more than one main-frame document was fulfilled and the last
  was 2xx (a redirect by script before `DOMContentLoaded`) carries on to the waits, and the page
  is read against the last document's final URL. Any other `Error` is still `render_failed`.
- **A hung session.** The browser session runs on a daemon worker thread, waited for at most
  `SESSION_LIMIT_S` (`RENDER_TIMEOUT_S` + 40 s) of real time; past it the render is the
  retryable `render_timeout` and the worker is abandoned (it keeps its browser until it ends or
  the engine exits). A session that dies without an outcome is `render_failed`.
- **The DOM's size.** A `page.content()` longer than `MAX_PAGE_BYTES` characters is
  `render_failed`, before parsing.
- **Wording.** The module docstring and the README say a render stops fetching once 20 MiB have
  been read (the body that crosses the mark is never handed to the page), not that it reads at
  most 20 MiB.
- **More vendors.** Chat: `ngage`, `apexchat`, `intaker`, `podium`, `olark`, `crisp`,
  `freshchat`, `zoho_salesiq`, `liveagent` and `jivochat`, each with hosts, markers (widget hosts
  or loader globals such as `$crisp` and `$zoho.salesiq`) and a phrase. Booking: `setmore`,
  `simplybook`, `youcanbookme`, `zoho_bookings` and `google_appointments` (hosts and a phrase, no
  markers). The docstring and README lists name them; the per-signature tests cover them.
- **`decode`** refuses a `v` that isn't exactly the integer 1 (`1.0` included).
- **Tests.** A `browser=` whose exit raises never changes the outcome; an offline conformance
  test checks every Playwright method, property and keyword the renderer uses (and the fakes
  record) against the real `playwright.sync_api` classes; the log fields (`render_queued`,
  `applied`, the `render_skipped` reasons) are asserted. The fake browser now has a frame `url`,
  a `main_frame_url` override and an `exit_error`.
- **The merge note** above is corrected: nothing queues `render_pages` for the survivor.

**Final verification** (on the final tree, after the review fixes): the spec's verification commands gave 1808 passed and 1 skipped (`ai_eval`); then the full suite through `suite_lock.py` (track-D), run once: 10904 passed and 3 skipped (the two `ai_eval` tests and the symlink test). `lint-imports`: 29 kept, 0 broken. No browser was launched and nothing reached the network. Chromium is not installed on this PC for Playwright 1.65.2, so no real render has been run.

## Spec Change Log

## Review Triage Log

**Pass 1.** The three layers reported 33 raw findings:
- Blind Hunter (B): 14;
- Edge Case Hunter (E): 15;
- Verification Gap (V): 3 gaps plus 1 other finding.

Merging duplicates leaves 26: B1+E8, B2+E6, B3+E7, B4+E1, B5+E4, B6+E13 and B10+E10.
- **Routing:** 12 patch, 0 defer and 14 reject. Eight of the rejects are rejected only because the fix would change the approved spec: #1, #2, #10, #11, #12, #13, #18 and #19.
- **Loopbacks:** there are no `intent_gap` or `bad_spec` entries. Following the track's standing practice, findings whose fix would change the frozen spec are rejected and reported to Mac rather than looped back.
- **Logged in `deferred-work.md`:** the residual risks of #1 with #9 (absence reliability, for Story 2.9), #2 (same-day re-crawl) and #18 (`changed_at` churn, for Story 2.9).

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | B1+E8: `contact_methods` lists a near-empty page as checked even when its render failed or the 3-render cap skipped it. Until Chromium is installed every render fails, and a re-crawl first deletes a page's earlier render-found methods. A JavaScript-only site therefore reads "no chat", the false absence the Intent exists to prevent. | medium | Real (`_set_contact_methods` summarises every page of the run; `replace_contact_methods` deletes every row of the page). But the frozen matrix row "Render fails … `contact_methods` still set" and Decision 1 ("Nothing alerts") prescribe exactly this. | reject: would change the approved spec (reported to Mac). Residual risk for Story 2.9 logged in `deferred-work.md`: an absence must not be claimed while a checked page still `needs_render`. |
| 2 | B2+E6: a second crawl of a Lead on the same UTC day, while the first `render_pages` is still queued, has its enqueue absorbed by that job (key `render_pages:<lead>:<date>`). That job then skips as `stale_run`, so the second run is never rendered and its profile is never set. | medium | Real (`JobQueue.enqueue` returns the live job; the payload carries the old `run_id`). The frozen spec fixes both the key (`lead_job_keys`) and the stale-run skip; the discriminator can't hold a ULID. | reject: would change the approved spec (reported). Logged in `deferred-work.md`. |
| 3 | B3+E7: `_apply_renders` checks only for a merge, not that the run is still the Lead's latest. A crawl that finishes during the renders is overwritten by the stale run's summary. This is reachable: after `canonicalize_lead` moves `web_domain`, the next crawl holds another `domain:` key. | medium | Real (`api.py` `_apply_renders`; the README says a stale run "writes nothing"). | patch (repeat the `latest_run` check in the apply transaction; write nothing, `applied` false) |
| 4 | B4+E1: a main-frame navigation the handler aborts after `goto` (an off-site JavaScript redirect, the 7th navigation) is aborted with `blockedbyclient`. Chromium then commits its `ERR_BLOCKED_BY_CLIENT` error page, whose text replaces the stored text. | medium | Real: Chromium commits an error page for a blocked main-frame navigation, while `ERR_ABORTED` leaves the current document in place. The fake can't show it. | patch (abort a refused later main-frame navigation with `aborted`; read nothing when the main frame's URL is a `chrome-error:` page) |
| 5 | B5+E4: `page.content()` takes no timeout. A page whose script busy-loops after load hangs the render forever: the bulk-lane thread and Chromium stay stuck past `max_runtime`, and every retry adds another. | medium | Real (`Page.content(self, *, include_shadow=None)` has no timeout; the 20 s deadline feeds only `goto` and the waits). | patch (run each render's browser session on a daemon worker thread joined for at most `RENDER_TIMEOUT_S` plus a grace; past it, `render_timeout` and the worker is abandoned) |
| 6 | B6+E13: a merge leaves the survivor's `contact_methods` as it was, and the merged Lead's run that moved to the survivor is never rendered (its job is skipped as `other_lead` or `merged_lead`). | low | Real, but the merge that follows a crawl is canonicalisation: the older Lead survives because it already holds the landing domain, so its own crawl covers that site, and its next crawl refreshes the profile. Fixing it adds merge-time recomputation. | reject (see #26 for the misleading claim) |
| 7 | B7: the `wpcf7` marker matches every page of a site with Contact Form 7 installed (its assets load site-wide). `smith.ai` as a bare substring matches `jane.smith.aitken@…` or a plain link to smith.ai. | low | Real, but it only over-reports per page: a CF7 site has a form somewhere, and the `smith.ai` collisions are rare. The spec lists these markers. | reject (low; spec-listed markers) |
| 8 | B8: the signature lists miss the common US law-intake chat vendors (Ngage, ApexChat, Intaker), common small-business chat (Podium, Olark, Crisp, Freshchat, Zoho SalesIQ, LiveAgent, JivoChat) and booking tools (Setmore, SimplyBook.me, YouCanBookMe, Zoho Bookings, Google Calendar appointment pages). Each firm using one reads "contact form only". | medium | Real, and the AC gives its lists "for example". Adding signatures is data that the existing per-signature tests cover automatically. | patch (add them, with each chat and booking signature's host and phrase; the README list follows) |
| 9 | B9: on a text-rich page a tag manager (GTM) often injects the chat widget, so the raw HTML never names it, the page is never rendered, and "no chat" is recorded. | medium | Real, but the 500-character render rule is the AC's and the spine's. | reject: would change the approved spec (reported). Logged with #1 for Story 2.9. |
| 10 | B10+E10: any non-empty render replaces the crawled text, even a shorter one (a cookie wall or an error body). | low | Real. Decision 6 ("only when it isn't empty") is approved, and the crawled text is under 500 characters by construction. | reject: would change the approved spec |
| 11 | B11: renders pace the main document at 1 s, not at the robots.txt `Crawl-delay`; JavaScript navigations aren't robots-checked; same-site sub-resources are unpaced. | low | Real, but Decision 4 (approved) sets exactly this; a render fetches a page the crawl already found allowed. | reject: would change the approved spec |
| 12 | B12: `page_contact_methods.matched` keeps a phone number with no purge. | low | Decision 10 (approved): `redact`, not purged. | reject: would change the approved spec (reported) |
| 13 | B13: no `Origin` header goes upstream, so APIs that echo an allowed origin send no CORS header and the browser blocks the data; a single-page app may then render nearly empty. | medium | Real. Decision 3 (approved) forwards no cookie, referrer or origin, and the README says so. | reject: would change the approved spec (reported) |
| 14 | B14: the module docstring and README say a render "reads at most 20 MiB". The cap is checked before each fetch, so the last body can take it to about 25 MiB. | low | Real. The fix is a direct correction of the wording. | patch (docs) |
| 15 | E2: after a server redirect of the main document, the browser gets the body under the pre-redirect URL, so relative script paths resolve against the wrong URL. | low | Real in principle, but `render_pages` renders the crawl's stored `final_url`, so a redirect at render time is rare. A synthetic 302 can't be verified without a real browser and doubles the fetch. | reject |
| 16 | E3: a client-side redirect before `DOMContentLoaded` interrupts `goto` ("navigation interrupted by another navigation"), which raises Playwright's `Error`. Every JavaScript-redirecting site becomes `render_failed`. | medium | Real (Playwright's `goto` throws when another navigation commits first; `_navigate` maps any `PlaywrightError` to `FAILED`). | patch (when the first main document was fulfilled 2xx and a later main-frame navigation started, carry on to the waits and read the page) |
| 17 | E5: the rendered DOM isn't size-capped, so a script-built huge DOM reaches `parse_html` and `widgets.detect` unbounded. | medium | Real. AD-9 caps responses at 5 MB, and the renderer's answer should hold to it. The fix is one guard. | patch (`page.content()` longer than `MAX_PAGE_BYTES` characters is `render_failed`) |
| 18 | E9: for a JavaScript page, `changed_at` moves on every crawl (shell hash vs the stored rendered hash) and again on every render, though the site never changed. Story 2.9 would re-extract it each run. | medium | Real, but the frozen text says `changed_at` moves "when the hash changed" against the stored text. Fixing it needs a second hash column. | reject: would change the approved spec. Logged for Story 2.9: decide "changed" against the hash last extracted. |
| 19 | E11: a `render_pages` job deferred all month (budget) or dead leaves `contact_methods` unset, although the crawl detected methods. | low | Real. Decision 9 (approved) writes the profile only once detection is complete; an unset profile only means no absence can be claimed. | reject: would change the approved spec |
| 20 | E12: `run_pages(run_id)` has no Lead filter, so after a merge the loser's leftover pages would be rendered and summarised for the survivor. | false | No production path calls `render_pages` or `_set_contact_methods` for a run moved by a merge: the only enqueue carries the crawled Lead, whose moved run is then skipped as `other_lead`, and `_set_contact_methods` runs in the crawl's own apply before any merge. | reject |
| 21 | E14: `queries.research.contact_methods` raises `ValueError` on a corrupt stored value. | false | Only `encode`, through `_set_contact_methods`, writes the column, and nothing reads it on a page yet (no Lead-page change in this story). | reject |
| 22 | E15: `decode` accepts `"v": 1.0`, which `encode` never writes, against its docstring. | low | Real. The fix is a direct correction. | patch (`v` must be the int 1) |
| 23 | V1: a browser whose close raises (Chromium crashed: "Target closed") is never tested. Only the context's close is. | gap (pre-verified) | Filed with evidence (`FakeBrowsers._open` can't raise on exit). | patch (a test) |
| 24 | V2: the Playwright calls are tested only against fakes that accept any keyword; nothing checks them against the real `playwright.sync_api` signatures. | gap (pre-verified) | Filed with evidence. Today every keyword exists (checked with `inspect`), so this is a guard against a future bump. | patch (an offline signature-conformance test) |
| 25 | V3: the log fields `crawl_finished.render_queued`, `render_finished.applied` and the skip reasons `unknown_run`, `other_lead` and `merged_lead` are never asserted. | gap (pre-verified) | Filed with evidence. | patch (caplog assertions) |
| 26 | V-o1: the Implementation Notes and a test comment say "the survivor's `render_pages` renders" a moved run, but no code queues that job; the test calls it by hand. | low | Real (the only enqueue is in `enrich_lead`'s apply). The fix is a direct correction of a comment and a note. | patch |

## Verification

**Commands** (from `e:/Nextron-track4/nextron-engine`):
- `uv run pytest -q tests/unit/test_widgets.py tests/unit/test_renderer_adapter.py tests/unit/test_crawler_adapter.py tests/app/test_website_crawl.py tests/contracts` -- all pass.
- `uv run lint-imports` -- every contract kept.
