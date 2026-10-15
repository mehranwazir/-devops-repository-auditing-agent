"""Read-only, bounded tools over committed Git blobs. Never execute target code."""

import fnmatch
from collections import OrderedDict

from auditor.errors import ToolError
from auditor.redaction import Redactor
from auditor.repository import run_git

MAX_BLOB_BYTES = 256_000
MAX_FILES = 20_000


def bounded_integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ToolError(f"{name} must be an integer from {minimum} to {maximum}.")
    return value


class RepositoryTools:
    def __init__(self, repository, redactor=None):
        self.repository = repository
        self.redactor = redactor or Redactor()
        self.observed_lines = {}
        self.read_paths = set()
        self.successful_tools = set()
        self.listed_paths = set()
        self.tree = []
        self.blobs = {}
        self._line_cache = OrderedDict()
        data = run_git(["ls-tree", "-r", "-z", "--full-tree", "HEAD"], repository)
        for record in data.split(b"\0"):
            if not record:
                continue
            metadata, path_bytes = record.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split()
            path = path_bytes.decode("utf-8", errors="replace")
            if "\ufffd" in path or any(ord(char) < 32 for char in path):
                raise ToolError("A tracked path has unsupported encoding or control characters.")
            entry = {"path": path, "mode": mode, "type": kind}
            self.tree.append(entry)
            if kind == "blob" and mode in {"100644", "100755"}:
                self.blobs[path] = oid
        if len(self.tree) > MAX_FILES:
            raise ToolError(f"Repository exceeds the {MAX_FILES}-file inspection limit.")
        self.tree.sort(key=lambda entry: entry["path"])
        self.commit = run_git(["rev-parse", "HEAD"], repository).decode().strip()

    def _lines(self, path):
        if not isinstance(path, str) or path not in self.blobs:
            raise ToolError(
                "File is not a tracked regular file. Symlinks/submodules are not followed."
            )
        if path in self._line_cache:
            self._line_cache.move_to_end(path)
            return self._line_cache[path]
        oid = self.blobs[path]
        size = int(run_git(["cat-file", "-s", oid], self.repository))
        if size > MAX_BLOB_BYTES:
            raise ToolError(f"File exceeds the {MAX_BLOB_BYTES}-byte inspection limit.")
        raw = run_git(["cat-file", "blob", oid], self.repository, max_bytes=MAX_BLOB_BYTES)
        if b"\0" in raw:
            raise ToolError("Binary file cannot be inspected as text.")
        try:
            lines = self.redactor.text(raw.decode("utf-8-sig")).splitlines()
            self._line_cache[path] = lines
            if len(self._line_cache) > 64:
                self._line_cache.popitem(last=False)
            return lines
        except UnicodeDecodeError:
            raise ToolError("File is not UTF-8 text.") from None

    def list_files(self, prefix="", offset=0, limit=200):
        """Discover tracked paths; paginate until next_offset is null for complete coverage."""
        if not isinstance(prefix, str):
            raise ToolError("prefix must be a string.")
        bounded_integer(offset, "offset", 0, MAX_FILES)
        bounded_integer(limit, "limit", 1, 500)
        entries = [entry for entry in self.tree if entry["path"].startswith(prefix)]
        page = entries[offset : offset + limit]
        self.successful_tools.add("list_files")
        self.listed_paths.update(entry["path"] for entry in page)
        return self.redactor.clean(
            {
                "files": page,
                "total": len(entries),
                "offset": offset,
                "next_offset": offset + limit if offset + limit < len(entries) else None,
                "scope": "HEAD tracked files only; no working tree or remote settings",
            }
        )

    def read_file(self, path, start_line=1, max_lines=160):
        """Read numbered, redacted lines from a tracked regular file."""
        bounded_integer(start_line, "start_line", 1, 1_000_000)
        bounded_integer(max_lines, "max_lines", 1, 300)
        lines = self._lines(path)
        if start_line > len(lines) + 1:
            raise ToolError("start_line is past the end of the file.")
        page = []
        size = 0
        for number in range(start_line, min(len(lines) + 1, start_line + max_lines)):
            text = lines[number - 1]
            if size + len(text) > 24_000:
                if not page:
                    raise ToolError("A source line exceeds the tool response limit.")
                break
            page.append({"line": number, "text": text})
            self.observed_lines.setdefault(path, set()).add(number)
            size += len(text)
        self.read_paths.add(path)
        self.successful_tools.add("read_file")
        end = page[-1]["line"] if page else start_line - 1
        return {
            "path": self.redactor.text(path),
            "lines": page,
            "total_lines": len(lines),
            "next_line": end + 1 if end < len(lines) else None,
        }

    def search_code(self, pattern, glob="*", offset=0, limit=40):
        """Case-insensitive literal substring search, with a file page cursor."""
        if not isinstance(pattern, str) or not 1 <= len(pattern) <= 200:
            raise ToolError("pattern must be a literal string of 1 to 200 characters.")
        if not isinstance(glob, str) or len(glob) > 200:
            raise ToolError("glob must be a string up to 200 characters.")
        bounded_integer(offset, "offset", 0, MAX_FILES)
        bounded_integer(limit, "limit", 1, 100)
        paths = sorted(path for path in self.blobs if fnmatch.fnmatchcase(path, glob))
        matches, skipped = [], []
        cursor = offset
        # Finish a file even if match limit is reached, return only bounded snippets.
        for path in paths[offset : offset + 100]:
            cursor += 1
            try:
                lines = self._lines(path)
            except ToolError as exc:
                skipped.append({"path": path, "reason": str(exc)})
                continue
            for number, text in enumerate(lines, 1):
                if pattern.casefold() in text.casefold():
                    matches.append({"path": path, "line": number, "text": text[:500]})
                    self.observed_lines.setdefault(path, set()).add(number)
                    if len(matches) >= limit:
                        # Revisit this file with read_file to inspect any additional matches.
                        self.successful_tools.add("search_code")
                        return self.redactor.clean(
                            {
                                "matches": matches,
                                "skipped": skipped,
                                "next_offset": cursor - 1,
                                "truncated_within_file": True,
                                "total_files": len(paths),
                            }
                        )
        self.successful_tools.add("search_code")
        return self.redactor.clean(
            {
                "matches": matches,
                "skipped": skipped,
                "next_offset": cursor if cursor < len(paths) else None,
                "truncated_within_file": False,
                "total_files": len(paths),
            }
        )

    def git_log(self, limit=15):
        """Read recent commit metadata; shallow history is explicitly identified."""
        bounded_integer(limit, "limit", 1, 50)
        output = run_git(
            [
                "log",
                f"-n{limit}",
                "--format=%H%x00%aI%x00%s",
                "--no-show-signature",
                "HEAD",
            ],
            self.repository,
            max_bytes=100_000,
        ).decode("utf-8", errors="replace")
        commits = []
        for line in output.splitlines():
            pieces = line.split("\0", 2)
            if len(pieces) == 3:
                commits.append(dict(zip(("commit", "date", "subject"), pieces, strict=True)))
        shallow = (
            run_git(["rev-parse", "--is-shallow-repository"], self.repository).strip() == b"true"
        )
        self.successful_tools.add("git_log")
        return self.redactor.clean(
            {
                "commits": commits,
                "shallow": shallow,
                "scope": "Recent metadata only; not a full historical secret scan",
            }
        )

    def dispatch(self, name, arguments):
        if name not in {"list_files", "read_file", "search_code", "git_log"}:
            raise ToolError("Unknown inspection tool.")
        if not isinstance(arguments, dict):
            raise ToolError("Tool arguments must be a JSON object.")
        try:
            return getattr(self, name)(**arguments)
        except TypeError:
            raise ToolError("Invalid arguments for this tool; consult its schema.") from None


def object_schema(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


STRING = {"type": "string"}
INTEGER = {"type": "integer"}
VIOLATION_SCHEMA = object_schema(
    {
        "rule_id": STRING,
        "category": STRING,
        "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
        "file": {"type": ["string", "null"]},
        "line": {"type": ["integer", "null"]},
        "description": STRING,
        "evidence": STRING,
        "recommendation": STRING,
    }
)
TOOL_SCHEMAS = [
    {
        "name": "list_files",
        "description": RepositoryTools.list_files.__doc__,
        "parameters": object_schema({"prefix": STRING, "offset": INTEGER, "limit": INTEGER}),
    },
    {
        "name": "read_file",
        "description": RepositoryTools.read_file.__doc__,
        "parameters": object_schema({"path": STRING, "start_line": INTEGER, "max_lines": INTEGER}),
    },
    {
        "name": "search_code",
        "description": RepositoryTools.search_code.__doc__,
        "parameters": object_schema(
            {"pattern": STRING, "glob": STRING, "offset": INTEGER, "limit": INTEGER}
        ),
    },
    {
        "name": "git_log",
        "description": RepositoryTools.git_log.__doc__,
        "parameters": object_schema({"limit": INTEGER}),
    },
    {
        "name": "submit_report",
        "description": "Finish after interpreting evidence and checking "
        "alternatives. An empty violations array is valid for a healthy repository.",
        "parameters": object_schema({"violations": {"type": "array", "items": VIOLATION_SCHEMA}}),
    },
]
