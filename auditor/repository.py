"""Fetch public GitHub repositories into disposable bare object databases."""

import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from auditor.errors import AuditError, ToolError


def normalize_url(value):
    match = re.fullmatch(
        r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/"
        r"([A-Za-z0-9_.-]{1,100})/?",
        value,
    )
    if not match:
        raise AuditError("Use a public URL of the form https://github.com/OWNER/REPOSITORY.")
    owner, name = match.groups()
    name = name.removesuffix(".git")
    if not name or name in {".", ".."}:
        raise AuditError("The GitHub repository name is invalid.")
    return f"https://github.com/{owner}/{name}"


def git_environment():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_ALLOW_PROTOCOL": "https",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        }
    )
    return env


def run_git(args, cwd, timeout=30, max_bytes=8_000_000):
    executable = shutil.which("git")
    if not executable:
        raise AuditError("Git is missing. Install Git and put it on PATH.")
    command = [executable, "-c", "core.hooksPath=", "-c", "credential.helper=", *args]
    try:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            with subprocess.Popen(
                command,
                cwd=cwd,
                env=git_environment(),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
            ) as process:
                try:
                    code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise ToolError(f"Git operation timed out after {timeout} seconds.") from None
            if code:
                # Git diagnostics may include target-controlled text. Never expose them.
                raise ToolError(
                    "Git operation failed. Check the public repository URL, network, "
                    "Git installation, and repository availability."
                )
            if out.tell() > max_bytes:
                raise ToolError("Git output exceeded the safety limit; inspection is incomplete.")
            out.seek(0)
            return out.read()
    except OSError:
        raise ToolError(
            "Git could not be started or its temporary output could not be stored."
        ) from None


@contextmanager
def clone_repository(url, timeout=90):
    url = normalize_url(url)
    with tempfile.TemporaryDirectory(prefix="devops-audit-") as temporary:
        directory = Path(temporary)
        bare = directory / "objects.git"
        run_git(
            [
                "clone",
                "--bare",
                "--depth=50",
                "--single-branch",
                "--no-tags",
                "--template=",
                "--",
                url + ".git",
                str(bare),
            ],
            directory,
            timeout=timeout,
        )
        yield bare
