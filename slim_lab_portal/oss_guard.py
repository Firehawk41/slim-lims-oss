"""OSS-ONLY GUARD — this portal attaches to the open-source SLIM demo ecosystem only.

slim-lab-portal is a portfolio module. It must never connect to a real lab's databases
(e.g. anything PreciLab production). Every database connection goes through
`db.make_engine`, which calls `assert_oss_database` first:

  * URLs mentioning a forbidden marker (e.g. "precilab") are refused. No override exists.
  * Only SQLite files and a short list of demo hosts are allowed by default:
    localhost / 127.0.0.1 / ::1 and the docker-compose service names `db` / `postgres`.
  * Another demo host (e.g. a hosted demo database for the portfolio) must be listed
    explicitly in PORTAL_ALLOWED_DB_HOSTS (comma-separated). It still may not match a
    forbidden marker.

If you are reading this because a connection was refused: point DATABASE_URL /
SLIM_DATABASE_URL at the OSS demo database (scripts/seed_demo.py), not at a lab system.
"""

import os

from sqlalchemy.engine import make_url

FORBIDDEN_MARKERS = ("precilab",)
DEFAULT_ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "db", "postgres"})
ALLOWED_HOSTS_ENV = "PORTAL_ALLOWED_DB_HOSTS"


class NotAnOssDatabase(RuntimeError):
    pass


def allowed_hosts() -> frozenset[str]:
    extra = os.environ.get(ALLOWED_HOSTS_ENV, "")
    return DEFAULT_ALLOWED_HOSTS | {h.strip().lower() for h in extra.split(",") if h.strip()}


def assert_oss_database(url: str) -> None:
    """Raises NotAnOssDatabase unless `url` is an allowed OSS/demo database."""
    lowered = str(url).lower()
    for marker in FORBIDDEN_MARKERS:
        if marker in lowered:
            raise NotAnOssDatabase(
                f"Refusing a database URL containing {marker!r}: slim-lab-portal attaches to the OSS "
                "demo ecosystem only. See slim_lab_portal/oss_guard.py."
            )
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        return
    host = (parsed.host or "").lower()
    if host not in allowed_hosts():
        raise NotAnOssDatabase(
            f"Refusing database host {host!r}: only OSS demo hosts are allowed "
            f"({', '.join(sorted(allowed_hosts()))}). Add a demo host to {ALLOWED_HOSTS_ENV} if it "
            "really is a demo database. See slim_lab_portal/oss_guard.py."
        )
