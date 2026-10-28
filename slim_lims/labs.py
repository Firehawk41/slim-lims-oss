"""A lab per visitor (slim-data D-102; review A3). Postgres, demo only (`DEMO_LABS`).

Each visitor who picks a demo persona gets a lab of their own: a Postgres schema `lab_<16 hex>` seeded
with the whole demo (`demo_seed.seed_demo`), found through `search_path`, so every module's tables and
migrations work unchanged. Nobody sees another visitor's lab; an idle lab is dropped after an hour.
Seeding takes seconds of CPU (minutes on a small host), so a few spare labs are kept seeded in the
background and a visitor takes one at once. The registry (`public.demo_labs`) is in Postgres, so
spares outlive a restart.

In labs mode `public` holds only extensions (citext) and the registry: a lab's search path is
`lab_x, public`, and any app table left in public would be seen through it. `check` refuses to start
otherwise. Password logins and the CLI use the shared lab (`DATABASE_SCHEMA`, e.g. `lab_shared`).
"""

import logging
import re
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    MetaData,
    String,
    Table,
    delete,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from slim_lims.db import make_engine, make_session_factory

log = logging.getLogger(__name__)

LAB_NAME = re.compile(r"^lab_[a-z0-9_]{1,40}$")
PREPARING, READY, CLAIMED = "preparing", "ready", "claimed"

registry = MetaData(schema="public")
demo_labs = Table(
    "demo_labs",
    registry,
    Column("schema", String(50), primary_key=True),
    Column("state", String(10), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_seen", DateTime(timezone=True), nullable=False),
)


def with_schema(url: str, schema: str) -> str:
    """The URL whose connections find `schema` first (and public's extensions after it)."""
    if not LAB_NAME.match(schema):
        raise ValueError(f"not a lab schema name: {schema!r}")
    return (
        make_url(url)
        .update_query_dict({"options": f"-csearch_path={schema},public"})
        .render_as_string(hide_password=False)
    )


def schema_of(url: str) -> str | None:
    """The lab schema a URL's search_path names first, if any."""
    options = make_url(url).query.get("options", "")
    found = re.search(r"search_path=([a-z0-9_]+)", options if isinstance(options, str) else "")
    return found.group(1) if found else None


def without_schema(url: str) -> str:
    return make_url(url).difference_update_query(["options"]).render_as_string(hide_password=False)


def ensure_schema(url: str) -> None:
    """Creates the URL's lab schema, and citext in public (where every lab finds it)."""
    schema = schema_of(url)
    if schema is None or not url.startswith("postgresql"):
        return
    engine = make_engine(without_schema(url), poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext SCHEMA public"))
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    finally:
        engine.dispose()


class Labs:
    def __init__(
        self,
        url: str,
        seed: Callable[[str], object],
        spares: int = 2,
        cap: int = 25,
        idle: timedelta = timedelta(hours=1),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        background: bool = True,
    ):
        self.url = without_schema(url)
        self.seed = seed
        self.spares, self.cap, self.idle, self.clock = spares, cap, idle, clock
        self.background = background  # False: spares are made only by calling refill (tests)
        self.base = make_engine(self.url, poolclass=NullPool)
        self._factories: dict[str, sessionmaker[Session]] = {}
        self._engines: dict[str, Engine] = {}
        self._seen: dict[str, datetime] = {}
        self._lock = threading.Lock()
        self._filling = threading.Lock()

    # ------------------------------------------------------------ setup

    def check(self) -> None:
        """Refuses labs mode while public holds app tables: every lab would see them."""
        with self.base.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext SCHEMA public"))
        registry.create_all(self.base)
        left = sorted(set(inspect(self.base).get_table_names(schema="public")) - {demo_labs.name})
        if left:
            raise RuntimeError(
                f"DEMO_LABS needs a public schema without app tables; found {', '.join(left[:5])}"
                f"{' …' if len(left) > 5 else ''}. Seed into DATABASE_SCHEMA (e.g. lab_shared) and "
                "empty public first: python -m slim_lims.cli empty-public"
            )

    def start(self) -> None:
        """Drops labs left half-seeded by a restart, then fills the spares in the background."""
        with self.base.begin() as conn:
            stale = conn.scalars(select(demo_labs.c.schema).where(demo_labs.c.state == PREPARING)).all()
        for schema in stale:
            self.drop(schema)
        self.refill_in_background()

    # ------------------------------------------------------------ a visitor's lab

    def claim(self) -> str:
        """A lab of the visitor's own: a spare if one is ready, else one seeded now."""
        now = self.clock()
        with self.base.begin() as conn:
            schema = conn.scalars(
                select(demo_labs.c.schema)
                .where(demo_labs.c.state == READY)
                .order_by(demo_labs.c.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if schema is not None:
                conn.execute(
                    update(demo_labs).where(demo_labs.c.schema == schema).values(state=CLAIMED, last_seen=now)
                )
        if schema is None:
            self.sweep(room_for=1)
            schema = self._make(CLAIMED)
        self.refill_in_background()
        return schema

    def enter(self, schema: str) -> sessionmaker[Session] | None:
        """The session factory of a claimed lab, noting the visit; None when it is gone."""
        if not LAB_NAME.match(schema):
            return None
        now = self.clock()
        with self.base.begin() as conn:
            state = conn.scalar(select(demo_labs.c.state).where(demo_labs.c.schema == schema))
            if state != CLAIMED:
                return None
            if now - self._seen.get(schema, datetime.min.replace(tzinfo=UTC)) > timedelta(minutes=1):
                conn.execute(update(demo_labs).where(demo_labs.c.schema == schema).values(last_seen=now))
                self._seen[schema] = now
        return self._factory(schema)

    def drop(self, schema: str) -> None:
        if not LAB_NAME.match(schema):
            raise ValueError(f"not a lab schema name: {schema!r}")
        with self._lock:
            self._factories.pop(schema, None)
            self._seen.pop(schema, None)
            engine = self._engines.pop(schema, None)
        if engine is not None:
            engine.dispose()
        with self.base.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.execute(delete(demo_labs).where(demo_labs.c.schema == schema))

    # ------------------------------------------------------------ housekeeping

    def sweep(self, room_for: int = 0) -> list[str]:
        """Drops labs idle past `idle`; then, to stay within the cap, the least recently used."""
        cutoff = self.clock() - self.idle
        with self.base.begin() as conn:
            idle = conn.scalars(
                select(demo_labs.c.schema).where(demo_labs.c.state == CLAIMED, demo_labs.c.last_seen < cutoff)
            ).all()
            claimed = conn.scalars(
                select(demo_labs.c.schema)
                .where(demo_labs.c.state == CLAIMED, demo_labs.c.last_seen >= cutoff)
                .order_by(demo_labs.c.last_seen)
            ).all()
            total = conn.scalar(select(text("count(*)")).select_from(demo_labs))
        over = max(0, total - len(idle) + room_for - self.cap)
        dropped = [*idle, *claimed[:over]]
        for schema in dropped:
            self.drop(schema)
        return dropped

    def refill(self) -> int:
        """Seeds spares until `spares` are ready (within the cap); one filler at a time."""
        if not self._filling.acquire(blocking=False):
            return 0
        made = 0
        try:
            self.sweep()
            while True:
                with self.base.begin() as conn:
                    rows = conn.execute(select(demo_labs.c.state)).scalars().all()
                waiting = sum(1 for s in rows if s in (READY, PREPARING))
                if waiting >= self.spares or len(rows) >= self.cap:
                    return made
                self._make(READY)
                made += 1
        finally:
            self._filling.release()

    def refill_in_background(self) -> None:
        if not self.background:
            return

        def run():
            try:
                self.refill()
            except Exception:  # a failed spare is dropped by _make; the next claim tries again
                log.exception("demo labs: refilling spares failed")

        threading.Thread(target=run, name="demo-labs", daemon=True).start()

    # ------------------------------------------------------------ inside

    def _make(self, state: str) -> str:
        schema = f"lab_{secrets.token_hex(8)}"
        now = self.clock()
        with self.base.begin() as conn:
            conn.execute(
                demo_labs.insert().values(schema=schema, state=PREPARING, created_at=now, last_seen=now)
            )
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        try:
            self.seed(with_schema(self.url, schema))
        except BaseException:
            self.drop(schema)
            raise
        with self.base.begin() as conn:
            conn.execute(
                update(demo_labs)
                .where(demo_labs.c.schema == schema)
                .values(state=state, last_seen=self.clock())
            )
        return schema

    def _factory(self, schema: str) -> sessionmaker[Session]:
        with self._lock:
            if schema not in self._factories:
                engine = make_engine(with_schema(self.url, schema), poolclass=NullPool)
                self._engines[schema] = engine
                self._factories[schema] = make_session_factory(engine)
            return self._factories[schema]
