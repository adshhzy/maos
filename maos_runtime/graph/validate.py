"""Command-line validator for MAOS task graph JSON files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from maos_runtime.graph.schema import collect_task_graph_issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate MAOS task graph JSON files.")
    parser.add_argument("paths", nargs="+", help="JSON file or directory paths to validate")
    args = parser.parse_args()

    files = _expand_files([Path(path) for path in args.paths])
    if not files:
        print("No JSON files found.")
        return 1

    failed = False
    for file_path in files:
        issues = _validate_file(file_path)
        if issues:
            failed = True
            print(f"FAIL {file_path}")
            for issue in issues:
                print(f"  - {issue.format()}")
        else:
            print(f"OK   {file_path}")
    return 1 if failed else 0


def _expand_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.glob("*.json")))
        else:
            files.append(path)
    return files


def _validate_file(file_path: Path):
    try:
        graph = json.loads(file_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        from maos_runtime.graph.schema import GraphValidationIssue

        return [GraphValidationIssue("$", f"Could not parse JSON: {exc}")]
    if isinstance(graph, dict) and isinstance(graph.get("graphs"), list):
        issues = []
        for index, item in enumerate(graph["graphs"]):
            for issue in collect_task_graph_issues(item):
                issues.append(type(issue)(f"$.graphs[{index}]{issue.path.removeprefix('$')}", issue.message))
        return issues
    return collect_task_graph_issues(graph)


if __name__ == "__main__":
    raise SystemExit(main())
