"""Jinja2 templates and small helpers shared by the HTML routes."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from slim_lab_portal.security import csrf_token

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"

templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
templates.env.globals["csrf_token"] = csrf_token

_FLASH_KEY = "_flashes"


def flash(request: Request, message: str, category: str = "info") -> None:
    request.session.setdefault(_FLASH_KEY, []).append([category, message])


def _pop_flashes(request: Request) -> list[list[str]]:
    return request.session.pop(_FLASH_KEY, [])


def render(request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200):
    ctx = {
        "current_user": getattr(request.state, "user", None),
        "flashes": _pop_flashes(request),
        **(context or {}),
    }
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
