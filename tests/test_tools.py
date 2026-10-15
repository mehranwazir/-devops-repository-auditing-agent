import json

import pytest

from auditor.errors import ToolError
from auditor.redaction import Redactor
from tools import MAX_BLOB_BYTES


def test_files_pagination_and_no_target_changes(make_repo):
    tools, git = make_repo({"README.md": "hello\n", ".github/workflows/ci.yml": "name: CI\n"})
    before = git("status", "--porcelain")
    page = tools.list_files(limit=1)
    assert page["next_offset"] == 1
    assert tools.list_files(offset=1)["next_offset"] is None
    tools.read_file("README.md")
    assert tools.git_log()["commits"][0]["subject"] == "fixture"
    assert git("status", "--porcelain") == before


def test_numbered_read_and_literal_search(make_repo):
    tools, _ = make_repo({"src/app.py": "# TODO\nprint('a.*b')\n# todo later\n"})
    page = tools.read_file("src/app.py", start_line=2, max_lines=1)
    assert page["lines"] == [{"line": 2, "text": "print('a.*b')"}]
    assert page["next_line"] == 3
    assert len(tools.search_code("TODO", glob="*.py")["matches"]) == 2
    assert len(tools.search_code("a.*b")["matches"]) == 1


@pytest.mark.parametrize("path", ["../outside", "/etc/passwd", "HEAD:README.md", ".git/config"])
def test_reject_untracked_and_traversal(make_repo, path):
    tools, _ = make_repo({"README.md": "hello\n"})
    with pytest.raises(ToolError):
        tools.read_file(path)


def test_binary_large_files_and_search_skips(make_repo):
    tools, _ = make_repo({"a.bin": b"a\0b", "b.txt": "x" * (MAX_BLOB_BYTES + 1)})
    with pytest.raises(ToolError, match="Binary"):
        tools.read_file("a.bin")
    with pytest.raises(ToolError, match="exceeds"):
        tools.read_file("b.txt")
    assert len(tools.search_code("x")["skipped"]) == 2


def test_search_bound_reports_cursor(make_repo):
    tools, _ = make_repo({"a.txt": "hit\nhit\n", "b.txt": "hit\n"})
    result = tools.search_code("hit", limit=1)
    assert result["truncated_within_file"] is True
    assert result["next_offset"] == 0


def test_search_resumes_inside_file_without_repeating_or_losing_matches(make_repo):
    tools, _ = make_repo({"a.txt": "hit\nhit\n", "b.txt": "hit\n"})
    cursor, line, matches = 0, 1, []
    for _ in range(5):
        page = tools.search_code("hit", offset=cursor, start_line=line, limit=1)
        matches.extend((match["path"], match["line"]) for match in page["matches"])
        cursor, line = page["next_offset"], page["next_start_line"]
        if cursor is None:
            break
    assert cursor is None
    assert matches == [("a.txt", 1), ("a.txt", 2), ("b.txt", 1)]


def test_long_search_line_shows_the_match_in_its_snippet(make_repo):
    tools, _ = make_repo({"a.txt": "prefix " + "x" * 1000 + " continue-on-error: true\n"})
    result = tools.search_code("continue-on-error")["matches"][0]
    assert "continue-on-error" in result["text"]
    assert result["column_start"] > 1 and result["snippet_truncated"]


def test_redaction_metadata_distinguishes_references_from_credential_shapes(make_repo):
    secret = "ghp_" + "Z" * 36
    source = f'API_KEY=os.getenv("KEY")\nPASSWORD="changeme"\nTOKEN="{secret}"\n'
    tools, _ = make_repo({"config.py": source})
    result = tools.read_file("config.py")
    kinds = {(signal["line"], signal["kind"]) for signal in result["redactions"]}
    assert (1, "environment_reference") in kinds
    assert (2, "placeholder") in kinds
    assert (3, "credential_format") in kinds
    assert secret not in json.dumps(result)


def test_redaction_does_not_consume_next_line_after_empty_assignment():
    safe, _ = Redactor().source("API_KEY=\nGEMINI_MODEL=model-name\n")
    assert "GEMINI_MODEL=model-name" in safe


@pytest.mark.parametrize("args", [{"limit": True}, {"offset": -1}, {"limit": 501}])
def test_tool_bounds(make_repo, args):
    tools, _ = make_repo({})
    with pytest.raises(ToolError):
        tools.list_files(**args)


def test_secret_redaction_before_tool_output_and_preserve_lines(make_repo):
    secret = "ghp_" + "Q" * 36
    tools, _ = make_repo({"config.py": f'API_KEY = "{secret}"\nvalue = 2\n'})
    assert secret not in json.dumps(tools.read_file("config.py"))
    assert tools.read_file("config.py")["lines"][1]["line"] == 2
    text = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\nnext\n"
    assert Redactor().text(text).splitlines()[3] == "next"


def test_unknown_and_invalid_tool_arguments(make_repo):
    tools, _ = make_repo({})
    with pytest.raises(ToolError):
        tools.dispatch("run_shell", {"command": "echo unsafe"})
    with pytest.raises(ToolError):
        tools.dispatch("git_log", {"unexpected": True})


def test_symlink_mode_not_readable(make_repo):
    tools, git = make_repo({"README.md": "readme\n"})
    oid = git("rev-parse", "HEAD:README.md").strip()
    git("update-index", "--add", "--cacheinfo", f"120000,{oid},link")
    git(
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "symlink",
    )
    tools = type(tools)(tools.repository)
    with pytest.raises(ToolError, match="Symlinks"):
        tools.read_file("link")
