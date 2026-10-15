"""Small Gemini REST adapter. All tool dispatch stays in our own Python loop."""

import copy
import json
import re
import time
import urllib.error
import urllib.request

from auditor.errors import AuditError


def gemini_schema(value):
    """Convert the shared JSON Schema subset to Gemini's OpenAPI Schema subset."""
    if isinstance(value, list):
        return [gemini_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {
        key: gemini_schema(item) for key, item in value.items() if key != "additionalProperties"
    }
    if isinstance(result.get("type"), list):
        result["type"] = next(kind for kind in result["type"] if kind != "null")
        result["nullable"] = True
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class GeminiProvider:
    def __init__(self, api_key, model="gemini-3.5-flash-lite", timeout=120, retries=2):
        if not api_key or api_key.strip() in {"your_key", "your-key", "YOUR_API_KEY"}:
            raise AuditError("Set GEMINI_API_KEY in your environment or local .env file.")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", model):
            raise AuditError("GEMINI_MODEL contains an invalid model name.")
        self.api_key, self.model = api_key.strip(), model
        self.timeout, self.retries = timeout, retries
        self.opener = urllib.request.build_opener(NoRedirect())

    def generate(self, instructions, contents, schemas):
        payload = {
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": contents,
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": tool["name"],
                            "description": tool["description"],
                            "parameters": gemini_schema(tool["parameters"]),
                        }
                        for tool in schemas
                    ]
                }
            ],
            "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},
            "generationConfig": {"maxOutputTokens": 8192},
        }
        if self.model.startswith("gemini-3"):
            payload["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "LOW"}
        encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode()
        request = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            data=encoded,
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            method="POST",
        )
        for attempt in range(self.retries + 1):
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise AuditError("Gemini response exceeded the safety limit.")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise AuditError("Gemini returned an invalid response object.")
                candidates = data.get("candidates", [])
                if not isinstance(candidates, list) or not candidates:
                    raise AuditError(
                        "Gemini returned no candidate; check provider safety/quota settings."
                    )
                candidate = candidates[0]
                if not isinstance(candidate, dict):
                    raise AuditError("Gemini returned an invalid candidate.")
                if candidate.get("finishReason") not in {None, "STOP"}:
                    raise AuditError(
                        "Gemini response was blocked or incomplete; no report was written."
                    )
                content = candidate.get("content", {})
                if (
                    not isinstance(content, dict)
                    or content.get("role") != "model"
                    or not isinstance(content.get("parts"), list)
                    or not content["parts"]
                ):
                    raise AuditError("Gemini returned an invalid model message.")
                # Preserve the complete content, including any opaque thought signatures.
                return copy.deepcopy(content)
            except urllib.error.HTTPError as exc:
                code = exc.code
                exc.close()
                if code in {429, 500, 502, 503, 504} and attempt < self.retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                hints = {
                    400: "Request rejected; check function-calling support and configuration.",
                    401: "Invalid API key. Check GEMINI_API_KEY.",
                    403: "API access denied. Check key restrictions, region, and project access.",
                    404: "Model unavailable. Set GEMINI_MODEL to an available model.",
                    429: "Gemini quota exhausted. Wait for reset or use another free-tier model.",
                }
                raise AuditError(
                    f"Gemini HTTP {code}: " + hints.get(code, "Provider unavailable.")
                ) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt < self.retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise AuditError(
                    "Gemini connection failed or timed out; check network access."
                ) from None
            except (ValueError, KeyError, TypeError):
                raise AuditError("Gemini returned malformed data; no report was written.") from None
        raise AuditError("Gemini retry limit exhausted.")
