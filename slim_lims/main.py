"""FastAPI application factory. Run with: uvicorn --factory slim_lims.main:create_app"""

from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from slim_lims import __version__
from slim_lims.api import reference_router, register_error_handlers
from slim_lims.api import router as submissions_router
from slim_lims.auth import LoginRequired, StaffRequired, login_redirect
from slim_lims.auth import router as auth_router
from slim_lims.config import Settings, load_settings
from slim_lims.db import make_engine, make_session_factory
from slim_lims.demo_seed import DEFAULT_OSS_DIR, seed_demo, upgrade_lab
from slim_lims.labs import Labs
from slim_lims.labtime import LabClock
from slim_lims.prep_routes import api_router as prep_api_router
from slim_lims.prep_routes import router as prep_router
from slim_lims.quality_routes import router as quality_router
from slim_lims.reference import ReferenceData
from slim_lims.reference_cache import ReferenceCache
from slim_lims.reports import engine_renderer
from slim_lims.results import SpecReader
from slim_lims.results_routes import api_router as results_api_router
from slim_lims.results_routes import router as results_router
from slim_lims.routes import router as pages_router
from slim_lims.samples_routes import api_router as samples_api_router
from slim_lims.samples_routes import router as samples_router
from slim_lims.security import BodySizeLimitMiddleware, LoginThrottle, SecurityHeadersMiddleware
from slim_lims.web import STATIC_DIR, render

SESSION_COOKIE = "slim_portal_session"


def _labs(settings: Settings) -> Labs:
    if not (settings.database_url.startswith("postgresql") and settings.demo_personas):
        raise RuntimeError("DEMO_LABS needs Postgres and DEMO_PERSONAS")
    oss_dir = Path(settings.oss_report_engine_dir) if settings.oss_report_engine_dir else DEFAULT_OSS_DIR
    labs = Labs(
        settings.database_url,
        seed=lambda url: seed_demo(url, oss_dir=oss_dir, lab_timezone=settings.lab_timezone),
        upgrade=upgrade_lab,
    )
    labs.check()
    labs.start()
    return labs


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", "")


def create_app(
    settings: Settings | None = None,
    engine=None,
    reference: ReferenceData | None = None,
    specs=None,
    report_renderer=None,
    labs=None,
) -> FastAPI:
    settings = settings or load_settings()
    engine = engine or make_engine(settings.database_url)

    app = FastAPI(
        title="slim-lims",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    slim_engine = engine
    if settings.slim_database_url and settings.slim_database_url != settings.database_url:
        slim_engine = make_engine(settings.slim_database_url)
    if reference is None:
        reference = ReferenceCache(slim_engine, engine, ttl_seconds=settings.reference_cache_ttl_seconds)
    app.state.reference = reference
    # SLIM spec limits for slim-data: (customer_id, chemical_id, location) -> {element: limit}.
    app.state.specs = specs or SpecReader(slim_engine)
    app.state.report_renderer = report_renderer or engine_renderer  # spec §13, slim-data D-79
    app.state.login_throttle = LoginThrottle()
    app.state.lab_clock = LabClock(ZoneInfo(settings.lab_timezone))
    if labs is None and settings.demo_labs:
        labs = _labs(settings)
    app.state.labs = labs  # a lab per visitor (slim-data D-102), or None

    # Last added = outermost. Headers wrap everything, including 413s and session errors.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=SESSION_COOKIE,
        max_age=settings.session_max_age_seconds,
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(auth_router)
    app.include_router(pages_router)
    app.include_router(submissions_router)
    app.include_router(reference_router)
    app.include_router(results_router)
    app.include_router(results_api_router)
    app.include_router(prep_router)
    app.include_router(prep_api_router)
    app.include_router(samples_router)
    app.include_router(samples_api_router)
    app.include_router(quality_router)
    register_error_handlers(app)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, _exc: LoginRequired):
        if _wants_json(request):
            return JSONResponse({"detail": "Not authenticated."}, status_code=401)
        return login_redirect(request)

    @app.exception_handler(StaffRequired)
    async def _staff_required(request: Request, _exc: StaffRequired):
        if _wants_json(request):
            return JSONResponse({"detail": "Staff only."}, status_code=403)
        return render(
            request, "error.html", {"status": 403, "message": "This page is for lab staff only."}, 403
        )

    @app.exception_handler(HTTPException)
    async def _http_exception(request: Request, exc: HTTPException):
        if _wants_json(request) or exc.status_code < 400:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        return render(
            request, "error.html", {"status": exc.status_code, "message": exc.detail}, exc.status_code
        )

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok", "version": __version__}

    return app
