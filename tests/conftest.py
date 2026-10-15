import subprocess

import pytest

from auditor.dto import INSPECTION_AREAS
from auditor.repository import git_environment
from tools import RepositoryTools


@pytest.fixture
def make_repo(tmp_path):
    counter = 0

    def create(files):
        nonlocal counter
        counter += 1
        root = tmp_path / f"repo-{counter}"
        root.mkdir()

        def git(*args):
            return subprocess.run(
                ["git", *args],
                cwd=root,
                env=git_environment(),
                check=True,
                capture_output=True,
                text=True,
            ).stdout

        git("init", "--template=", "-b", "main")
        for name, value in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(value, bytes):
                path.write_bytes(value)
            else:
                path.write_text(value, encoding="utf-8")
        git("add", ".")
        git(
            "-c",
            "user.name=Audit Tests",
            "-c",
            "user.email=tests@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-m",
            "fixture",
        )
        return RepositoryTools(root), git

    return create


def model_call(name, **arguments):
    if name == "submit_report" and "assessment" not in arguments:
        # Unit-test fixtures exercise focused behavior, not a complete semantic audit.
        arguments["assessment"] = [
            {
                "area": area,
                "status": "unavailable",
                "explanation": "This focused unit test does not assess this DevOps area.",
                "file": None,
                "line": None,
            }
            for area in INSPECTION_AREAS
        ]
    return {"role": "model", "parts": [{"functionCall": {"name": name, "args": arguments}}]}
