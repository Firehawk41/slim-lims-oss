"""The landing page's facts: the tour's steps and the code each highlight points to. The words are
Jamie's, in templates/about/_copy.html; tests/test_about.py checks this repo's links resolve."""

from dataclasses import dataclass

GITHUB = "https://github.com/Firehawk41"


@dataclass(frozen=True)
class Link:
    repo: str
    path: str = ""
    label: str = ""

    @property
    def url(self) -> str:
        return f"{GITHUB}/{self.repo}/blob/main/{self.path}" if self.path else f"{GITHUB}/{self.repo}"

    @property
    def text(self) -> str:
        return self.label or self.path.rsplit("/", 1)[-1] or self.repo


@dataclass(frozen=True)
class Step:
    persona: str
    screen: str


@dataclass(frozen=True)
class Highlight:
    title: str
    links: tuple[Link, ...]


TOUR = (
    Step("Casey (customer)", "New request"),
    Step("Ana (analyst)", "Requests → check in"),
    Step("Ana (analyst)", "Prep"),
    Step("Ana (analyst)", "Results → import"),
    Step("Rob (reviewer)", "Results → review, release"),
    Step("Sue (supervisor)", "Results → report; Invoices"),
)

HIGHLIGHTS = (
    Highlight(
        "A rules engine that shows its work",
        (Link("slim-data-oss", "slim_data/engine/runner.py"), Link("slim-data-oss", "docs/DESIGN.md")),
    ),
    Highlight(
        "Append-only records, time travel",
        (
            Link("slim-data-oss", "slim_data/persistence/append_only.py"),
            Link("slim-lims-oss", "slim_lims/repositories.py"),
        ),
    ),
    Highlight("Replay and verify", (Link("slim-data-oss", "slim_data/service/verify.py"),)),
    Highlight("Four-eyes review", (Link("slim-data-oss", "slim_data/domain/workflow.py"),)),
    Highlight("A lab per visitor", (Link("slim-lims-oss", "slim_lims/labs.py"),)),
    Highlight(
        "Guard rails",
        (Link("slim-lims-oss", "slim_lims/oss_guard.py"), Link("slim-lims-oss", "slim_lims/security.py")),
    ),
)

REPOS = (
    Link("slim-lims-oss", label="slim-lims-oss: the host"),
    Link("slim-data-oss", label="slim-data-oss: results"),
    Link("slim-prep-oss", label="slim-prep-oss: the prep book"),
    Link("slim-domain-oss", label="slim-domain-oss: reference data, audit log"),
    Link("slim-report-engine-oss", label="slim-report-engine-oss: reports"),
    Link("slim-data-oss", "docs/DECISIONS.md", "Decisions"),
    Link("slim-lims-oss", "docs/DEV_DIARY.md", "Dev diary"),
)
