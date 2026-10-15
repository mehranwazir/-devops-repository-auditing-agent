import io
import json
import urllib.error

import pytest

import agent
from auditor.errors import AuditError
from auditor.provider import GeminiProvider, gemini_schema
from auditor.repository import normalize_url
from tools import TOOL_SCHEMAS


class Response(io.BytesIO):
    pass


class Opener:
    def __init__(self, data):
        self.data = data
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        if isinstance(self.data, Exception):
            raise self.data
        return Response(json.dumps(self.data).encode())


def test_provider_function_round_trip_preserves_opaque_signatures():
    content = {
        "role": "model",
        "parts": [
            {"thoughtSignature": "opaque", "functionCall": {"name": "list_files", "args": {}}}
        ],
    }
    provider = GeminiProvider("test-credential")
    provider.opener = Opener({"candidates": [{"finishReason": "STOP", "content": content}]})
    assert provider.generate("instructions", [], TOOL_SCHEMAS) == content
    request = provider.opener.requests[0]
    assert "test-credential" not in request.full_url
    assert request.get_header("X-goog-api-key") == "test-credential"
    payload = json.loads(request.data)
    assert payload["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"
    assert gemini_schema({"type": ["integer", "null"]}) == {"type": "integer", "nullable": True}


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429, 503])
def test_api_errors_are_actionable_without_exposing_provider_body(code):
    provider = GeminiProvider("test-credential", retries=0)
    provider.opener = Opener(
        urllib.error.HTTPError(
            "https://example.invalid", code, "error", {}, io.BytesIO(b"private provider diagnostic")
        )
    )
    with pytest.raises(AuditError, match=str(code)) as error:
        provider.generate("instructions", [], TOOL_SCHEMAS)
    assert "private provider diagnostic" not in str(error.value)


@pytest.mark.parametrize("data", [{}, [], {"candidates": [{"finishReason": "MAX_TOKENS"}]}])
def test_provider_does_not_accept_empty_or_incomplete_response(data):
    provider = GeminiProvider("test-credential", retries=0)
    provider.opener = Opener(data)
    with pytest.raises(AuditError):
        provider.generate("instructions", [], TOOL_SCHEMAS)


@pytest.mark.parametrize(
    "url",
    [
        "file:///repo",
        "https://evil.test/a/b",
        "https://github.com/a/b/x",
        "https://github.com/a/b?x=1",
        "https://u:p@github.com/a/b",
        "https://github.com/a/..",
    ],
)
def test_url_validation(url):
    with pytest.raises(AuditError):
        normalize_url(url)


def test_normalize_public_git_url():
    assert (
        normalize_url("https://github.com/example/demo.git/") == "https://github.com/example/demo"
    )


def test_cli_missing_key_fails_without_creating_report(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert agent.main(["--repo", "https://github.com/example/demo", "--output", "report.json"]) == 1
    assert not (tmp_path / "report.json").exists()
    assert "GEMINI_API_KEY" in capsys.readouterr().err


def test_env_is_local_allowlisted_and_does_not_overwrite_environment(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        'GEMINI_API_KEY="local"\nGEMINI_MODEL=local-model\nPATH=bad\n', encoding="utf-8"
    )
    monkeypatch.setenv("GEMINI_API_KEY", "existing")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    before = agent.os.environ["PATH"]
    agent.load_local_env(path)
    assert agent.os.environ["GEMINI_API_KEY"] == "existing"
    assert agent.os.environ["GEMINI_MODEL"] == "local-model"
    assert agent.os.environ["PATH"] == before


def test_cli_success_writes_report_with_actual_tool_loop(make_repo, tmp_path, monkeypatch):
    from contextlib import nullcontext

    from conftest import model_call
    from test_agent import ScriptedProvider

    tools, _ = make_repo({"ci.yml": "run: pytest\n"})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "test-credential")
    provider = ScriptedProvider(
        [
            model_call("list_files"),
            model_call("git_log"),
            model_call("read_file", path="ci.yml"),
            model_call("submit_report", violations=[]),
        ]
    )
    monkeypatch.setattr(agent, "GeminiProvider", lambda *args: provider)
    monkeypatch.setattr(agent, "clone_repository", lambda url: nullcontext(tools.repository))
    assert (
        agent.main(
            [
                "--repo",
                "https://github.com/example/demo",
                "--output",
                "result.json",
                "--trace",
                "trace.jsonl",
            ]
        )
        == 0
    )
    assert json.loads((tmp_path / "result.json").read_text())["violations"] == []
    assert (
        json.loads((tmp_path / "trace.jsonl").read_text().splitlines()[-1])["event"] == "complete"
    )


def test_failed_cli_preserves_previous_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    path = tmp_path / "report.json"
    path.write_text("previous report")
    assert (
        agent.main(
            [
                "--repo",
                "https://github.com/example/demo",
                "--output",
                "report.json",
            ]
        )
        == 1
    )
    assert path.read_text() == "previous report"
