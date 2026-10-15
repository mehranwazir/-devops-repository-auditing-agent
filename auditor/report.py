"""Validate the output contract and require file evidence observed by the agent."""

import json
import os
import tempfile
from pathlib import Path

from auditor.dto import INSPECTION_AREAS, SEVERITY_RANK, AreaAssessmentDTO, ViolationDTO
from auditor.errors import ToolError


def validate_assessment(value, tools):
    if not isinstance(value, list) or len(value) != len(INSPECTION_AREAS):
        raise ToolError("Before submitting, assess all nine inspection areas exactly once.")
    areas = [AreaAssessmentDTO.from_mapping(item) for item in value]
    if {area.area for area in areas} != set(INSPECTION_AREAS):
        raise ToolError("Assessment has missing or duplicate inspection areas.")
    for area in areas:
        if (
            area.file is not None
            and area.file not in tools.read_paths | tools.observed_lines.keys()
        ):
            raise ToolError("Assessment cites a file that has not been inspected.")
        if area.line is not None and area.line not in tools.observed_lines.get(area.file, set()):
            raise ToolError("Assessment cites a line that has not been observed.")
        if (
            area.file is None
            and area.status == "assessed"
            and len(tools.listed_paths) != len(tools.tree)
        ):
            raise ToolError("A repository-wide assessment requires complete file discovery.")
    return [tools.redactor.clean(area.to_dict()) for area in areas]


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
        dto = ViolationDTO.from_mapping(finding)
        path, line = dto.file, dto.line
        if path is None:
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
        key = (dto.rule_id, path, line)
        if key not in seen:
            seen.add(key)
            result.append(ViolationDTO.from_mapping(tools.redactor.clean(dto.to_dict())).to_dict())
    return sorted(
        result, key=lambda f: (SEVERITY_RANK[f["severity"]], f["file"] or "", f["line"] or 0)
    )


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
