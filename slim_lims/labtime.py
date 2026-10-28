"""The lab's clock (review A8): "today" is the lab's date, and times the modules turn into dates (a
standard's expiry against the prep day) are given in the lab's zone, so an evening's work isn't dated
tomorrow by the server's UTC. Instants are stored as they are; only the date they fall on is the lab's."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from fastapi import Request


@dataclass(frozen=True)
class LabClock:
    zone: ZoneInfo
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    def now(self) -> datetime:
        return self.clock().astimezone(self.zone)

    def today(self) -> date:
        return self.now().date()

    def local(self, at: datetime) -> datetime:
        """The same instant, in the lab's zone (a naive time is taken as the lab's)."""
        return at.replace(tzinfo=self.zone) if at.tzinfo is None else at.astimezone(self.zone)


def lab_clock(request: Request) -> LabClock:
    return request.app.state.lab_clock
