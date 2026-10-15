"""Optional live regression exercise on local Git fixtures, never hidden repositories.

Fixture source/configuration is deliberately stored as strings and is never executed.
The production CLI continues to accept only public GitHub repositories.
"""

import argparse
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from agent import load_local_env
from auditor.errors import AuditError
from auditor.loop import AuditAgent
from auditor.provider import GeminiProvider
from auditor.redaction import Redactor
from auditor.report import atomic_json
from auditor.repository import run_git
from tools import RepositoryTools

BASE_FILES = {
    "README.md": """# Arithmetic utility
Reusable Python library. Install with pip install .; run python -m pytest and ruff check .
Build with python -m build. Propose changes through a pull request, run checks, and request
review. Configuration should be portable. CI validates every push and pull request.
""",
    ".gitignore": (
        ".venv/\n__pycache__/\n.pytest_cache/\n.ruff_cache/\nbuild/\ndist/\n*.egg-info/\n.env\n"
    ),
    "pyproject.toml": """[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"
[project]
name = "arithmetic-utility"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = []
[tool.pytest.ini_options]
pythonpath = ["src"]
""",
    "src/arithmetic.py": "def add(a, b):\n    return a + b\n",
    "tests/test_arithmetic.py": (
        "from arithmetic import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    ),
    ".github/workflows/ci.yml": """name: CI
on: [push, pull_request]
permissions:
  contents: read
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
        with:
          persist-credentials: false
      - uses: actions/setup-python@v6
        with:
          python-version: '3.13'
      - run: python -m pip install . pytest ruff build
      - name: Test
        run: python -m pytest
      - run: python -m ruff check .
      - run: python -m build
""",
}


def scenarios():
    """Expected defects are for manual review; they are never sent to the model."""
    obvious = dict(BASE_FILES)
    obvious[".github/workflows/ci.yml"] = obvious[".github/workflows/ci.yml"].replace(
        "      - name: Test\n", "      - name: Test\n        continue-on-error: true\n"
    )
    obvious["src/arithmetic.py"] = """from pathlib import Path
LOG_PATH = "/Users/alice/Desktop/prod-app/logs/service.log"

def add(a, b):
    return a + b

def record_event(event):
    with Path(LOG_PATH).open("a") as stream:
        stream.write(event)
"""
    subtle = dict(BASE_FILES)
    subtle[".github/workflows/ci.yml"] = """name: CI
on:
  pull_request_target:
permissions:
  contents: write
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
        with:
          ref: ${{ github.event.pull_request.head.sha }}
      - uses: actions/setup-python@v6
        with:
          python-version: '3.13'
      - run: python scripts/build.py
"""
    subtle["scripts/build.py"] = 'print("Building arithmetic utility")\n'
    return {
        "obvious": (
            obvious,
            [
                "Test step has continue-on-error: true, so failing tests do not gate CI.",
                "Production logging uses a developer-specific absolute path in src/arithmetic.py.",
            ],
        ),
        "subtle": (
            subtle,
            [
                "pull_request_target checks out contributor code and executes its build script "
                "with contents: write and persisted checkout credentials.",
                "The only workflow does not invoke existing tests on push or pull_request.",
            ],
        ),
        "healthy": (dict(BASE_FILES), []),
    }


def create_fixture(root, files):
    root.mkdir()
    run_git(["init", "--template=", "-b", "main"], root)
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    run_git(["add", "."], root)
    run_git(
        [
            "-c",
            "user.name=Local Evaluation",
            "-c",
            "user.email=evaluation@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "Local regression fixture",
        ],
        root,
    )


def run_evaluation(provider, redactor, destination, cases):
    destination.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="audit-evaluation-") as temporary:
        for name in cases:
            files, expected = scenarios()[name]
            root = Path(temporary) / name
            create_fixture(root, files)
            trace_path = destination / f"{name}_trace.jsonl"
            try:
                with trace_path.open("w", encoding="utf-8") as trace:
                    tools = RepositoryTools(root, redactor)
                    report = AuditAgent(provider, tools, trace=trace).run(f"local-fixture://{name}")
                atomic_json(destination / f"{name}_report.json", report)
                results.append(
                    {
                        "case": name,
                        "status": "completed",
                        "expected_defects": expected,
                        "reported_count": len(report["violations"]),
                        "report": f"{name}_report.json",
                        "trace": trace_path.name,
                    }
                )
            except AuditError as exc:
                results.append(
                    {
                        "case": name,
                        "status": "failed",
                        "error": str(exc),
                        "expected_defects": expected,
                    }
                )
    summary = {
        "timestamp": datetime.now(UTC).isoformat(),
        "model": provider.model,
        "scope": "Local synthetic fixtures; expected defects require manual semantic comparison. "
        "These are not public target audits or hidden-repository precision/recall scores.",
        "results": results,
    }
    atomic_json(destination / "evaluation_summary.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("output/evaluation"))
    parser.add_argument("--case", action="append", choices=list(scenarios()))
    args = parser.parse_args(argv)
    try:
        load_local_env(Path.cwd() / ".env")
        key = os.environ.get("GEMINI_API_KEY", "")
        provider = GeminiProvider(key, os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"))
        summary = run_evaluation(
            provider, Redactor([key]), args.output_dir, args.case or list(scenarios())
        )
        for result in summary["results"]:
            print(
                f"{result['case']}: {result['status']} "
                f"({result.get('reported_count', 'unavailable')} findings)"
            )
        return 0 if all(r["status"] == "completed" for r in summary["results"]) else 1
    except (AuditError, OSError) as exc:
        print(
            f"Evaluation failed: {type(exc).__name__}; check API and local configuration.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
