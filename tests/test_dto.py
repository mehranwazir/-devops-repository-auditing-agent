from dataclasses import FrozenInstanceError

import pytest

from auditor.dto import AuditReportDTO, ToolCallDTO, ViolationDTO
from auditor.errors import ToolError


def valid_finding():
    return dict(
        rule_id="CI-001",
        category="ci",
        severity="medium",
        file="ci.yml",
        line=2,
        description="Tests do not gate CI",
        evidence="continue-on-error: true",
        recommendation="Remove continue-on-error from the test step.",
    )


def test_report_dto_roundtrip_has_exact_assignment_contract():
    finding = ViolationDTO.from_mapping(valid_finding())
    report = AuditReportDTO("https://github.com/example/demo", (finding,)).to_dict()
    assert set(report) == {"repository", "violations"}
    assert report["violations"] == [valid_finding()]
    with pytest.raises(FrozenInstanceError):
        finding.line = 3


@pytest.mark.parametrize(
    "updates", [{"line": True}, {"line": 0}, {"file": 3}, {"severity": []}, {"recommendation": ""}]
)
def test_dto_rejects_invalid_data_before_serialization(updates):
    with pytest.raises(ToolError):
        ViolationDTO.from_mapping(valid_finding() | updates)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {"name": []},
        {"name": "read_file", "args": []},
        {"name": "read_file", "args": {}, "id": 3},
    ],
)
def test_malformed_tool_call_rejected_without_uncaught_type_errors(value):
    with pytest.raises(ToolError):
        ToolCallDTO.from_mapping(value)
