"""The mirror of the lab's private slim-lims is retired (slim-data D-82): this repo is the OSS host app.

A tripwire in case a sync from the private repo ever runs again: its files must not come back.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIRRORED = ("app.py", "vba_ping.bas", ".env.template", "deploy/setup_service.ps1", "deploy/watchdog_check.ps1")


def test_nothing_from_the_private_slim_lims_comes_back():
    back = [path for path in MIRRORED if (ROOT / path).exists()]
    assert not back, f"files from the private slim-lims mirror are back (D-82): {back}"
