"""App review 2026-10-30b R1 (slim-data D-117): every SLIM module the app installs is pinned to a commit,
and pyproject.toml, the CI workflow and render.yaml pin each to the same one."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FILES = ("pyproject.toml", ".github/workflows/ci.yml", "render.yaml")
URL = re.compile(r"github\.com/Firehawk41/([\w-]+)@([^\s\"#]+)")
CHECKOUT = re.compile(r"github\.com/Firehawk41/([\w-]+)\s+\S+\s*\n?\s*&& git -C \S+ checkout (\S+)")
SHA = re.compile(r"[0-9a-f]{40}")


def _pins() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for name in FILES:
        text = (REPO / name).read_text()
        for repo, ref in URL.findall(text) + CHECKOUT.findall(text):
            found.setdefault(repo, set()).add(ref)
    return found


def test_every_module_is_pinned_to_one_commit_everywhere():
    pins = _pins()
    assert {"slim-data-oss", "slim-prep-oss", "slim-quality-oss", "slim-domain-oss"} <= pins.keys()
    assert "slim-report-engine-oss" in pins and "SLIM-Invoicing-Program" in pins
    for repo, refs in pins.items():
        assert len(refs) == 1, f"{repo} is pinned to {refs}"
        assert SHA.fullmatch(next(iter(refs))), f"{repo} follows {refs}, not a commit"


def test_no_module_is_cloned_at_its_moving_head():
    for name in FILES:
        for line in (REPO / name).read_text().splitlines():
            assert "--depth 1" not in line or "Firehawk41" not in line, f"{name}: {line.strip()}"
