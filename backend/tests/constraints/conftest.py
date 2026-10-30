"""八條約束測試跑完後寫 reports/constraints.json（供 /console 驗收台顯示）。

只有八個檔案都有跑到才寫，避免單跑一個檔案時覆蓋成殘缺的報告。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, datetime

import pytest

from app.config import get_settings

_FILE = re.compile(r"test_c(\d)_\w+\.py")
_results: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0})


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    match = _FILE.search(report.nodeid)
    if match is None:
        return
    key = f"C-{match.group(1)}"
    if report.when == "call" or (report.when == "setup" and not report.passed):
        outcome = "skipped" if report.skipped else "passed" if report.passed else "failed"
        _results[key][outcome] += 1


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if len(_results) != 8:
        return
    target = get_settings().reports_dir / "constraints.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "constraints": {key: _results[key] for key in sorted(_results)},
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
