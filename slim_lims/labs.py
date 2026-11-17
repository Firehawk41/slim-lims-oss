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
    Column("seeded_by", String(64)),  # the code that seeded it: a spare outlives a restart of the same code
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
        upgrade: Callable[[str], object] | None = None,
        spares: int = 2,
        cap: int = 25,
        idle: timedelta = timedelta(hours=1),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        background: bool = True,
        version: str | None = None,
    ):
        """`version` names the code (Render: the deployed commit). Spares it seeded are kept through a
        restart of the same version (the free plan sleeps when idle); without one, a restart drops them."""
        self.url = without_schema(url)
        self.version = version
        self.seed = seed
        self.upgrade = upgrade  # brings a claimed lab up to the code now running (D-115)
        self.spares, self.cap, self.idle, self.clock = spares, cap, idle, clock
        self.background = background  # False: spares are made only by calling refill (tests)
        self.base = make_engine(self.url, poolclass=NullPool)
        self._factories: dict[str, sessionmaker[Session]] = {}
        self._engines: dict[str, Engine] = {}
        self._references: dict = {}  # schema -> its ReferenceCache (D-141)
        self._seen: dict[str, datetime] = {}
        self._lock = threading.Lock()
        self._filling = threading.Lock()
        # Alembic keeps its context in module globals: two seeds or upgrades at once in one process (a
        # visitor's claim and the spare filler, right after a deploy) corrupt each other. One at a time.
        self._seeding = threading.RLock()
        self._claims_waiting = 0  # the filler gives way to these (the RLock isn't fair: 3½ min on Render)

    @property
    def kept_for(self) -> str:
        """How long a lab is kept after its visitor's last click, in words (the pages say so, review T-5)."""
        minutes = int(self.idle.total_seconds() // 60)
        if minutes % 60 == 0:
            hours = minutes // 60
            return "an hour" if hours == 1 else f"{hours} hours"
        return f"{minutes} minutes"

    # ------------------------------------------------------------ setup

    def check(self) -> None:
        """Refuses labs mode while public holds app tables: every lab would see them."""
        with self.base.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext SCHEMA public"))
        registry.create_all(self.base)
        with self.base.begin() as conn:  # a table made before the column (2026-11-17)
            conn.execute(text("ALTER TABLE public.demo_labs ADD COLUMN IF NOT EXISTS seeded_by varchar(64)"))
        left = sorted(set(inspect(self.base).get_table_names(schema="public")) - {demo_labs.name})
        if left:
            raise RuntimeError(
                f"DEMO_LABS needs a public schema without app tables; found {', '.join(left[:5])}"
                f"{' …' if len(left) > 5 else ''}. Seed into DATABASE_SCHEMA (e.g. lab_shared) and "
                "empty public first: python -m slim_lims.cli empty-public"
            )

    def start(self) -> None:
        """After a restart, which may be a deploy with new migrations (D-115; app review 2026-10-30b L1):
        drops labs left half-seeded and every spare seeded by other code (refilled from the current seed),
        brings each claimed lab up to date with `upgrade` (dropping one that won't: its visitor starts
        afresh), then fills the spares in the background."""
        with self.base.begin() as conn:
            rows = conn.execute(select(demo_labs.c.schema, demo_labs.c.state, demo_labs.c.seeded_by)).all()
        for schema, state, seeded_by in rows:
            if state == READY and self.version and seeded_by == self.version:
                continue  # the same code woke up: its spares are as good as new ones
            if state in (PREPARING, READY):
                self.drop(schema)
            elif self.upgrade is not None:
                try:
                    with self._seeding:
                        self.upgrade(with_schema(self.url, schema))
                except Exception:
                    log.exception("demo labs: %s didn't upgrade; dropped", schema)
                    self.drop(schema)
        self.refill_in_background()

    # ------------------------------------------------------------ a visitor's lab

    def claim(self) -> str:
        """A lab of the visitor's own: a spare if one is ready, else one seeded now. A claim that finds
        none waits for a spare being seeded and takes it, rather than seeding beside it; the filler starts no
        other spare while a claim waits."""
        schema = self._take_ready()
        if schema is None:
            with self._lock:
                self._claims_waiting += 1
            try:
                with self._seeding:
                    schema = self._take_ready()
                    if schema is None:
                        self.sweep(room_for=1)
                        schema = self._make(CLAIMED)
            finally:
                with self._lock:
                    self._claims_waiting -= 1
        self.refill_in_background()
        return schema

    def take_spare(self) -> str | None:
        """A ready spare for the visitor, or None at once (they use the shared lab meanwhile): a visitor never
        waits for a lab to be seeded (Jamie, 2026-11-17: "not something that keeps a recruiter waiting")."""
        schema = self._take_ready()
        self.refill_in_background()
        return schema

    def _take_ready(self) -> str | None:
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

    def reference_for(self, schema: str, ttl_seconds: float = 300):
        """A lab's own reference cache (its SLIM tables are its own, D-141): a visitor's edits show in
        their lab and nowhere else. None for a lab not entered in this process."""
        from slim_lims.reference_cache import ReferenceCache

        with self._lock:
            engine = self._engines.get(schema)
            if engine is None:
                return None
            if schema not in self._references:
                self._references[schema] = ReferenceCache(engine, engine, ttl_seconds=ttl_seconds)
            return self._references[schema]

    def engine_for(self, schema: str) -> Engine | None:
        with self._lock:
            return self._engines.get(schema)

    def drop(self, schema: str) -> None:
        if not LAB_NAME.match(schema):
            raise ValueError(f"not a lab schema name: {schema!r}")
        with self._lock:
            self._references.pop(schema, None)
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
                with self._lock:
                    if self._claims_waiting:  # a visitor first; their claim refills afterwards
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
                demo_labs.insert().values(
                    schema=schema, state=PREPARING, created_at=now, last_seen=now, seeded_by=self.version
                )
            )
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        with self._seeding:  # held until it is marked ready: a waiting claim then finds it
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
