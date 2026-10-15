import io
import json

import pytest
from conftest import model_call

from auditor.errors import AuditError, ToolError
from auditor.loop import AuditAgent
from auditor.report import atomic_json, validate_findings

URL = "https://github.com/example/demo"


def finding(**updates):
    value = dict(
        rule_id="CI-001",
        category="ci",
        severity="medium",
        file="ci.yml",
        line=1,
        description="Test failure is ignored",
        evidence="continue-on-error is enabled",
        recommendation="Remove continue-on-error from the test step.",
    )
    return value | updates


class ScriptedProvider:
    """Test double only: production never uses scripted decisions or canned reports."""

    def __init__(self, replies):
        self.replies = iter(replies)
        self.histories = []

    def generate(self, instructions, history, schemas):
        self.histories.append(json.loads(json.dumps(history)))
        return next(self.replies)


def discovery():
    return [model_call("list_files"), model_call("git_log"), model_call("read_file", path="ci.yml")]


def test_dynamic_follow_up_uses_previous_observations(make_repo):
    tools, _ = make_repo({"ci.yml": "run: make test\n", "Makefile": "test:\n\tpytest || true\n"})

    class ObservingProvider(ScriptedProvider):
        def generate(self, instructions, history, schemas):
            if len(self.histories) == 3:
                result = history[-1]["parts"][0]["functionResponse"]["response"]["result"]
                assert "make test" in result["lines"][0]["text"]
            return super().generate(instructions, history, schemas)

    provider = ObservingProvider(
        discovery()
        + [
            model_call("read_file", path="Makefile"),
            model_call(
                "submit_report",
                violations=[
                    finding(
                        file="Makefile",
                        line=2,
                        evidence="pytest || true turns test failures into success",
                    )
                ],
            ),
        ]
    )
    trace = io.StringIO()
    result = AuditAgent(provider, tools, trace=trace).run(URL)
    assert result["violations"][0]["file"] == "Makefile"
    assert [json.loads(line)["event"] for line in trace.getvalue().splitlines()][-1] == "complete"


def test_healthy_repository_can_produce_empty_findings(make_repo):
    tools, _ = make_repo({"ci.yml": "run: pytest\n"})
    provider = ScriptedProvider(discovery() + [model_call("submit_report", violations=[])])
    assert AuditAgent(provider, tools).run(URL) == {"repository": URL, "violations": []}


def test_tool_error_is_returned_and_agent_recovers(make_repo):
    tools, _ = make_repo({"ci.yml": "run: pytest\n"})
    provider = ScriptedProvider(
        [model_call("read_file", path="missing")]
        + discovery()
        + [
            model_call("submit_report", violations=[]),
        ]
    )
    assert AuditAgent(provider, tools).run(URL)["violations"] == []
    error = provider.histories[1][-1]["parts"][0]["functionResponse"]["response"]["result"]
    assert error["available"] is False


def test_unobserved_line_rejected_then_corrected(make_repo):
    tools, _ = make_repo({"ci.yml": "continue-on-error: true\n"})
    provider = ScriptedProvider(
        discovery()
        + [
            model_call("submit_report", violations=[finding(line=999)]),
            model_call("submit_report", violations=[finding()]),
        ]
    )
    report = AuditAgent(provider, tools).run(URL)
    assert report["violations"][0]["line"] == 1
    assert "not been observed" in json.dumps(provider.histories[-1][-1])


@pytest.mark.parametrize(
    "changes",
    [
        {"severity": "urgent"},
        {"line": True},
        {"file": "ghost"},
        {"file": None},
        {"rule_id": "bad"},
        {"evidence": ""},
    ],
)
def test_report_contract_rejects_bad_findings(make_repo, changes):
    tools, _ = make_repo({"ci.yml": "continue-on-error: true\n"})
    tools.list_files()
    tools.git_log()
    tools.read_file("ci.yml")
    with pytest.raises(ToolError):
        validate_findings({"violations": [finding(**changes)]}, tools)


def test_missing_file_uses_null_and_requires_full_listing(make_repo):
    tools, _ = make_repo({"ci.yml": "run: pytest\n", "app.py": "pass\n"})
    tools.list_files(limit=1)
    tools.git_log()
    tools.read_file("ci.yml")
    report = {"violations": [finding(file=None, line=None)]}
    with pytest.raises(ToolError, match="Paginate"):
        validate_findings(report, tools)
    tools.list_files(offset=1)
    assert validate_findings(report, tools)[0]["file"] is None


def test_duplicates_collapsed_and_secrets_not_traced(make_repo):
    tools, _ = make_repo({"ci.yml": "continue-on-error: true\n"})
    tools.list_files()
    tools.git_log()
    tools.read_file("ci.yml")
    assert len(validate_findings({"violations": [finding(), finding()]}, tools)) == 1
    trace = io.StringIO()
    AuditAgent(None, tools, trace=trace).event("test", value="password=secret-value")
    assert "secret-value" not in trace.getvalue()


def test_exhausted_loop_is_fatal_not_clean_report(make_repo):
    tools, _ = make_repo({})
    with pytest.raises(AuditError, match="budget"):
        AuditAgent(ScriptedProvider([model_call("list_files")]), tools, max_turns=1).run(URL)


def test_no_tools_is_fatal(make_repo):
    tools, _ = make_repo({})
    with pytest.raises(AuditError, match="did not select"):
        AuditAgent(ScriptedProvider([{"role": "model", "parts": [{"text": "done"}]}]), tools).run(
            URL
        )


def test_atomic_report_written_as_valid_json(tmp_path):
    path = tmp_path / "report.json"
    atomic_json(path, {"repository": URL, "violations": []})
    assert json.loads(path.read_text())["violations"] == []
