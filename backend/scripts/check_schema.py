"""C-1 靜態掃描（make check-schema）。

找出同時引用 idn 與 anon 的 SQL，以及同時 import 兩區模型的模組。

規則：
1. cross_schema_sql：.py 中的字串常值（含隱式／+ 串接、f-string）或 .sql 的單一敘述，
   像 SQL 且同時引用 idn.* 與 anon.*（或以 search_path 同時掛上兩區）。docstring 不算。
2. cross_zone_import：同一模組 import app.db.models_idn 與 app.db.models_anon。
   只有 deidentify（單向通道）、all_models 與 migrations 例外。

偵測邏輯與執行期 guard 共用 app/db/guard.py。
用法：python -m scripts.check_schema [路徑 ...]（預設掃 app/ 與 scripts/）
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from app.db.guard import is_cross_schema

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = (BACKEND / "app", BACKEND / "scripts")
SKIP_DIRS = {"__pycache__", ".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
DUAL_IMPORT_ALLOWED = ("app/deidentify/", "app/db/all_models.py", "app/db/migrations/")
ZONE_MODULES = {"app.db.models_idn": "idn", "app.db.models_anon": "anon"}

_SQL_WORD = re.compile(r"\b(select|insert|update|delete|join|from|with|set|create|alter)\b", re.I)


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    kind: str
    snippet: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.kind}] {self.snippet}"


def _looks_like_cross_sql(value: str) -> bool:
    return bool(_SQL_WORD.search(value)) and is_cross_schema(value)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """裸字串陳述（docstring 與註解式字串）不是查詢，排除。"""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _string_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else " ? "
            for part in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _string_of(node.left), _string_of(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _python_strings(tree: ast.AST) -> Iterator[tuple[int, str]]:
    skip = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp):
            value = _string_of(node)
            if value is not None:
                yield node.lineno, value


def _imported_zones(tree: ast.AST) -> dict[str, int]:
    zones: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
        else:
            continue
        for name in names:
            zone = ZONE_MODULES.get(name)
            if zone and zone not in zones:
                zones[zone] = node.lineno
    return zones


def _dual_import_allowed(path: Path) -> bool:
    posix = path.resolve().as_posix()
    return any(
        f"/{allowed}" in posix or posix.endswith(f"/{allowed}") for allowed in DUAL_IMPORT_ALLOWED
    )


def scan_python(path: Path) -> list[Violation]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        print(f"略過（語法錯誤）：{path}：{exc}", file=sys.stderr)
        return []
    # 以（行號, 類型）為鍵：同一行的巢狀串接只回報一次
    found: dict[tuple[int, str], Violation] = {}
    for line, value in _python_strings(tree):
        if _looks_like_cross_sql(value):
            snippet = " ".join(value.split())[:120]
            found.setdefault(
                (line, "cross_schema_sql"), Violation(path, line, "cross_schema_sql", snippet)
            )
    zones = _imported_zones(tree)
    if len(zones) == 2 and not _dual_import_allowed(path):
        line = max(zones.values())
        found[(line, "cross_zone_import")] = Violation(
            path, line, "cross_zone_import", "同一模組同時 import idn 與 anon 模型"
        )
    return [found[key] for key in sorted(found)]


def scan_sql(path: Path) -> list[Violation]:
    text = path.read_text(encoding="utf-8")
    violations: list[Violation] = []
    offset = 0
    for statement in text.split(";"):
        if _looks_like_cross_sql(statement):
            leading = len(statement) - len(statement.lstrip())
            line = text.count("\n", 0, offset + leading) + 1
            violations.append(
                Violation(path, line, "cross_schema_sql", " ".join(statement.split())[:120])
            )
        offset += len(statement) + 1
    return violations


def _files(paths: Iterable[Path]) -> Iterator[Path]:
    for root in paths:
        if root.is_file():
            yield root
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix in (".py", ".sql") and not SKIP_DIRS & set(path.parts):
                yield path


def scan_paths(paths: Iterable[Path]) -> list[Violation]:
    violations: list[Violation] = []
    for path in _files(paths):
        violations.extend(scan_python(path) if path.suffix == ".py" else scan_sql(path))
    return violations


def main(argv: list[str]) -> int:
    paths = [Path(p) for p in argv[1:]] or list(DEFAULT_PATHS)
    violations = scan_paths(paths)
    for violation in violations:
        print(violation)
    if violations:
        print(f"\n✗ 發現 {len(violations)} 處跨 idn／anon 的違規（C-1）")
        return 1
    print("✓ 沒有跨 idn／anon 的 SQL 或 import（C-1）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
