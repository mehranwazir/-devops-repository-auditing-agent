"""Validated data transfer objects at model and report boundaries."""

from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

from auditor.errors import ToolError

Severity = Literal["critical", "high", "medium", "low"]
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
INSPECTION_AREAS = (
    "repository_hygiene",
    "dependency_management",
    "code_quality",
    "testing",
    "ci",
    "security",
    "configuration",
    "collaboration",
    "build_release",
)


@dataclass(frozen=True, slots=True)
class ViolationDTO:
    rule_id: str
    category: str
    severity: Severity
    file: str | None
    line: int | None
    description: str
    evidence: str
    recommendation: str

    def __post_init__(self):
        import re

        for field in fields(self):
            value = getattr(self, field.name)
            if field.name not in {"file", "line"}:
                if not isinstance(value, str) or not value.strip():
                    raise ToolError(f"{field.name} must be a nonempty string.")
                if len(value) > 6000:
                    raise ToolError(f"{field.name} exceeds the 6000-character limit.")
        if not re.fullmatch(r"[A-Z][A-Z0-9-]*-\d{3}", self.rule_id):
            raise ToolError("rule_id must be a stable code such as SEC-001 or CI-001.")
        if self.severity not in SEVERITY_RANK:
            raise ToolError("severity must be critical, high, medium, or low.")
        if self.file is not None and (not isinstance(self.file, str) or not self.file):
            raise ToolError("file must be a nonempty string or null.")
        if self.line is not None and (type(self.line) is not int or self.line < 1):
            raise ToolError("line must be a positive integer or null.")
        if self.file is None and self.line is not None:
            raise ToolError("A null file requires a null line.")

    @classmethod
    def from_mapping(cls, value):
        if not isinstance(value, dict) or set(value) != {field.name for field in fields(cls)}:
            raise ToolError("Every violation must contain exactly the required eight fields.")
        return cls(**value)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AuditReportDTO:
    repository: str
    violations: tuple[ViolationDTO, ...]

    def __post_init__(self):
        if not isinstance(self.repository, str) or not self.repository:
            raise ToolError("repository must be a nonempty string.")
        if not isinstance(self.violations, tuple) or any(
            not isinstance(item, ViolationDTO) for item in self.violations
        ):
            raise ToolError("violations must be a tuple of validated ViolationDTO objects.")

    def to_dict(self):
        return {"repository": self.repository, "violations": [v.to_dict() for v in self.violations]}


@dataclass(frozen=True, slots=True)
class ToolCallDTO:
    name: str
    arguments: dict[str, Any]
    call_id: str | None = None

    @classmethod
    def from_mapping(cls, value):
        if not isinstance(value, dict):
            raise ToolError("Provider returned a malformed function call.")
        name, arguments, call_id = value.get("name"), value.get("args", {}), value.get("id")
        if not isinstance(name, str) or not name:
            raise ToolError("Provider returned an invalid function name.")
        if not isinstance(arguments, dict) or any(not isinstance(k, str) for k in arguments):
            raise ToolError("Tool arguments must be a JSON object with string keys.")
        if call_id is not None and not isinstance(call_id, str):
            raise ToolError("Provider returned an invalid function call ID.")
        return cls(name, dict(arguments), call_id)


@dataclass(frozen=True, slots=True)
class AreaAssessmentDTO:
    area: str
    status: Literal["assessed", "not_applicable", "unavailable"]
    explanation: str
    file: str | None
    line: int | None

    @classmethod
    def from_mapping(cls, value):
        if not isinstance(value, dict) or set(value) != {field.name for field in fields(cls)}:
            raise ToolError("Each assessment needs area, status, explanation, file, and line.")
        if not isinstance(value["area"], str) or value["area"] not in INSPECTION_AREAS:
            raise ToolError("Assessment area must be one of the nine documented inspection areas.")
        if not isinstance(value["status"], str) or value["status"] not in {
            "assessed",
            "not_applicable",
            "unavailable",
        }:
            raise ToolError("Assessment status must be assessed, not_applicable, or unavailable.")
        explanation = value["explanation"]
        if not isinstance(explanation, str) or not 20 <= len(explanation.strip()) <= 3000:
            raise ToolError("Assessment explanation must contain 20 to 3000 characters.")
        path, line = value["file"], value["line"]
        if path is not None and (not isinstance(path, str) or not path):
            raise ToolError("Assessment file must be a path or null.")
        if line is not None and (type(line) is not int or line < 1 or path is None):
            raise ToolError("Assessment line must be an applicable positive integer or null.")
        return cls(**value)

    def to_dict(self):
        return asdict(self)
