"""Unattended CLI: python agent.py --repo URL --output report.json."""

import argparse
import os
import sys
from contextlib import ExitStack
from pathlib import Path

from auditor.errors import AuditError
from auditor.loop import AuditAgent
from auditor.provider import GeminiProvider
from auditor.redaction import Redactor
from auditor.report import atomic_json
from auditor.repository import clone_repository, normalize_url
from tools import RepositoryTools


def load_local_env(path):
    """Read only the user's local configuration; never source/execute a .env file."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if separator and name in {"GEMINI_API_KEY", "GEMINI_MODEL"}:
            os.environ.setdefault(name, value.strip().strip("\"'"))


def positive(value):
    number = int(value)
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError("must be from 1 to 100")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description="LLM-directed read-only GitHub DevOps audit")
    parser.add_argument("--repo", required=True, help="Public https://github.com/OWNER/REPO URL")
    parser.add_argument("--output", required=True, type=Path, help="Destination JSON report")
    parser.add_argument("--trace", type=Path, help="Optional redacted JSONL tool-call trace")
    parser.add_argument("--model", help="Override GEMINI_MODEL (default: gemini-3.5-flash-lite)")
    parser.add_argument("--max-turns", type=positive, default=28)
    parser.add_argument("--max-calls", type=positive, default=90)
    args = parser.parse_args(argv)
    try:
        url = normalize_url(args.repo)
        if args.trace and args.trace.resolve() == args.output.resolve():
            raise AuditError("--trace and --output must be different paths.")
        # Load from the invocation directory, before any target is fetched.
        load_local_env(Path.cwd() / ".env")
        key = os.environ.get("GEMINI_API_KEY", "")
        model = args.model or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
        provider = GeminiProvider(key, model)
        redactor = Redactor([key])
        with ExitStack() as stack:
            trace = (
                stack.enter_context(args.trace.open("w", encoding="utf-8")) if args.trace else None
            )
            bare = stack.enter_context(clone_repository(url))
            tools = RepositoryTools(bare, redactor)
            runner = AuditAgent(provider, tools, args.max_turns, args.max_calls, trace)
            report = runner.run(url)
            atomic_json(args.output, report)
        print(f"Audit completed: {len(report['violations'])} violation(s).", file=sys.stderr)
        return 0
    except AuditError as exc:
        print(f"Audit failed: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print(
            "Audit failed: cannot read/write local files; check paths and permissions.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("Audit interrupted; no completed report was written.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
