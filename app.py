"""
=======================================================================
 Slim-LIMS report-generation API
=======================================================================

 PURPOSE
    A thin FastAPI layer over slim_report_engine's already-tested
    report-generation pipeline, exposed over HTTP so more than one VBA
    hook (today: one, but the API is built to support more) can share a
    single always-on process instead of each embedding its own copy of
    the domain logic or spinning up its own one-shot process.

    This deliberately does NOT re-derive any business logic. It imports
    slim_report_engine.cli.run() unchanged.

 REQUEST/RESPONSE CONTRACT
    The workbook is NOT uploaded over HTTP. The caller (VBA, via
    WinHTTP) and this server both reach the same lab network share, so
    the request just carries the shared path -- no need to move the
    file itself over the wire.

    POST /generate-report
        Request body (JSON):  {"input_path": "<path on the shared drive>",
                                "hook_version": "<VBA hook's own version, optional>"}
        Success (200):         {"path": "<output workbook path>", "duration_seconds": <float>}
        Recognized data error (422): {"detail": "<message>", "duration_seconds": <float>}
        Unexpected error (500):      {"detail": "<traceback>", "duration_seconds": <float>}

        duration_seconds times only the run() call itself (server-side
        processing), separate from whatever the VBA caller measures for
        the full round trip -- comparing the two is how to tell network/
        WinHTTP overhead apart from actual report-generation time,
        instead of guessing. run() itself logs a further phase breakdown
        (service/cache construction, parsing, section building, writing)
        at INFO level -- see this process's own console/log output.

    GET /health
        {"status": "ok"} -- used by the Scheduled Task watchdog once this
        runs as an NSSM-wrapped Windows Service.

    POST /refresh-cache
        {"status": "ok", "duration_seconds": <float>} -- rebuilds the
        customer/chemical/analysis/element/specification caches from
        Postgres. Call this after adding new data to the database; a
        service restart also refreshes it. See CACHING below.

 UNRESOLVED CUSTOMER/CHEMICAL/REPORT-FOLDER RESOLUTION
    The legacy VBA macro popped up a UserForm right there in Excel when a
    customer/chemical wasn't found, let the technician pick an existing
    one or add a new one, and continued -- the new HTTP path had no
    equivalent, just a dead-end error. This closes that gap, but
    deliberately NOT by building a VBA UserForm again: that would put
    real logic back in VBA and make it untestable without opening Excel,
    undoing the main benefit of this whole move. Instead:

    - /generate-report, on an unresolved customer/chemical/report-folder,
      returns {"error_type": "unresolved_customer"|"unresolved_chemical"|
       "unresolved_report_folder", "detail": "...", "resolve_url": "<full
       URL>", "token": "...", "duration_seconds": <float>} instead of a
       plain 422 message.
    - VBA opens resolve_url in the technician's own default browser (their
      machine, their screen -- not LAB-SERVER's) and short-polls
      GET /resolution-status/{token} every ~1-2s rather than holding one
      long connection open (more robust, and each poll is cheap/fast).
    - GET/POST /resolve/customer, /resolve/chemical, and /resolve/folder
      are plain server-rendered HTML forms -- pick an existing entry from
      a dropdown (built from the same cached services, no separate API
      needed) or fill in a new one/paste a folder path. The actual work is
      the same already-tested CustomerService/ChemicalService/
      ReportFolderService create_*/add_form_*/set_folder methods
      slim-domain already has; this is just an HTTP-facing wrapper that
      also means the whole flow is testable with curl/pytest, no Excel
      involved. Report-folder resolution is a one-time cost per customer:
      Testing_Requests and Reports folder names on the lab share are
      confirmed NOT to follow the same convention (real drift, and at
      least one genuinely one-to-many case), so it can't be auto-derived
      from the customer name -- once mapped via this page, though,
      ReportFolderService remembers it for every future report to that
      customer, and re-asks only if the mapped folder is later renamed or
      removed on the share.
    - Once VBA sees {"resolved": true}, it automatically retries the
      original /generate-report call -- no second button click.
    - Pending resolutions live in an in-memory dict (_pending_resolutions),
      pruned opportunistically by age -- fine for a low-frequency,
      short-lived workflow; not persisted across a restart.
    - /resolve/* and /resolution-status/* are deliberately NOT behind
      X-API-Key: they're opened directly in a browser (which can't attach
      a custom header), and the random token itself is the practical
      protection -- consistent with /health's "low-sensitivity, trust the
      lab network" treatment.

 AUTH
    /generate-report and /refresh-cache require a shared-secret header:
        X-API-Key: <the real API_KEY value from .env>
    Missing/wrong key -> 401. If API_KEY isn't set in .env at all, auth
    is skipped entirely (local dev / the test suite) rather than locking
    everyone out by default -- deliberate, not an oversight. /health
    stays open (nothing sensitive in it, and the watchdog script doesn't
    need to know the key).

 CACHING
    Measured: rebuilding the 5 domain services' caches from Postgres
    takes ~0.34s -- ~92% of total run() time for a typical request. A
    fresh rebuild on every call is fine for a short-lived, one-shot
    invocation (rebuilding once is unavoidable when the process doesn't
    outlive a single report anyway) but pure waste for a persistent
    server handling many requests. So this builds those services ONCE
    at startup (see `_services` / `_build_services_cached`
    below) and reuses them for every /generate-report call, at the cost
    of not seeing new customers/chemicals/etc. added to Postgres until
    /refresh-cache is called or the service restarts -- a deliberate
    tradeoff, not an oversight.

 RUNNING
    See README.md for setup (path installs of slim-domain and
    report-engine) and how this gets wrapped as a Windows
    Service via NSSM.
=======================================================================
"""

from __future__ import annotations

import html
import logging
import os
import secrets
import threading
import time
import traceback
from pathlib import Path
from typing import Annotated

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from infrastructure.network_paths import resolve_mapped_drive
from slim_report_engine.cli import (
    ReportEngineError,
    ReportEngineServices,
    UnresolvedChemicalReportError,
    UnresolvedCustomerReportError,
    UnresolvedReportFolderReportError,
    build_services,
    run,
)

# INFO so run()'s own phase-timing log lines (service/cache construction,
# parsing, section building, writing) actually show up -- added to find
# out where time really goes before optimizing anything on a guess.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# This app has no pyproject.toml/packaging (it's the running service
# itself, never installed as a dependency), so there's no
# importlib.metadata version to read the way slim-report-engine/
# slim-domain do -- a plain constant, bumped by hand alongside
# CHANGELOG.md entries, is the simplest equivalent. Logged at startup and
# in /health so a report generated at a given time can be tied back to
# which version of this API was actually running, not just which version
# of the underlying report-generation packages it called into.
APP_VERSION = "0.6.13"

# Read once at import time -- by the time this line runs, `from
# slim_report_engine.cli import ...` above has already triggered
# infrastructure.database's load_dotenv() as a side effect, same as how
# DB_URL gets picked up. None (not set in .env) means auth is skipped
# entirely -- see _require_api_key.
API_KEY = os.environ.get("API_KEY")

if API_KEY is None:
    # Deliberate for local dev/the test suite (see _require_api_key) --
    # but silent otherwise, so a real deployment that's missing this
    # line in .env (a fresh copy of .env.template never filled in, an
    # accidental edit) would go fully unauthenticated with nothing to
    # notice it happened. Loud on purpose: this should be impossible to
    # miss in server.log even if nobody's actively watching.
    logger.warning(
        "API_KEY is not set -- /generate-report and /refresh-cache are UNAUTHENTICATED. "
        "This is expected for local dev/tests only; a real deployment must set API_KEY in .env."
    )

# TESTING_REQUESTS_ROOT (.env, defaults to the real share): confirmed
# real gap (direct user question) -- without this, /generate-report
# would open ANY path the caller supplies, not just something under the
# real Testing Requests tree. Anyone with the API key (which, per the
# VBA hook's own design, is visible to anyone with Alt+F11 access to the
# deployed workbook) could otherwise ask the server to try to read any
# file it can see.
TESTING_REQUESTS_ROOT = resolve_mapped_drive(os.environ.get("TESTING_REQUESTS_ROOT") or r"S:\Testing_Requests")


def _require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Shared-secret auth for the VBA -> server call. If API_KEY isn't
    configured (local dev, the test suite), skips the check entirely
    rather than locking everyone out by default -- a deliberate choice
    for a lab-internal tool, not an oversight. Uses compare_digest, not
    ==, so comparing the key doesn't leak timing information about how
    much of it matched."""
    if API_KEY is None:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key header")


def _is_under_testing_requests_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(TESTING_REQUESTS_ROOT.resolve())
        return True
    except ValueError:
        return False

# Built once at startup, reused for every /generate-report call, replaced
# wholesale (not mutated in place) by /refresh-cache. The lock only
# guards the build-and-swap itself -- reading the current value for a
# request needs no lock, a single reference assignment is atomic enough
# under the GIL, and a request racing a concurrent refresh just uses
# whichever complete ReportEngineServices was current at that instant.
_services: ReportEngineServices | None = None
_services_lock = threading.Lock()

# Pending customer/chemical resolutions, keyed by an opaque token. Each
# entry: {"kind": "customer"|"chemical"|"folder", "fields": {...},
# "resolved": bool, "cancelled": bool, "created_at": float}. "cancelled"
# is the explicit "this needs manual correction, don't retry" path
# (2026-09-22) -- distinct from just letting VBA's 10-minute wait time
# out, so a technician who immediately recognizes a bad customer form
# (e.g. an ambiguous chemical field) can say so right away instead of
# staring at Excel for 10 minutes for the same eventual outcome. In-memory
# only -- this is a short-lived, low-
# frequency workflow (a browser tab open for however long one person
# takes to fill in a form), not something that needs to survive a
# restart. Pruned opportunistically (see _prune_stale_resolutions) rather
# than with a background thread, since volume is low.
_pending_resolutions: dict[str, dict] = {}
_pending_lock = threading.Lock()
_PENDING_MAX_AGE_SECONDS = 60 * 60  # 1 hour

# How long the resolve page's own heartbeat (see _HEARTBEAT_SCRIPT below) can
# go silent before VBA is told the technician's browser is gone -- closing
# the tab, walking away with it backgrounded, a crash, a dropped connection
# all look the same here: the pings just stop. 90s, not something tighter
# like 30-45s, specifically to absorb real browser timer throttling in a
# backgrounded/unfocused tab (confirmed real behavior, not a hypothetical --
# most browsers slow setInterval in an inactive tab) without mistaking
# "still open, just not focused right now" for "gone." Still a large
# improvement over the previous behavior, which had no faster signal at all
# short of VBA's own 10-minute poll timeout.
_ABANDONED_AFTER_SECONDS = 90


def _prune_stale_resolutions() -> None:
    cutoff = time.time() - _PENDING_MAX_AGE_SECONDS
    with _pending_lock:
        stale = [token for token, entry in _pending_resolutions.items() if entry["created_at"] < cutoff]
        for token in stale:
            del _pending_resolutions[token]


def _new_pending_resolution(kind: str, fields: dict) -> str:
    _prune_stale_resolutions()
    token = secrets.token_urlsafe(16)
    now = time.time()
    with _pending_lock:
        _pending_resolutions[token] = {
            "kind": kind,
            "fields": fields,
            "resolved": False,
            "cancelled": False,
            "created_at": now,
            "last_seen": now,
        }
    return token


def _get_services() -> ReportEngineServices:
    """The cached ReportEngineServices, building it now if the startup
    cache never succeeded -- so the resolve pages work even before a
    working /refresh-cache, rather than 500ing on a None."""
    global _services
    if _services is None:
        _refresh_services()
    return _services


def _refresh_services() -> float:
    global _services
    start = time.perf_counter()
    with _services_lock:
        _services = build_services(db_url=None)
    return time.perf_counter() - start


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        duration = _refresh_services()
        logger.info("Startup: built service caches in %.3fs (Slim-LIMS v%s)", duration, APP_VERSION)
    except Exception:
        # Don't crash the server if Postgres isn't reachable yet --
        # /generate-report falls back to building fresh per-request (see
        # below) until a working /refresh-cache succeeds.
        logger.exception("Startup: failed to build service caches -- will build fresh per-request until refreshed")
    yield


app = FastAPI(title="Slim-LIMS report-generation API", lifespan=_lifespan)


class GenerateReportRequest(BaseModel):
    input_path: str
    # Optional so an older, not-yet-updated VBA hook that predates this
    # field doesn't break -- it just logs as "unknown" below rather than
    # failing validation.
    hook_version: str | None = None


@app.get("/health")
def health() -> dict:
    # Deliberately reads the module-level `_services` directly, NOT
    # _get_services() -- that would build services (needs Postgres) on
    # every call, breaking /health's whole point: staying up (for the
    # eventual NSSM watchdog) even when the DB is unreachable. A `null`
    # test_mode just means services haven't built yet, not "definitely
    # off". Also guards against a test-double _services (or any future
    # partially-built one) that doesn't have report_folder_svc at all.
    try:
        test_mode = _services.report_folder_svc.test_root is not None if _services else None
    except AttributeError:
        test_mode = None
    return {"status": "ok", "test_mode": test_mode, "version": APP_VERSION}


@app.post("/refresh-cache", dependencies=[Depends(_require_api_key)])
def refresh_cache() -> JSONResponse:
    # Confirmed real gap (2026-09-29 failure-path audit, follow-up pass):
    # _refresh_services() had no try/except -- a DB outage (the most
    # plausible real trigger for calling this at all, e.g. right after a
    # Postgres restart) raised straight to FastAPI's default handler,
    # a bare 500 with no body, same class of gap as the three resolve/*
    # handlers fixed in 0.6.9. This is an admin-only, API-key-gated
    # endpoint (Jamie/tooling, not a lab technician), but the same "tell
    # the caller SOMETHING" principle applies.
    try:
        duration = _refresh_services()
    except Exception as e:
        logger.exception("refresh_cache: failed to rebuild service caches")
        return JSONResponse(status_code=500, content={"detail": f"Could not refresh service caches: {e}"})
    logger.info("Refreshed service caches in %.3fs", duration)
    return JSONResponse(content={"status": "ok", "duration_seconds": duration})


@app.post("/generate-report", dependencies=[Depends(_require_api_key)])
def generate_report(body: GenerateReportRequest, http_request: Request):
    # This handler previously logged nothing at all on receipt -- the only
    # trace of a request was cli.py's own phase-timing/SUCCESS lines,
    # visible only once run() actually got underway. Logging the hook
    # version here closes that gap and gives full round-trip
    # traceability: which deployed copy of the VBA hook made this
    # request, not just which engine/domain versions processed it.
    logger.info(
        "generate_report: request for %s (hook v%s)",
        body.input_path, body.hook_version or "unknown",
    )

    # VBA always sends whatever ActiveWorkbook.FullName resolves to on the
    # TECHNICIAN's machine -- always a mapped-drive-letter path (e.g.
    # S:\...), since that's how they navigate to it. Confirmed real: this
    # service (Windows Service, even running under a real interactive
    # user's own account) does NOT see that account's drive mappings --
    # they're tied to the interactive logon session that created them, not
    # the account itself. Translating to the real UNC path here is what
    # actually lets the service open the file, regardless of which
    # account or session runs it going forward.
    input_path = resolve_mapped_drive(body.input_path)

    if not _is_under_testing_requests_root(input_path):
        return JSONResponse(
            status_code=422,
            content={
                "detail": f"input_path must be under {TESTING_REQUESTS_ROOT}",
                "duration_seconds": 0.0,
            },
        )

    if not input_path.exists():
        return JSONResponse(
            status_code=422,
            content={"detail": f"input file not found: {input_path}", "duration_seconds": 0.0},
        )

    # output_path=None: unlike a bare CLI invocation of run() (which has
    # no real notion of a per-customer Reports folder and just writes
    # next to the input), let run() pick its own destination -- the customer's mapped report
    # folder plus a computed filename (see ReportFolderService /
    # report_filename() in slim_report_engine). Raises
    # UnresolvedReportFolderReportError, handled below the same way as an
    # unresolved customer/chemical, if that mapping doesn't exist yet.
    # run_info: populated in place by run() (its own `info` param, additive
    # and off-by-default -- see cli.py's docstring) with "the big gates
    # the code successfully clears" (direct user request, 2026-09-29):
    # customer/type/sample-count at parse, sheet-count at build, a timing
    # for each, and the engine/domain versions that actually processed
    # this report -- merged into the success response below so the VBA
    # hook can log them locally, without needing server log access to see
    # what happened beyond "it worked."
    start = time.perf_counter()
    run_info: dict = {}
    try:
        result_path = run(input_path, None, db_url=None, services=_services, info=run_info)
    except UnresolvedCustomerReportError as e:
        duration = time.perf_counter() - start
        token = _new_pending_resolution(
            "customer",
            {"form_customer": e.form_customer, "form_address": e.form_address, "form_address2": e.form_address2},
        )
        resolve_url = str(http_request.base_url) + f"resolve/customer?token={token}"
        return JSONResponse(
            status_code=422,
            content={
                "error_type": "unresolved_customer",
                "detail": str(e),
                "resolve_url": resolve_url,
                "token": token,
                "duration_seconds": duration,
            },
        )
    except UnresolvedChemicalReportError as e:
        duration = time.perf_counter() - start
        token = _new_pending_resolution("chemical", {"form_name": e.form_name})
        resolve_url = str(http_request.base_url) + f"resolve/chemical?token={token}"
        return JSONResponse(
            status_code=422,
            content={
                "error_type": "unresolved_chemical",
                "detail": str(e),
                "resolve_url": resolve_url,
                "token": token,
                "duration_seconds": duration,
            },
        )
    except UnresolvedReportFolderReportError as e:
        duration = time.perf_counter() - start
        token = _new_pending_resolution(
            "folder",
            {"customer_id": e.customer_id, "customer_name": e.customer_name, "location": e.location},
        )
        resolve_url = str(http_request.base_url) + f"resolve/folder?token={token}"
        return JSONResponse(
            status_code=422,
            content={
                "error_type": "unresolved_report_folder",
                "detail": str(e),
                "resolve_url": resolve_url,
                "token": token,
                "duration_seconds": duration,
            },
        )
    except ReportEngineError as e:
        duration = time.perf_counter() - start
        return JSONResponse(status_code=422, content={"detail": str(e), "duration_seconds": duration})
    except Exception:
        duration = time.perf_counter() - start
        return JSONResponse(
            status_code=500,
            content={"detail": traceback.format_exc(), "duration_seconds": duration},
        )
    duration = time.perf_counter() - start

    try:
        test_mode = _get_services().report_folder_svc.test_root is not None
    except AttributeError:
        test_mode = None
    # app_version alongside run_info's own engine_version/domain_version:
    # the last piece of the version-traceability chain (hook_version ->
    # this field -> engine/domain versions) that wasn't already on this
    # response -- previously only /health exposed which build of
    # Slim-LIMS itself served a request.
    return {
        "path": str(result_path),
        "duration_seconds": duration,
        "test_mode": test_mode,
        "app_version": APP_VERSION,
        **run_info,
    }


def _test_mode_banner(kind: str | None = None) -> str:
    """A persistent, hard-to-miss banner on every browser page whenever
    REPORT_FOLDER_TEST_ROOT is configured -- so Jamie and Quality can
    always tell, at a glance, whether a given save is going anywhere near
    a real customer's real S:\\Reports folder. Best-effort: if services
    can't even build (e.g. Postgres down), fails quietly rather than
    breaking page rendering over a cosmetic banner.

    kind: which resolve page this is for. Confirmed real (2026-09-22):
    the banner's original wording ("nothing saved here can reach a real
    Reports folder") is only true for the FOLDER page -- test mode has
    never sandboxed customer/chemical catalog writes at all, since
    REPORT_FOLDER_TEST_ROOT only ever confined the file-output side. A
    technician reading the generic wording on the customer/chemical
    pages could reasonably assume broader protection than actually
    exists -- a new customer or chemical created there is real and
    permanent regardless of test mode. Say so plainly instead."""
    try:
        test_root = _get_services().report_folder_svc.test_root
    except Exception:
        return ""
    if test_root is None:
        return ""
    extra = ""
    if kind in ("customer", "chemical"):
        extra = (
            f" <strong>This does not apply to what you do on this page</strong> -- "
            f"any customer/chemical you add or map here is written to the real "
            f"database immediately, test mode or not."
        )
    return f"""<div style="background:#fff3cd;border:1px solid #ffe08a;border-radius:4px;
        padding:0.6rem 1rem;margin-bottom:1.5rem;font-size:0.9rem;">
        <strong>TEST MODE</strong> -- report folders are confined to
        <code>{html.escape(str(test_root))}</code>. Nothing saved there can reach a real
        customer's real Reports folder.{extra}</div>"""


def _heartbeat_script(token: str) -> str:
    """Tells the server this resolve page is still open, every 15s, plus
    immediately on load and again whenever the tab regains focus (a
    technician switching to Excel and back shouldn't cost a missed beat
    right when visibility resumes). Fire-and-forget: a failed heartbeat
    (network hiccup, server restart) just means this poll's ping didn't
    land -- the next one 15s later tries again, same as VBA's own
    short-poll design elsewhere in this flow. keepalive lets the final
    heartbeat before a tab close actually leave the browser instead of
    being cancelled with the page, though nothing here depends on that
    one succeeding -- the abandoned-detection on the server side works
    the same way (via silence) whether or not it does."""
    return f"""<script>
(function() {{
  var url = "/resolution-heartbeat/{token}";
  function beat() {{ fetch(url, {{method: "POST", keepalive: true}}).catch(function() {{}}); }}
  beat();
  setInterval(beat, 15000);
  document.addEventListener("visibilitychange", function() {{
    if (document.visibilityState === "visible") beat();
  }});
}})();
</script>"""


def _page(title: str, body: str, kind: str | None = None, token: str | None = None) -> HTMLResponse:
    """Minimal shared page shell -- plain HTML, no JS framework, matching
    the rest of this project's "simple and functional" style. This is an
    internal lab tool opened a handful of times, not a product.

    kind: passed straight through to _test_mode_banner() -- see there.
    token: only set for a live resolve-form page (not a terminal/
    confirmation page, which has nothing left to keep alive) -- when set,
    includes the heartbeat script so abandonment can be detected faster
    than VBA's own 10-minute poll timeout (see _ABANDONED_AFTER_SECONDS)."""
    heartbeat = _heartbeat_script(token) if token else ""
    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 32rem; margin: 2rem auto; padding: 0 1rem; }}
  fieldset {{ margin-bottom: 1.5rem; }}
  label {{ display: block; margin-top: 0.5rem; font-size: 0.9rem; }}
  input, select {{ width: 100%; padding: 0.4rem; margin-top: 0.15rem; box-sizing: border-box; }}
  button {{ margin-top: 0.75rem; padding: 0.5rem 1rem; }}
</style></head>
<body>{_test_mode_banner(kind)}{body}{heartbeat}</body></html>""")


@app.get("/resolve/customer", response_class=HTMLResponse)
def resolve_customer_page(token: str):
    pending = _pending_resolutions.get(token)
    if pending is None or pending["kind"] != "customer":
        return _page("Link expired", "<h2>This resolution link is invalid or has expired.</h2>", kind="customer")

    fields = pending["fields"]
    customers = sorted(_get_services().customer_svc.all_customers(), key=lambda c: c.name.lower())
    options = "".join(f'<option value="{c.id}">{html.escape(c.name)}</option>' for c in customers)

    return _page("Resolve customer", f"""
<h2>Customer not found</h2>
<p>The form has customer <b>{html.escape(fields['form_customer'])}</b>
   / {html.escape(fields['form_address'])} / {html.escape(fields['form_address2'])},
   which doesn't match anything in the database.</p>

<form method="post" action="/resolve/customer">
  <input type="hidden" name="token" value="{html.escape(token)}">

  <label>Your initials (required either way below)
    <input name="created_by">
  </label>

  <fieldset>
    <legend>Map to an existing customer</legend>
    <label>Existing customer
      <select name="existing_customer_id">
        <option value="">-- select --</option>
        {options}
      </select>
    </label>
    <button type="submit" name="mode" value="existing">Use this customer</button>
  </fieldset>

  <fieldset>
    <legend>Or add a new customer</legend>
    <label>Name <input name="name" value="{html.escape(fields['form_customer'])}"></label>
    <label>Street address <input name="street_address"></label>
    <label>City <input name="city"></label>
    <label>State <input name="state"></label>
    <label>Postal code <input name="postal_code"></label>
    <label>Country <input name="country" value="USA"></label>
    <button type="submit" name="mode" value="new">Add new customer</button>
  </fieldset>
</form>

<hr style="margin-top:2rem;border:none;border-top:1px solid #ddd;">
<form method="post" action="/resolve/cancel" style="margin-top:1rem;">
  <input type="hidden" name="token" value="{html.escape(token)}">
  <p style="font-size:0.85rem;color:#555;margin-bottom:0.5rem;">
    If there's a problem with the Testing Request form itself (an ambiguous or
    incorrect entry), don't guess at a resolution:
  </p>
  <button type="submit">Cancel</button>
</form>
""", kind="customer", token=token)


@app.post("/resolve/customer", response_class=HTMLResponse)
def resolve_customer_submit(
    token: str = Form(...),
    mode: str = Form(...),
    existing_customer_id: str = Form(""),
    name: str = Form(""),
    street_address: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    postal_code: str = Form(""),
    country: str = Form(""),
    created_by: str = Form(""),
):
    pending = _pending_resolutions.get(token)
    if pending is None or pending["kind"] != "customer":
        return _page("Link expired", "<h2>This resolution link is invalid or has expired.</h2>", kind="customer")
    with _pending_lock:
        if pending["resolved"] or pending["cancelled"]:
            # A token is only ever meant to be submitted once -- reachable
            # via a stale browser back-button, a double-click, or a
            # resubmit confirmation dialog, none of them malicious.
            # Re-running this would call create_customer()/add_form_key()
            # a second time for the same mapping.
            return _page("Already resolved", "<h2>Already resolved</h2><p>This was already handled -- you can close this tab.</p>", kind="customer")
        # Claim BEFORE doing the real work, not after (2026-09-24, real
        # stress-test finding): checking the flag and setting it were two
        # separate steps with no lock across both, so two near-simultaneous
        # submits (double-click, browser resubmit) could both pass the
        # check before either claimed it -- create_customer/add_form_key
        # aren't idempotent, so that raced into two real duplicate rows.
        # Claiming inside the same lock as the check closes that window.
        pending["resolved"] = True

    customer_svc = _get_services().customer_svc
    fields = pending["fields"]
    existing_name_notice = ""
    try:
        if mode == "existing":
            if not existing_customer_id:
                raise ValueError("Please select an existing customer, or use the 'add new' section instead.")
            customer_id = int(existing_customer_id)
        else:
            # Self-heal instead of dead-ending (2026-09-24, direct user
            # request): a technician moving quickly can type a name that's
            # an exact case-insensitive match for a customer that already
            # exists (previously: create_customer() raised, showing a bare
            # "Could not resolve" error and forcing a manual do-over via
            # the "existing" dropdown instead). Checked BEFORE attempting
            # the create so this never even reaches create_customer()'s own
            # uniqueness error -- exact match only (get_by_name), same
            # case-insensitivity the DB's own CITEXT constraint already
            # enforces, never the substring matching that caused a real
            # near-miss earlier this project.
            existing = customer_svc.get_by_name(name)
            if existing is not None:
                customer_id = existing.id
                existing_name_notice = (
                    f'<p style="background:#fff3cd;border:1px solid #ffe69c;padding:0.75rem 1rem;'
                    f'border-radius:4px;">Note: <b>{html.escape(existing.name)}</b> already existed '
                    "in the system -- this form was linked to the existing record instead of "
                    "creating a duplicate.</p>"
                )
            else:
                customer_id = customer_svc.create_customer(
                    name, street_address, city, state, postal_code, country, created_by=created_by
                ).id
        customer_svc.add_form_key(
            fields["form_customer"], fields["form_address"], fields["form_address2"], customer_id,
            created_by=created_by,
        )
    except ValueError as e:
        # A real, expected, fixable problem (bad input, a name collision
        # self-heal couldn't resolve, etc.) -- release the claim so the
        # technician can actually correct it and resubmit, rather than
        # being told "already resolved" for something that never happened.
        with _pending_lock:
            pending["resolved"] = False
        return _page("Could not resolve", f"""
<h2>Could not resolve customer</h2>
<p>{html.escape(str(e))}</p>
<p><a href="/resolve/customer?token={html.escape(token)}">Go back and try again</a></p>
""", kind="customer")
    except Exception:
        # Anything unexpected (a real bug, a transient DB hiccup) must
        # release the claim too, not just ValueError -- otherwise this
        # token is permanently stuck "resolved" with nothing actually
        # saved, and a legitimate retry would wrongly see "already
        # resolved" forever. Logged (not just re-raised) and answered with
        # a real page: confirmed real gap (2026-09-29 failure-path audit)
        # -- this app registers no exception_handler and runs without
        # debug=True, so a bare `raise` here used to fall through to
        # Starlette's default handler, which returns a 500 with NO body at
        # all -- a technician got literally nothing, not even a generic
        # apology, while /generate-report's own catch-all at least shows a
        # traceback. Logging preserves the diagnostic detail (server-side,
        # where it belongs) without leaking a raw traceback onto a page a
        # lab technician reads directly.
        with _pending_lock:
            pending["resolved"] = False
        logger.exception("resolve_customer_submit: unexpected error")
        return _page("Could not resolve", f"""
<h2>Something went wrong resolving this customer</h2>
<p>An unexpected error occurred and this could not be completed. Try again, or contact support
   if it keeps happening.</p>
<p><a href="/resolve/customer?token={html.escape(token)}">Go back and try again</a></p>
""", kind="customer")

    return _page("Resolved", f"<h2>Done</h2>{existing_name_notice}<p>You can close this tab and return to Excel -- the report will continue automatically.</p>", kind="customer")


@app.get("/resolve/chemical", response_class=HTMLResponse)
def resolve_chemical_page(token: str):
    pending = _pending_resolutions.get(token)
    if pending is None or pending["kind"] != "chemical":
        return _page("Link expired", "<h2>This resolution link is invalid or has expired.</h2>", kind="chemical")

    fields = pending["fields"]
    services = _get_services()
    chemical_svc = services.chemical_svc
    chemicals = sorted(chemical_svc.all_chemicals(), key=lambda c: c.name.lower())
    options = "".join(f'<option value="{c.id}">{html.escape(c.name)}</option>' for c in chemicals)

    def prep_options(values: tuple[str, ...]) -> str:
        return "".join(f'<option value="{html.escape(v)}">{html.escape(v)}</option>' for v in values)

    elements = sorted(services.element_svc.all_elements(), key=lambda e: e.name)
    element_options = "".join(
        f'<option value="{e.id}">{html.escape(e.name)} ({html.escape(e.symbol)})</option>' for e in elements
    )

    return _page("Resolve chemical", f"""
<h2>Chemical not found</h2>
<p>The form has chemical <b>{html.escape(fields['form_name'])}</b>,
   which doesn't match anything in the database.</p>

<form method="post" action="/resolve/chemical">
  <input type="hidden" name="token" value="{html.escape(token)}">

  <label>Your initials (required either way below)
    <input name="created_by">
  </label>

  <fieldset>
    <legend>Map to an existing chemical</legend>
    <label>Existing chemical
      <select name="existing_chemical_id">
        <option value="">-- select --</option>
        {options}
      </select>
    </label>
    <button type="submit" name="mode" value="existing">Use this chemical</button>
  </fieldset>

  <fieldset>
    <legend>Or add a new chemical</legend>
    <label>Name <input name="name" value="{html.escape(fields['form_name'])}"></label>
    <label>Metals prep
      <select name="metals_prep">{prep_options(chemical_svc.metals_prep_options)}</select>
    </label>
    <label>Silicon prep
      <select name="silicon_prep">{prep_options(chemical_svc.silicon_prep_options)}</select>
    </label>
    <label>Ions prep
      <select name="ions_prep">{prep_options(chemical_svc.ions_prep_options)}</select>
    </label>
    <label>KED elements (hold Ctrl/Cmd to select more than one -- leave
      none selected if this chemical doesn't need KED mode)
      <select name="ked_element_ids" multiple size="8">{element_options}</select>
    </label>
    <button type="submit" name="mode" value="new">Add new chemical</button>
  </fieldset>
</form>

<hr style="margin-top:2rem;border:none;border-top:1px solid #ddd;">
<form method="post" action="/resolve/cancel" style="margin-top:1rem;">
  <input type="hidden" name="token" value="{html.escape(token)}">
  <p style="font-size:0.85rem;color:#555;margin-bottom:0.5rem;">
    If there's a problem with the Testing Request form itself (an ambiguous or
    incorrect entry), don't guess at a resolution:
  </p>
  <button type="submit">Cancel</button>
</form>
""", kind="chemical", token=token)


@app.post("/resolve/chemical", response_class=HTMLResponse)
def resolve_chemical_submit(
    token: str = Form(...),
    mode: str = Form(...),
    existing_chemical_id: str = Form(""),
    name: str = Form(""),
    metals_prep: str = Form(""),
    silicon_prep: str = Form(""),
    ions_prep: str = Form(""),
    ked_element_ids: list[str] = Form([]),
    created_by: str = Form(""),
):
    pending = _pending_resolutions.get(token)
    if pending is None or pending["kind"] != "chemical":
        return _page("Link expired", "<h2>This resolution link is invalid or has expired.</h2>", kind="chemical")
    with _pending_lock:
        if pending["resolved"] or pending["cancelled"]:
            # See the matching guard in resolve_customer_submit -- same reasoning.
            return _page("Already resolved", "<h2>Already resolved</h2><p>This was already handled -- you can close this tab.</p>", kind="chemical")
        # Claim before doing the real work -- see the matching comment in
        # resolve_customer_submit for the full reasoning.
        pending["resolved"] = True

    chemical_svc = _get_services().chemical_svc
    fields = pending["fields"]
    existing_name_notice = ""
    try:
        if mode == "existing":
            if not existing_chemical_id:
                raise ValueError("Please select an existing chemical, or use the 'add new' section instead.")
            chemical_id = int(existing_chemical_id)
        else:
            # Self-heal instead of dead-ending -- see the matching comment
            # in resolve_customer_submit for the full reasoning. Exact
            # case-insensitive match only (get_by_name).
            existing = chemical_svc.get_by_name(name)
            if existing is not None:
                chemical_id = existing.id
                ked_note = ""
                if any(eid for eid in ked_element_ids):
                    # KED elements typed here were meant for a NEW chemical
                    # record -- there's still no supported way to add KED
                    # elements to an already-existing one (a known, separately
                    # tracked gap), so say so rather than silently dropping them.
                    ked_note = " KED elements entered here were NOT applied -- there's no way yet to add KED elements to an existing chemical; do that separately if needed."
                existing_name_notice = (
                    f'<p style="background:#fff3cd;border:1px solid #ffe69c;padding:0.75rem 1rem;'
                    f'border-radius:4px;">Note: <b>{html.escape(existing.name)}</b> already existed '
                    "in the system -- this form was linked to the existing record instead of "
                    f"creating a duplicate.{ked_note}</p>"
                )
            else:
                ked_ids = frozenset(int(eid) for eid in ked_element_ids if eid)
                chemical_id = chemical_svc.create_chemical(
                    name, metals_prep, silicon_prep, ions_prep, ked_element_ids=ked_ids, created_by=created_by
                ).id
        chemical_svc.add_form_name(fields["form_name"], chemical_id, created_by=created_by)
    except ValueError as e:
        # Release the claim -- see the matching comment in
        # resolve_customer_submit for the full reasoning.
        with _pending_lock:
            pending["resolved"] = False
        return _page("Could not resolve", f"""
<h2>Could not resolve chemical</h2>
<p>{html.escape(str(e))}</p>
<p><a href="/resolve/chemical?token={html.escape(token)}">Go back and try again</a></p>
""", kind="chemical")
    except Exception:
        # See the matching comment in resolve_customer_submit's own
        # except Exception clause -- same fix, same reasoning: a bare
        # `raise` here used to reach the technician as a bodyless 500.
        with _pending_lock:
            pending["resolved"] = False
        logger.exception("resolve_chemical_submit: unexpected error")
        return _page("Could not resolve", f"""
<h2>Something went wrong resolving this chemical</h2>
<p>An unexpected error occurred and this could not be completed. Try again, or contact support
   if it keeps happening.</p>
<p><a href="/resolve/chemical?token={html.escape(token)}">Go back and try again</a></p>
""", kind="chemical")

    return _page("Resolved", f"<h2>Done</h2>{existing_name_notice}<p>You can close this tab and return to Excel -- the report will continue automatically.</p>", kind="chemical")


@app.get("/resolve/folder", response_class=HTMLResponse)
def resolve_folder_page(token: str):
    pending = _pending_resolutions.get(token)
    if pending is None or pending["kind"] != "folder":
        return _page("Link expired", "<h2>This resolution link is invalid or has expired.</h2>")

    fields = pending["fields"]
    location = fields.get("location", "")
    # Cosmetic only -- purely for a nicer suggested default below, never
    # used for anything set_folder() validates. Location CODES ("SX5N"/
    # "SX5S", matching sx5_location_from_filename) don't match the real
    # folder names ("SX5_North"/"SX5_South") 1:1, so translate for display.
    _LOCATION_LABELS = {"SX5N": "North", "SX5S": "South"}
    location_label = _LOCATION_LABELS.get(location, location)
    customer_display = f"{fields['customer_name']} ({location_label})" if location else fields["customer_name"]
    folder_name_guess = f"{fields['customer_name']}_{location_label}" if location else fields["customer_name"]

    # A real *value*, not a placeholder: a placeholder is never submitted
    # if left untouched (confirmed the hard way -- it just reads as
    # greyed-out hint text and vanishes on focus, so a technician who
    # takes it as "the default" submits an empty field instead). This is
    # only a guess -- the folder trees don't share a naming convention
    # (see ReportFolderService's docstring) -- but it's editable and
    # still validated by set_folder() below either way, so a wrong guess
    # just re-prompts rather than silently accepting anything. In test
    # mode, guess a subfolder under the sandbox root instead of a real
    # Reports path -- set_folder() would reject the real path anyway,
    # so guessing it here would just guarantee a wasted round trip.
    test_root = _get_services().report_folder_svc.test_root
    if test_root is not None:
        guessed_path = str(test_root / folder_name_guess)
    else:
        guessed_path = f"S:\\Reports\\{folder_name_guess}"
    return _page("Resolve report folder", f"""
<h2>Report folder not set</h2>
<p>There's no known Reports folder yet for customer
   <b>{html.escape(customer_display)}</b> (or the one on file no longer exists) --
   this is only asked once per customer{' and location' if location else ''}; it's remembered for every future report.</p>

<form method="post" action="/resolve/folder">
  <input type="hidden" name="token" value="{html.escape(token)}">

  <fieldset>
    <legend>Set the report folder</legend>
    <label>Full folder path (edit this if it's not right -- copy the real one from File Explorer's address bar)
      <input name="folder_path" value="{html.escape(guessed_path)}">
    </label>
    <label>Your initials <input name="created_by"></label>
    <button type="submit">Save folder</button>
  </fieldset>
</form>

<hr style="margin-top:2rem;border:none;border-top:1px solid #ddd;">
<form method="post" action="/resolve/cancel" style="margin-top:1rem;">
  <input type="hidden" name="token" value="{html.escape(token)}">
  <p style="font-size:0.85rem;color:#555;margin-bottom:0.5rem;">
    If there's a problem with the Testing Request form itself (an ambiguous or
    incorrect entry), don't guess at a resolution:
  </p>
  <button type="submit">Cancel</button>
</form>
""", token=token)


@app.post("/resolve/folder", response_class=HTMLResponse)
def resolve_folder_submit(token: str = Form(...), folder_path: str = Form(""), created_by: str = Form("")):
    pending = _pending_resolutions.get(token)
    if pending is None or pending["kind"] != "folder":
        return _page("Link expired", "<h2>This resolution link is invalid or has expired.</h2>")
    with _pending_lock:
        if pending["resolved"] or pending["cancelled"]:
            # See the matching guard in resolve_customer_submit -- same reasoning.
            return _page("Already resolved", "<h2>Already resolved</h2><p>This was already handled -- you can close this tab.</p>")
        # Claim before doing the real work -- see the matching comment in
        # resolve_customer_submit. This guards the SAME-token race (two
        # near-simultaneous submits of this exact page). A DIFFERENT,
        # cross-token race -- a genuinely stale tab from an earlier,
        # already-superseded resolution attempt for the same customer --
        # is now guarded independently at the domain layer instead:
        # ReportFolderService.set_folder() no-ops if a currently-valid
        # mapping already exists, the same idempotent pattern customer/
        # chemical resolution already had. Both guards are needed; neither
        # alone covers both races.
        pending["resolved"] = True

    fields = pending["fields"]
    try:
        # A technician might type S:\... here even when test_root is
        # stored as a UNC path (see resolve_mapped_drive() at import time
        # above) -- translate before the containment check in set_folder()
        # so a real, correct S:\ answer isn't rejected as "not under
        # test_root" just because it's spelled differently than the
        # UNC form test_root itself uses.
        translated_folder_path = str(resolve_mapped_drive(folder_path)) if folder_path else folder_path
        _get_services().report_folder_svc.set_folder(
            fields["customer_id"], translated_folder_path, created_by=created_by, location=fields.get("location", "")
        )
    except ValueError as e:
        with _pending_lock:
            pending["resolved"] = False
        return _page("Could not resolve", f"""
<h2>Could not set report folder</h2>
<p>{html.escape(str(e))}</p>
<p><a href="/resolve/folder?token={html.escape(token)}">Go back and try again</a></p>
""")
    except Exception:
        # See the matching comment in resolve_customer_submit's own
        # except Exception clause -- same fix, same reasoning: a bare
        # `raise` here used to reach the technician as a bodyless 500.
        with _pending_lock:
            pending["resolved"] = False
        logger.exception("resolve_folder_submit: unexpected error")
        return _page("Could not resolve", f"""
<h2>Something went wrong setting this report folder</h2>
<p>An unexpected error occurred and this could not be completed. Try again, or contact support
   if it keeps happening.</p>
<p><a href="/resolve/folder?token={html.escape(token)}">Go back and try again</a></p>
""")

    return _page("Resolved", "<h2>Done</h2><p>You can close this tab and return to Excel -- the report will continue automatically.</p>")


@app.post("/resolve/cancel", response_class=HTMLResponse)
def resolve_cancel(token: str = Form(...)):
    """Explicit "cannot resolve" path (2026-09-22) -- lets a technician
    who immediately recognizes a bad Testing Request form (e.g. an
    ambiguous or wrong chemical/customer entry) say so right away,
    instead of either guessing at a resolution or leaving VBA to wait out
    the full 10-minute timeout for the same eventual "not resolved"
    outcome. Kind-agnostic on purpose -- cancelling needs no
    customer/chemical/folder-specific logic, just marking the token done
    without ever calling any of the create_*/add_form_*/set_folder
    write paths."""
    pending = _pending_resolutions.get(token)
    if pending is None:
        return _page("Link expired", "<h2>This resolution link is invalid or has expired.</h2>")
    with _pending_lock:
        if pending["resolved"] or pending["cancelled"]:
            return _page("Already resolved", "<h2>Already resolved</h2><p>This was already handled -- you can close this tab.</p>")
        # Same lock, same check-and-claim pattern as the submit handlers
        # (2026-09-24) -- without this, a cancel racing a submit for the
        # SAME token could both pass their unlocked checks and both
        # proceed: the submit genuinely creates/maps something, then
        # cancel marks it "cancelled" afterward with no real effect but a
        # misleading page for whoever clicked cancel.
        pending["cancelled"] = True
    return _page(
        "Cancelled",
        "<h2>Cancelled</h2><p>Report generation was cancelled -- correct the Testing Request "
        "form and try again from Excel when it's ready. You can close this tab.</p>",
    )


@app.post("/resolution-heartbeat/{token}")
def resolution_heartbeat(token: str) -> dict:
    """Called every ~15s by the resolve page's own JS (see
    _heartbeat_script) while it's open. Deliberately no error for an
    unknown/already-resolved token -- a heartbeat arriving just after the
    page's own real submission (or one lingering after a restart wiped
    _pending_resolutions) is expected, not a problem worth surfacing to
    the browser's console."""
    pending = _pending_resolutions.get(token)
    if pending is not None:
        with _pending_lock:
            pending["last_seen"] = time.time()
    return {"ok": True}


@app.get("/resolution-status/{token}")
def resolution_status(token: str) -> dict:
    pending = _pending_resolutions.get(token)
    if pending is None:
        return {"resolved": False, "cancelled": False, "expired": True, "abandoned": False}
    abandoned = (
        not pending["resolved"]
        and not pending["cancelled"]
        and (time.time() - pending["last_seen"]) > _ABANDONED_AFTER_SECONDS
    )
    return {
        "resolved": pending["resolved"],
        "cancelled": pending["cancelled"],
        "expired": False,
        "abandoned": abandoned,
    }


if __name__ == "__main__":
    import uvicorn

    # host="0.0.0.0": this must be reachable from other lab machines, not
    # just LAB-SERVER itself -- binding to localhost would silently break
    # every technician machine except this one. Reachability from an
    # actual second machine still needs to be tested for real (same
    # never-verified gap the README already flags for Postgres).
    uvicorn.run(app, host="0.0.0.0", port=5000)
