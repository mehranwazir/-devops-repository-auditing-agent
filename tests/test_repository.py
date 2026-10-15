from pathlib import Path

import pytest

import auditor.repository as repository
from auditor.errors import AuditError, ToolError


def test_git_environment_ignores_injected_configuration(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.sshCommand")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "unsafe")
    monkeypatch.setenv("GIT_DIR", "/unrelated/repository")
    env = repository.git_environment()
    assert "GIT_CONFIG_COUNT" not in env
    assert "GIT_DIR" not in env
    assert env["GIT_ALLOW_PROTOCOL"] == "https"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_missing_git_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr(repository.shutil, "which", lambda name: None)
    with pytest.raises(AuditError, match="Git is missing"):
        repository.run_git(["status"], tmp_path)


def test_failed_clone_cleans_temporary_directory_and_never_yields(monkeypatch):
    locations = []

    def fail(args, cwd, **kwargs):
        locations.append(Path(cwd))
        assert "--bare" in args and "--template=" in args
        raise ToolError("Unavailable repository")

    monkeypatch.setattr(repository, "run_git", fail)
    with pytest.raises(ToolError, match="Unavailable"):
        with repository.clone_repository("https://github.com/example/demo"):
            pytest.fail("Failed clones must not be inspected")
    assert locations and not locations[0].exists()


def test_git_does_not_execute_a_repository_hook(make_repo):
    tools, _ = make_repo({"README.md": "A library\n"})
    marker = tools.repository / "hook-ran"
    hook = tools.repository / ".git" / "hooks" / "post-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\nprintf unsafe > hook-ran\n", encoding="utf-8")
    hook.chmod(0o755)
    repository.run_git(
        [
            "-c",
            "user.name=Tests",
            "-c",
            "user.email=tests@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-m",
            "hook safety",
        ],
        tools.repository,
    )
    assert not marker.exists()
