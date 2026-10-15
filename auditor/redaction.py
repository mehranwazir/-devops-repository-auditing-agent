"""Best-effort credential filtering before repository data crosses a trust boundary."""

import re


class Redactor:
    def __init__(self, secrets=()):
        self.secrets = tuple(value for value in secrets if value)
        self.patterns = [
            re.compile(
                r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
                re.S,
            ),
            re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
            re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
            re.compile(
                r"\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{30,}|AQ\.[A-Za-z0-9_-]{30,})\b"
            ),
            re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
            re.compile(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@"),
        ]
        self.assignment = re.compile(
            r"""(?im)((?:[\w.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)[\w.-]*)["']?\s*[:=]\s*)([^\r\n]+)"""
        )

    def text(self, value):
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]" + "\n" * secret.count("\n"))
        for pattern in self.patterns:
            value = pattern.sub(lambda m: "[REDACTED]" + "\n" * m[0].count("\n"), value)
        return self.assignment.sub(r"\1[REDACTED]", value)

    def clean(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.clean(item) for item in value]
        if isinstance(value, dict):
            return {self.text(str(k)): self.clean(v) for k, v in value.items()}
        return value
