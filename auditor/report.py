"""Validate the output contract and require file evidence observed by the agent."""

import json
import os
import re
import tempfile
from pathlib import Path

from auditor.errors import ToolError

FIELDS = {
    "rule_id",
    "category",
    "severity",
    "file",
    "line",
    "description",
    "evidence",
    "recommendation",
}


def validate_findings(arguments, tools):
    if not isinstance(arguments, dict) or set(arguments) != {"violations"}:
        raise ToolError("submit_report requires exactly one field: violations.")
    findings = arguments["violations"]
    if not isinstance(findings, list) or len(findings) > 100:
        raise ToolError("violations must be an array with at most 100 entries.")
    if not {"list_files", "git_log"} <= tools.successful_tools:
        raise ToolError("Discover files and inspect git_log successfully before submitting.")
    if tools.blobs and "read_file" not in tools.successful_tools:
        raise ToolError("Read source/configuration and interpret it before submitting.")
    result, seen = [], set()
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != FIELDS:
            raise ToolError("Every violation must contain exactly the required eight fields.")
        for name in FIELDS - {"file", "line"}:
            if not isinstance(finding[name], str) or not finding[name].strip():
                raise ToolError(f"{name} must be a nonempty string.")
            if len(finding[name]) > 6000:
                raise ToolError(f"{name} exceeds the 6000-character limit.")
        if not re.fullmatch(r"[A-Z][A-Z0-9-]*-\d{3}", finding["rule_id"]):
            raise ToolError("rule_id must be a stable code such as SEC-001 or CI-001.")
        if finding["severity"] not in {"critical", "high", "medium", "low"}:
            raise ToolError("severity must be critical, high, medium, or low.")
        path, line = finding["file"], finding["line"]
        if path is None:
            if line is not None:
                raise ToolError("A null file requires a null line.")
            if len(tools.listed_paths) != len(tools.tree):
                raise ToolError(
                    "Paginate all tracked files before reporting a repository-wide absence."
                )
        elif not isinstance(path, str) or path not in tools.observed_lines | dict.fromkeys(
            tools.read_paths
        ):
            raise ToolError("The finding cites a file that has not been inspected.")
        elif line is not None and (
            type(line) is not int or line not in tools.observed_lines.get(path, set())
        ):
            raise ToolError("The finding cites a line that has not been observed.")
        key = (finding["rule_id"], path, line)
        if key not in seen:
            seen.add(key)
            result.append(tools.redactor.clean(finding))
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(result, key=lambda f: (rank[f["severity"]], f["file"] or "", f["line"] or 0))


def atomic_json(path, report):
    """Do not leave a partial report if writing fails; preserve any previous report."""
    path = Path(path)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".audit-",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(report, stream, indent=2, ensure_ascii=True, allow_nan=False)
            stream.write("\n")
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
