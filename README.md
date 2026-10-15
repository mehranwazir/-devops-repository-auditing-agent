# DevOps Repository Auditing Agent

A Python agent that investigates a public GitHub repository, chooses read-only tools with
Gemini, follows up on observations, and writes an evidence-based JSON report. The reasoning
loop, tool dispatch, budgets, and validation are implemented directly in Python. No LangChain,
LangGraph, n8n, or other agent/orchestration framework is used.

## Requirements

- Python 3.11 or newer and Git on `PATH`.
- Internet access to GitHub and the Gemini Developer API.
- A Gemini API key with access to a function-calling model.

The runtime uses the Python standard library, including `urllib` for Gemini's REST API.
Development tools are optional dependencies. You do not need OpenAI credits.

## Installation and configuration

Clone this repository and enter its directory:

```bash
git clone https://github.com/mehranwazir/-devops-repository-auditing-agent.git
cd ./-devops-repository-auditing-agent
python -m venv .venv
```

Activate the environment on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

On Linux/macOS:

```bash
source .venv/bin/activate
```

Install the project:

```bash
python -m pip install -r requirements.txt
```

Create a key in [Google AI Studio](https://aistudio.google.com/apikey). Copy `.env.example`
to `.env` (`Copy-Item .env.example .env` in PowerShell; `cp .env.example .env` on Linux/macOS).
Set the local values:

```dotenv
GEMINI_API_KEY=replace_with_your_key
GEMINI_MODEL=gemini-3.5-flash-lite
```

Environment variables take precedence over `.env`. Only these two variable names are loaded.
The program never executes `.env` contents. `.env` is ignored by Git; never commit credentials.
Each user supplies their own key; credentials are not included in the repository.

Google currently lists a free tier for selected Gemini models, including Gemini 3.5 Flash-Lite,
with account-specific quotas. To stay on the free tier, use an eligible project without billing.
This application cannot enforce your Google billing configuration. Check
[current pricing](https://ai.google.dev/gemini-api/docs/pricing) and
[rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) before running.

## Execution

The assignment interface is supported exactly:

```bash
python agent.py --repo https://github.com/OWNER/REPOSITORY --output report.json
```

A concrete public repository example:

```bash
python agent.py --repo https://github.com/pallets/click --output report.json --trace trace.jsonl
```

The CLI runs unattended. Success returns exit code `0` and writes the report atomically.
Fatal input, clone, provider, budget, and file errors return nonzero with a message on stderr.
A failed audit does not overwrite a previous report; check the exit code before treating a
pre-existing file as a new result. No synthetic clean report is written on fatal failure.

| Option | Purpose | Default |
| --- | --- | --- |
| `--repo` | Public `https://github.com/OWNER/REPO` URL | Required |
| `--output` | JSON destination; parent directory must exist | Required |
| `--trace` | Redacted JSONL observations and tool calls | Disabled |
| `--model` | Override `GEMINI_MODEL` | `gemini-3.5-flash-lite` |
| `--max-turns` | Maximum model requests, from 1 to 100 | 28 |
| `--max-calls` | Maximum total function calls, from 1 to 100 | 90 |

The installed `devops-audit` command accepts the same options.

## Architecture and agent behavior

```mermaid
flowchart TD
    A[Validate public GitHub URL] --> B[Disposable shallow bare clone]
    B --> C[LLM observes current conversation]
    C --> D{LLM chooses a function}
    D --> E[Bounded read-only Git tools]
    E --> F[Redact and return observations]
    F --> C
    D --> G[submit_report]
    G --> H{Validate schema and observed citations}
    H -->|Needs correction| F
    H -->|Accepted| I[Atomic JSON output]
```

The model decides which tools to call and which files to investigate. There is no deterministic
scanner that manufactures a checklist of violations. For example, after reading `run: make test`
in CI, the model can read `Makefile` and discover whether the invoked command masks failures.
Tool outputs and errors are added to the conversation so the next model request can interpret
them and choose follow-up tools. `submit_report` is also model-selected.

The host requires successful file discovery, commit-history inspection, and a source/configuration
read before accepting a report for a nonempty repository. File citations must reference paths
and lines actually observed through tools. Repository-wide absence findings require complete
file-list pagination. These checks validate provenance, not the semantic truth of a claim;
the model still needs to interpret context correctly. Duplicate rule/file/line findings collapse.

`ToolCallDTO`, `ViolationDTO`, and `AuditReportDTO` are typed data transfer objects in
`auditor/dto.py`. Findings are validated at the model boundary, stored in frozen dataclasses,
checked against observed file evidence, redacted, and converted to plain JSON at the output
boundary. Unknown/missing report fields, invalid severities, and invalid line types are rejected.
This keeps validation separate from Git tools and model decisions while preserving the exact
assignment JSON contract.

Before `submit_report` is accepted, the model must also provide a structured assessment for all
nine DevOps areas, with an observed citation where applicable or an explicit unavailable/not
applicable explanation. `AreaAssessmentDTO` validates this review. It is recorded in the trace;
the report still contains only `repository` and `violations`. This makes review coverage visible
without presenting uninspected checks as successful. Semantic completeness remains model-dependent.

| File | Responsibility |
| --- | --- |
| `agent.py` | CLI, local configuration, lifecycle, and exit codes |
| `tools.py` | `list_files`, `read_file`, `search_code`, `git_log`, tool schemas |
| `auditor/loop.py` | Explicit model/tool loop, budgets, tool errors, traces |
| `auditor/provider.py` | Gemini REST adapter, bounded retries, content/signature preservation |
| `auditor/repository.py` | URL validation, isolated Git configuration, disposable bare clone |
| `auditor/prompts.py` | Investigation guidance, severity policy, false-positive controls |
| `auditor/redaction.py` | Filter known credentials and common secret patterns |
| `auditor/report.py` | Contract validation, deduplication, atomic output |
| `auditor/dto.py` | Typed and validated tool-call, finding, and report DTOs |
| `evaluate.py` | Optional live model regression checks on disposable local fixtures |
| `tests/` | Offline tool, agent, CLI, and provider tests |

## Inspection scope and evidence policy

The model investigates repository hygiene, dependencies, code quality, tests, CI, security,
configuration, collaboration, and build/release practices as relevant to the repository.
It follows CI commands into scripts and considers equivalent implementations before reporting
missing practices. A library without a lockfile or a project without a particular optional
linter is not automatically defective. Review instructions can live in the README.

No claims are made about remote branch protection or review enforcement from local files.
Tests and linters in the target are not run: missing CI test execution can be evidenced from
configuration, but a claim that tests fail cannot. ML training, datasets, model evaluation,
and experiment tracking are outside the audit scope.

Severity guidance: `critical` for exposed usable credentials or privileged untrusted-code
execution; `high` for concrete serious security/release bypasses; `medium` for meaningful
reliability, testing, or configuration defects; `low` for minor evidenced hygiene problems.
Context matters. Fixtures, examples, placeholders, and intentional teaching samples should
not be confused with deployed vulnerabilities.

## Output contract

The top level contains exactly `repository` and `violations`. Every finding contains:

| Field | Meaning |
| --- | --- |
| `rule_id` | Stable code such as `CI-001` or `SEC-001` |
| `category` | DevOps area, such as `ci`, `security`, or `testing` |
| `severity` | `critical`, `high`, `medium`, or `low` |
| `file` | Repository-relative tracked file, or JSON `null` when inapplicable |
| `line` | Observed 1-based source line, or JSON `null` when inapplicable |
| `description` | Defect and its impact |
| `evidence` | Concrete observed fact and explanation; secrets are redacted |
| `recommendation` | Actionable remediation |

An empty `violations` array is a successful valid result when no defensible defect is found.
Line numbers refer to the default-branch commit recorded in the trace, not later revisions.

## Safety and limitations

- Targets are fetched into a disposable bare repository, without checkout. Files are inspected
  by blob object ID. No hooks, filters, submodules, target tests, package installation, or target
  scripts execute. Symlinks and submodules are listed but not followed. No writes reach targets.
- Only public GitHub HTTPS repository URLs are accepted; credentials, query strings, other hosts,
  and local paths are rejected. Git credentials/global configuration are disabled for inspection.
- Source, commit messages, and filenames are untrusted data. The system prompt tells the model
  to ignore repository instructions. Tools have no shell or target-execution capability.
- Common token formats, private keys, credential assignments, and the supplied API key are
  redacted before tool output reaches the model or trace. Redaction is best effort; unfamiliar
  secret formats can evade patterns. Only audit public repositories you are permitted to inspect.
  Tool responses retain safe line-level redaction kinds (environment reference, placeholder,
  sensitive assignment, credential format) without credential values. A recognized token shape
  does not prove the credential is valid; the agent never tries to authenticate with target secrets.
- The model receives repository content via Google's API. Free-tier data handling follows
  [Google's terms and pricing disclosures](https://ai.google.dev/gemini-api/docs/pricing).
- The clone includes up to 50 recent commits from one branch, without tags. `git_log` reads
  metadata, not every historical blob. This is not a full historical secret scanner.
- Limits: 20,000 tracked entries, 256,000 bytes per text blob, 300 lines/24,000 characters per
  read page, 100 files per search page, 600,000 serialized conversation characters. File listing,
  reads, and search expose cursors; truncated/unsupported files must not be treated as clean.
  Search continuation uses both `next_offset` and `next_start_line` so pagination within a file
  neither repeats nor skips matches. Long-line snippets are centered on the matched text.
- Git operations have timeouts (90 seconds for clone, 30 for reads); API requests have a
  120-second socket timeout and at most two short retries for transient errors. There is no hard
  clone disk/network quota or OS-level memory sandbox. Very large or adversarial repositories
  should be audited within an externally resource-limited container/VM.
- Model findings are nondeterministic. Observed-line validation does not prove semantic accuracy,
  recall, or complete coverage. Unavailable checks are recorded in the trace, not fabricated.
  A successful report can reflect limited coverage; inspect traces when evaluating completeness.

## Tests and CI

Install development tools and run checks:

```bash
python -m pip install ".[dev]"
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m build
```

Tests use disposable local Git repositories and scripted **test-only** provider responses.
They require no API key, make no model calls, and do not execute fixture source code. Coverage
includes pagination, literal search, target immutability, path/symlink safety, file limits,
secret redaction, dynamic follow-up selection, healthy output, error recovery, provenance,
deduplication, provider request shape, invalid inputs, and missing credentials.
These tests verify implementation behavior; they are not hidden-repository precision/recall scores.

GitHub Actions runs tests, lint/format checks, and package-build validation on pushes and pull
requests with read-only repository permissions and no API secret requirement. Actions are pinned
to verified commit hashes, and checkout does not persist credentials.

Local validation on Python 3.13.3 passed all 73 tests, Ruff lint and formatting checks, and
both source-distribution and wheel builds. The live sample audit below also completed with
the real Gemini API; the offline test count does not include the separate live runs.

For contributions, use a branch and pull request, describe behavior changes, and run the checks
above. Review safety boundaries and false-positive risks. Never add real credentials to fixtures.

## Sample audit and reproducibility

[`sample_report.json`](sample_report.json) is authentic output from a live audit of
[`pallets/markupsafe`](https://github.com/pallets/markupsafe), using Gemini 3.5 Flash-Lite.
[`sample_trace.jsonl`](sample_trace.jsonl) records the observations and model-selected calls.

| Run detail | Recorded value |
| --- | --- |
| Date | October 16, 2026, 04:46 Asia/Karachi (trace uses UTC) |
| Inspected commit | `4e111271b1494995708a9e878a10829b5e10c7ba` |
| Model turns / function calls | 8 / 12 |
| Violations | 0 |

The model discovered the file tree and commit history, read `pyproject.toml`, investigated test,
publish, pre-commit, and security-analysis workflows, and read pre-commit configuration, source,
and the README. It recorded all nine area assessments before submitting an empty report.
This is a mostly healthy repository example, not proof that every possible defect is absent.
No hidden-repository performance is claimed.

Regenerate the sample with your own key:

```bash
python agent.py --repo https://github.com/pallets/markupsafe --output sample_report.json --trace sample_trace.jsonl
```

To reproduce an audit, run the command with `--trace`, compare the commit in the first trace
event, and retain both files. Repository changes, provider/model updates, and nondeterminism
can change findings. Never interpret unavailable checks or fixture tests as a completed live audit.

## Live detection regression exercise

To demonstrate actual detection rather than only an empty report, run:

```powershell
.\.venv\Scripts\python.exe evaluate.py --output-dir output/evaluation
```

This optional command uses the same real model/tool loop against three disposable local Git
repositories. It never executes fixture tests, scripts, or application code. Known expected
defects are retained for comparison by the evaluator and are **not** passed to the model.

| Case | Deliberately configured behavior |
| --- | --- |
| `obvious` | CI ignores test failures; logging has a developer-specific absolute path |
| `subtle` | A privileged PR workflow runs contributor code; normal test execution is missing |
| `healthy` | A library with tests, lint/build gates, portable source, and documented workflow |

The `*_report.json` files contain actual findings. The `*_trace.jsonl` files contain tool calls
and the nine-area assessment. `evaluation_summary.json` records expected defects and run status
for manual semantic comparison. `local-fixture://...` identifies local synthetic inputs honestly;
these files are not claimed to be public GitHub audits or instructor hidden-repository results.
Use `--case obvious`, `--case subtle`, or `--case healthy` to run one case.

The reviewed live regression run on October 16, 2026 found both seeded issues in `obvious`,
both seeded issues in `subtle`, and no issues in `healthy`. Each final trace includes all nine
area assessments. These are three small controlled cases, not evidence of general 100% recall.
The reports were manually compared with the fixture definitions; summary counts alone do not
establish semantic correctness.

The authentic outputs are retained in [`validation/`](validation/), including
[`evaluation_summary.json`](validation/evaluation_summary.json), reports, and redacted traces.
For example, [`subtle_report.json`](validation/subtle_report.json) shows the critical untrusted
PR execution finding and the separate missing CI validation finding with remediation.


## Implementation checklist

| Requirement | Implementation / verification |
| --- | --- |
| Public GitHub URL and unattended CLI | `agent.py --repo ... --output ...`; URL/CLI tests |
| Explicit Python LLM/tool loop | `auditor/loop.py`; observed-result follow-up tests and live traces |
| Required inspection tools | All four in `tools.py`; no target execution required |
| Nine DevOps areas | Investigation prompt plus validated trace assessments |
| Evidence and actionable remediation | Observed-path/line validation, typed finding DTO, live defect cases |
| Correct JSON, nulls, and severity | DTO validation; exact output fields; no extra coverage keys in report |
| Genuine sample output | `sample_report.json` and its live trace |
| No prohibited framework | Standard-library runtime; explicit dispatch and provider REST adapter |
| Dependency installation and key setup | `requirements.txt`, `pyproject.toml`, `.env.example`, this README |
| Automated tests and CI | Offline regression suite; Python 3.11/3.13 CI with pinned Actions |
| Read-only targets and secret handling | Bare Git inspection, disabled hooks, bounded tools, redaction tests |
| Errors and resource limits | Nonzero fatal exits, bounded retries, timeouts, context/call budgets |

Optional target `run_pytest`/`run_ruff` execution is deliberately omitted: arbitrary repository
code is not executed without an external isolation boundary. Remote branch protection is not
inferred from files. The instructor's hidden repositories and answer keys are unavailable, so
neither complete defect detection nor a particular grade is claimed.

## Usage walkthrough: Windows PowerShell

1. Confirm Python and Git are installed:

   ```powershell
   python --version
   git --version
   ```

2. Clone the auditor into a convenient local directory. The final argument gives the clone a
   simple local folder name even though the GitHub repository name starts with a hyphen:

   ```powershell
   git clone https://github.com/mehranwazir/-devops-repository-auditing-agent.git devops-auditor
   cd devops-auditor
   ```

   If this clone already exists, enter it and update it instead:

   ```powershell
   cd devops-auditor
   git pull --ff-only origin main
   ```

3. Create the environment and install the auditor. Calling its Python directly avoids
   PowerShell activation-policy issues:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

4. Configure your own Gemini key locally. A fresh clone does not include any API credential:

   ```powershell
   if (!(Test-Path .env)) { Copy-Item .env.example .env }
   notepad .env
   ```

   Save `GEMINI_API_KEY=your_actual_key` and `GEMINI_MODEL=gemini-3.5-flash-lite`. Keep the
   variable names exactly as shown. Replace any previously exposed credential before reuse.

5. Inspect a real public repository. The auditor fetches it automatically; do not manually
   clone or install the target:

   ```powershell
   New-Item -ItemType Directory -Force output | Out-Null
   .\.venv\Scripts\python.exe agent.py --repo https://github.com/pallets/markupsafe --output output/markupsafe_report.json --trace output/markupsafe_trace.jsonl
   if ($LASTEXITCODE -ne 0) { throw "Audit failed; do not present an older report as a new result." }
   ```

6. Locate and display the report:

   ```powershell
   Resolve-Path output/markupsafe_report.json
   Get-Content output/markupsafe_report.json
   $report = Get-Content output/markupsafe_report.json -Raw | ConvertFrom-Json
   $report.violations | Format-List rule_id, category, severity, file, line, description, evidence, recommendation
   ```

   The report is generated **on your computer under the auditor clone's `output` directory**.
   For example, cloning into `D:\MLOPS\devops-auditor` produces
   `D:\MLOPS\devops-auditor\output\markupsafe_report.json`. The trace is beside it.
   It is not generated in the inspected repository or automatically uploaded to GitHub.
   Without `output/` in `--output`, a relative filename is written in your current directory.

7. Inspect tool calls and the final coverage assessment:

   ```powershell
   $trace = Get-Content output/markupsafe_trace.jsonl | ForEach-Object { $_ | ConvertFrom-Json }
   $trace | Where-Object event -eq 'tool_call' | Select-Object turn, name, arguments | Format-Table -Wrap
   ($trace | Where-Object event -eq 'complete').assessment | Format-Table area, status, file, explanation -Wrap
   ```

   The LLM selects tools, reads their returned observations, and chooses follow-up calls.
   Findings include evidence, severity, and remediation. The host validates DTOs and observed
   citations before saving JSON. The target's code never executes.

8. Check positive detection using the controlled live exercise if the public repository has no
   findings. An empty array is a valid outcome, not an audit failure:

   ```powershell
   .\.venv\Scripts\python.exe evaluate.py --case obvious --output-dir output/evaluation
   Get-Content output/evaluation/obvious_report.json
   ```

   This is a local fixture with injected defects, separate from the public repository audit.
   Compare each recommendation with the cited source and expected defect.

9. Run the auditor's own offline checks (these do not run the target's tests):

   ```powershell
   .\.venv\Scripts\python.exe -m pip install ".[dev]"
   .\.venv\Scripts\python.exe -m pytest
   .\.venv\Scripts\python.exe -m ruff check .
   ```

## Public repositories to try

| Repository | Inspection focus |
| --- | --- |
| [pallets/markupsafe](https://github.com/pallets/markupsafe) | First run; Python packaging, tests, and CI; the checked-in sample uses this project |
| [pallets/click](https://github.com/pallets/click) | Python CLI project with more source, tests, and workflow context |
| [psf/requests](https://github.com/psf/requests) | Another established Python project for dependency and CI investigation |
| [This auditor](https://github.com/mehranwazir/-devops-repository-auditing-agent) | Explain and inspect your own architecture, tests, DTOs, and CI |

These public repositories were verified to exist during the review. They are investigation
targets, not promises of known defects. Maintained repositories may correctly return zero
findings. Results depend on the inspected commit, available tools, model, and evidence.

Example second run, keeping its output separate:

```powershell
.\.venv\Scripts\python.exe agent.py --repo https://github.com/pallets/click --output output/click_report.json --trace output/click_trace.jsonl
```

## Troubleshooting

| Error | Resolution |
| --- | --- |
| Missing `GEMINI_API_KEY` | Set it in the invocation directory's `.env` or environment |
| HTTP 401/403 | Check the key, API restrictions, account/region and project access |
| HTTP 404 | Select a model available to your key using `--model` or `GEMINI_MODEL` |
| HTTP 429 | Wait for quota reset; check free-tier limits; avoid repeated concurrent audits |
| Git operation failed | Confirm the URL is public, Git is installed, and GitHub is reachable |
| Budget exhausted | Increase `--max-turns`/`--max-calls` within the documented maximum |
| Cannot read/write files | Confirm the destination directory exists and permissions allow writes |

## Submission

Submit **only the public GitHub repository URL** through the course submission channel.
All installation, configuration, architecture, execution, validation, and related information
is included in this README. The executable code, tests, `.env.example`, and authentic sample
output belong in the repository; the assignment PDF and local credentials are excluded.

## References

- [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling)
- [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Git plumbing and object storage](https://git-scm.com/book/en/v2/Git-Internals-Plumbing-and-Porcelain)
