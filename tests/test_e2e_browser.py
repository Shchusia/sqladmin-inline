"""Headless end-to-end test of the inline UI (JavaScript included).

Starts a real uvicorn server and drives the edit page with jsdom: add, edit,
validation errors, search, pagination, load more, drag-and-drop, bulk delete,
FK selects and collapsing.  Every request to a non-local host is blocked and
reported, so this also proves the admin works without internet access.

Requires node and ``npm install`` in tests/e2e; skipped otherwise.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import socket
import subprocess
import sys
import time

import pytest

E2E = pathlib.Path(__file__).parent / "e2e"
ROOT = E2E.parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not (E2E / "node_modules" / "jsdom").is_dir(),
    reason="node + jsdom not installed (cd tests/e2e && npm install)",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def server(tmp_path: pathlib.Path):  # type: ignore[no-untyped-def]
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.e2e.server", str(tmp_path / "e2e.db"), str(port)],
        cwd=ROOT,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    else:  # pragma: no cover
        proc.kill()
        pytest.fail("server did not start")
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    proc.wait(timeout=10)


def test_inline_ui_offline(server: str) -> None:
    result = subprocess.run(
        ["node", str(E2E / "run.mjs"), server],
        capture_output=True,
        text=True,
        timeout=120,
    )
    report = json.loads(result.stdout)
    failed = [name for status, name in report["steps"] if status != "ok"]
    assert failed == [], failed
    assert report["external"] == [], "external requests attempted"
    assert report["errors"] == [], report["errors"]
    assert len(report["steps"]) >= 12
