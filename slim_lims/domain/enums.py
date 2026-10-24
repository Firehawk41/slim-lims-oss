"""TR enums (docs/SLIM_DOMAIN_V2.md §3). Integer values are stored in the database — never renumber."""

from enum import IntEnum


class RequestType(IntEnum):
    CHEMICAL = 1
    WATER = 2
    WAFER = 3

    @property
    def label(self) -> str:
        return self.name.title()


class ProcessingTime(IntEnum):
    """Values, labels and working days are identical to slim-domain v1."""

    EXTENDED_TIME = 1
    NEXT_DAY = 2
    TIME_LIMITED = 3
    SAME_DAY_RUSH = 4
    CALL_IN_RUSH = 5
    TWO_DAYS = 6
    THREE_DAYS = 7
    FIVE_DAYS = 8
    NEXT_DAY_RUSH = 9

    @property
    def days(self) -> int:
        """Working days from receipt to service date."""
        return _PT_DAYS[self]

    @property
    def label(self) -> str:
        """Exact text stamped on reports."""
        return _PT_LABELS[self]

    @property
    def portal_label(self) -> str:
        """Text shown to customers in the portal."""
        if self is ProcessingTime.CALL_IN_RUSH:
            return "Call-in RUSH (outside business hours)"
        return self.label

    @property
    def is_rush(self) -> bool:
        return self in _RUSH


_PT_DAYS = {
    ProcessingTime.SAME_DAY_RUSH: 0,
    ProcessingTime.CALL_IN_RUSH: 0,
    ProcessingTime.NEXT_DAY: 1,
    ProcessingTime.NEXT_DAY_RUSH: 1,
    ProcessingTime.TIME_LIMITED: 1,
    ProcessingTime.TWO_DAYS: 2,
    ProcessingTime.EXTENDED_TIME: 3,
    ProcessingTime.THREE_DAYS: 3,
    ProcessingTime.FIVE_DAYS: 5,
}

_PT_LABELS = {
    ProcessingTime.EXTENDED_TIME: "Extended Time",
    ProcessingTime.NEXT_DAY: "Next Day",
    ProcessingTime.NEXT_DAY_RUSH: "Next Day RUSH",
    ProcessingTime.TIME_LIMITED: "Next Day Time Limited",
    ProcessingTime.SAME_DAY_RUSH: "Same Day RUSH",
    ProcessingTime.CALL_IN_RUSH: "Call-in RUSH",
    ProcessingTime.TWO_DAYS: "Two Days",
    ProcessingTime.THREE_DAYS: "Three Days",
    ProcessingTime.FIVE_DAYS: "Five Days",
}

_RUSH = frozenset({ProcessingTime.SAME_DAY_RUSH, ProcessingTime.CALL_IN_RUSH, ProcessingTime.NEXT_DAY_RUSH})

# Offered in the portal, slowest first. EXTENDED_TIME is accepted only from legacy xlsx forms.
ALLOWED_PROCESSING_TIMES: dict[RequestType, tuple[ProcessingTime, ...]] = {
    RequestType.CHEMICAL: (
        ProcessingTime.FIVE_DAYS, ProcessingTime.THREE_DAYS, ProcessingTime.TWO_DAYS,
        ProcessingTime.NEXT_DAY, ProcessingTime.TIME_LIMITED,
        ProcessingTime.SAME_DAY_RUSH, ProcessingTime.CALL_IN_RUSH,
    ),
    RequestType.WATER: (
        ProcessingTime.FIVE_DAYS, ProcessingTime.THREE_DAYS, ProcessingTime.TWO_DAYS,
        ProcessingTime.NEXT_DAY, ProcessingTime.TIME_LIMITED,
        ProcessingTime.SAME_DAY_RUSH, ProcessingTime.CALL_IN_RUSH,
    ),
    RequestType.WAFER: (
        ProcessingTime.FIVE_DAYS, ProcessingTime.THREE_DAYS, ProcessingTime.TWO_DAYS,
        ProcessingTime.NEXT_DAY_RUSH, ProcessingTime.SAME_DAY_RUSH, ProcessingTime.CALL_IN_RUSH,
    ),
}


class TRStatus(IntEnum):
    SUBMITTED = 1
    RECEIVED = 2
    IN_PROGRESS = 3
    PARTIAL_REPORT = 4
    COMPLETE_REPORT = 5
    INVOICED = 6
    CANCELLED = 7

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").capitalize()

    @property
    def is_terminal(self) -> bool:
        return self in (TRStatus.INVOICED, TRStatus.CANCELLED)


class PaymentMethod(IntEnum):
    PURCHASE_ORDER = 1
    CREDIT_CARD = 2  # the lab calls the customer; no card data is ever stored

    @property
    def label(self) -> str:
        return {PaymentMethod.PURCHASE_ORDER: "Purchase order", PaymentMethod.CREDIT_CARD: "Credit card"}[self]


class WaferSize(IntEnum):
    MM_200 = 1
    MM_300 = 2

    @property
    def label(self) -> str:
        return {WaferSize.MM_200: "200 mm", WaferSize.MM_300: "300 mm"}[self]


class ReportingUnit(IntEnum):
    ATOMS_PER_CM2 = 1
    E10_ATOMS_PER_CM2 = 2

    @property
    def label(self) -> str:
        return {ReportingUnit.ATOMS_PER_CM2: "atoms/cm²", ReportingUnit.E10_ATOMS_PER_CM2: "10¹⁰ atoms/cm²"}[self]


class WaterPackage(IntEnum):
    STANDARD = 1
    PREMIUM = 2

    @property
    def label(self) -> str:
        return {WaterPackage.STANDARD: "Standard package", WaterPackage.PREMIUM: "Premium package"}[self]


class SubmissionSource(IntEnum):
    PORTAL = 1
    XLSX = 2


class ActorType(IntEnum):
    STAFF = 1
    CUSTOMER_USER = 2
    SYSTEM = 3
