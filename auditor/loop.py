"""Observe -> LLM selection -> dispatch -> interpret -> follow-up -> validated report."""

import json
from datetime import UTC, datetime

from auditor.errors import AuditError, ToolError
from auditor.prompts import SYSTEM_PROMPT
from auditor.report import validate_findings
from tools import TOOL_SCHEMAS


class AuditAgent:
    def __init__(self, provider, tools, max_turns=28, max_calls=90, trace=None):
        self.provider, self.tools = provider, tools
        self.max_turns, self.max_calls, self.trace = max_turns, max_calls, trace
        self.calls = 0

    def event(self, event, **values):
        if self.trace:
            payload = self.tools.redactor.clean(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "event": event,
                    **values,
                }
            )
            self.trace.write(json.dumps(payload, ensure_ascii=True, allow_nan=False) + "\n")
            self.trace.flush()

    def run(self, url):
        history = [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            f"Audit public repository {url} at commit {self.tools.commit}. "
                            f"Budget: {self.max_turns} model turns; "
                            f"{self.max_calls} tool calls. "
                            "Discover the repository, follow evidence, "
                            "and finish with submit_report."
                        )
                    }
                ],
            }
        ]
        self.event(
            "start",
            repository=url,
            commit=self.tools.commit,
            model=getattr(self.provider, "model", "test-double"),
        )
        for turn in range(1, self.max_turns + 1):
            if len(json.dumps(history, ensure_ascii=True)) > 600_000:
                raise AuditError(
                    "Inspection context budget exhausted; no completed report was written."
                )
            content = self.provider.generate(SYSTEM_PROMPT, history, TOOL_SCHEMAS)
            if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
                raise AuditError("Provider returned an invalid message.")
            history.append(content)
            requests = [
                part["functionCall"]
                for part in content["parts"]
                if isinstance(part, dict) and "functionCall" in part
            ]
            if not requests:
                raise AuditError("The model did not select a tool or submit a report.")
            if len(requests) + self.calls > self.max_calls:
                raise AuditError("Tool-call budget exhausted; no completed report was written.")
            responses = []
            for call in requests:
                self.calls += 1
                if not isinstance(call, dict):
                    raise AuditError("Provider returned a malformed function call.")
                name, arguments = call.get("name", ""), call.get("args", {})
                if not isinstance(name, str) or not name:
                    raise AuditError("Provider returned an invalid function name.")
                self.event("tool_call", turn=turn, name=name, arguments=arguments)
                try:
                    if name == "submit_report":
                        if len(requests) != 1:
                            raise ToolError(
                                "Submit the report alone, after interpreting other results."
                            )
                        findings = validate_findings(arguments, self.tools)
                        report = {"repository": url, "violations": findings}
                        self.event("complete", report=report, tool_calls=self.calls, turns=turn)
                        return report
                    result = self.tools.dispatch(name, arguments)
                except ToolError as exc:
                    result = {"error": str(exc), "available": False}
                result = self.tools.redactor.clean(result)
                self.event("tool_result", turn=turn, name=name, result=result)
                response = {"name": name, "response": {"result": result}}
                if "id" in call:
                    response["id"] = call["id"]
                responses.append({"functionResponse": response})
            history.append({"role": "user", "parts": responses})
        raise AuditError("Model-turn budget exhausted; no completed report was written.")
