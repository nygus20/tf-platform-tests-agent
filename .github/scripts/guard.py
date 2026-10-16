#!/usr/bin/env python3
"""Validate what the agent produced before anything downstream trusts it.

Inputs (from the agent job artifact):
  <agent-dir>/changes.tsv     `git diff --cached --name-status --no-renames` of the work tree,
                              excluding .agent/report.json
  <agent-dir>/files/...       copies of every changed file, same relative paths
  <agent-dir>/report.json     the report the agent wrote to .agent/report.json
  <agent-dir>/result.json     final result event of `claude -p --output-format stream-json`
  <agent-dir>/transcript.jsonl  the full stream (optional, used for evidence only)

Rules for files: only added or modified `modules/<m>/tests/<name>.tftest.hcl` for
modules in scope, regular UTF-8 text under 64 KiB, mocked azurerm, no module or
provider blocks. Any violation rejects every file (all or nothing) and fails the step.

Rules for the report: must validate against report.schema.json. Findings whose
file or line do not exist at the PR head are dropped and listed, not fatal.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import TEST_FILE_RE, gh_output, load_json, module_of, write_json  # noqa: E402

MAX_TEST_BYTES = 64 * 1024
FORBIDDEN_PATTERNS = [
    (re.compile(r"^\s*module\s*\{", re.M), "run blocks must test the module under test, not load another module"),
    (re.compile(r"^\s*provider\s+\"", re.M), "test files must not configure real providers"),
    (re.compile(r"^\s*plan_options\s*\{[^}]*refresh\s*=\s*true", re.M | re.S), "refresh against real infrastructure is not allowed"),
]
REQUIRED_PATTERN = re.compile(r"^\s*mock_provider\s+\"azurerm\"", re.M)
EXTERNAL_DATA_RE = re.compile(r"^\s*data\s+\"external\"\s+\"([A-Za-z0-9_-]+)\"", re.M)


def external_data_sources(module_dir: Path) -> list[str]:
    """`data "external"` runs a program at plan time and no mock covers it."""
    names: list[str] = []
    for tf in sorted(module_dir.glob("*.tf")):
        names.extend(EXTERNAL_DATA_RE.findall(tf.read_text(encoding="utf-8", errors="replace")))
    return names


def overrides_target(text: str, name: str) -> bool:
    return re.search(r"target\s*=\s*data\.external\." + re.escape(name) + r"\b", text) is not None


def read_transcript(path: Path) -> dict:
    """Summarise tool use from the stream so the PR shows what the agent actually did."""
    import json

    tools: Counter[str] = Counter()
    skills: list[str] = []
    subagents: list[str] = []
    if not path.is_file():
        return {"tools": {}, "skills": [], "subagents": []}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") != "assistant":
            continue
        for block in (ev.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name", "?")
            tools[name] += 1
            inp = block.get("input") or {}
            if name == "Skill":
                skill = inp.get("skill") or inp.get("command") or inp.get("name")
                if skill and skill not in skills:
                    skills.append(str(skill))
            if name in ("Agent", "Task"):
                sub = inp.get("subagent_type") or inp.get("agent")
                if sub and sub not in subagents:
                    subagents.append(str(sub))
    return {"tools": dict(sorted(tools.items())), "skills": skills, "subagents": subagents}


def check_files(agent_dir: Path, allowed_modules: set[str], head: Path) -> tuple[list[str], list[str]]:
    violations: list[str] = []
    accepted: list[str] = []
    tsv = agent_dir / "changes.tsv"
    if not tsv.is_file():
        return violations, accepted
    for line in tsv.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        status, _, path = line.partition("\t")
        path = path.strip()
        if status not in ("A", "M"):
            violations.append(f"{path}: status {status} is not allowed (only add or modify)")
            continue
        if not TEST_FILE_RE.match(path):
            violations.append(f"{path}: outside the writable scope modules/<module>/tests/<name>.tftest.hcl")
            continue
        if module_of(path) not in allowed_modules:
            violations.append(f"{path}: module is not in scope for this pull request")
            continue
        src = agent_dir / "files" / path
        if src.is_symlink() or not src.is_file():
            violations.append(f"{path}: missing or not a regular file")
            continue
        data = src.read_bytes()
        if len(data) > MAX_TEST_BYTES:
            violations.append(f"{path}: {len(data)} bytes exceeds {MAX_TEST_BYTES}")
            continue
        if b"\x00" in data:
            violations.append(f"{path}: contains NUL bytes")
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            violations.append(f"{path}: not valid UTF-8")
            continue
        if not REQUIRED_PATTERN.search(text):
            violations.append(f'{path}: must declare mock_provider "azurerm"')
        for rx, why in FORBIDDEN_PATTERNS:
            if rx.search(text):
                violations.append(f"{path}: {why}")
        for name in external_data_sources(head / module_of(path)):
            if not overrides_target(text, name):
                violations.append(f"{path}: must override data.external.{name} so no program runs during tests")
        accepted.append(path)
    return violations, accepted


def check_report(report, schema_path: Path, head: Path, allowed_modules: set[str],
                 allowed_stacks: set[str], changed_tests: set[str]) -> tuple[list[str], list[dict], list[dict]]:
    from jsonschema import Draft7Validator

    violations: list[str] = []
    kept: list[dict] = []
    dropped: list[dict] = []
    schema = load_json(schema_path)
    errors = sorted(Draft7Validator(schema).iter_errors(report), key=lambda e: list(e.path))
    for err in errors[:20]:
        loc = "/".join(str(p) for p in err.path) or "(root)"
        violations.append(f"report {loc}: {err.message[:200]}")
    if errors:
        return violations, kept, dropped

    for mod in report["modules"]:
        if mod["path"] not in allowed_modules:
            violations.append(f"report: module {mod['path']} is not in scope")
        for tf in mod["test_files"]:
            if module_of(tf) != mod["path"]:
                violations.append(f"report: {tf} does not belong to {mod['path']}")
            elif tf not in changed_tests and not (head / tf).is_file():
                violations.append(f"report: {tf} is neither written by the agent nor present in the PR")

    seen: set[str] = set()
    scope = {*allowed_modules, *allowed_stacks}
    for f in report["findings"]:
        why = ""
        owner = "/".join(f["file"].split("/")[:2])
        target = head / f["file"]
        if f["id"] in seen:
            why = "duplicate id"
        elif owner not in scope:
            why = f"{owner} is not in scope"
        elif ".." in f["file"].split("/") or not target.is_file():
            why = "file does not exist at the PR head"
        else:
            n = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
            end = f.get("end_line", f["line"])
            if f["line"] > n or end > n or end < f["line"]:
                why = f"line range {f['line']}-{end} is outside the file ({n} lines)"
        if why:
            dropped.append({"id": f["id"], "file": f["file"], "line": f["line"], "why": why})
        else:
            seen.add(f["id"])
            kept.append(f)
    return violations, kept, dropped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--changes", required=True)
    ap.add_argument("--agent-dir", required=True)
    ap.add_argument("--head", required=True, help="checkout of the PR head")
    ap.add_argument("--schema", required=True)
    ap.add_argument("--out-dir", required=True, help="receives guard.json, report.json and accepted/")
    args = ap.parse_args()

    changes = load_json(args.changes)
    agent_dir = Path(args.agent_dir)
    out = Path(args.out_dir)
    head = Path(args.head)
    allowed_modules = set(changes["modules"])
    allowed_stacks = set(changes["stacks"])

    try:
        result = load_json(agent_dir / "result.json")
    except ValueError:
        result = None  # empty or truncated: the CLI was killed or crashed
    agent = {"status": "missing", "num_turns": None, "duration_ms": None, "total_cost_usd": None,
             "permission_denials": 0, "models": []}
    report = None
    if isinstance(result, dict):
        agent.update({
            "status": "success" if result.get("subtype") == "success" and not result.get("is_error") else
                      str(result.get("subtype") or "error"),
            "num_turns": result.get("num_turns"),
            "duration_ms": result.get("duration_ms"),
            "total_cost_usd": result.get("total_cost_usd"),
            "permission_denials": len(result.get("permission_denials") or []),
            "models": sorted((result.get("modelUsage") or {}).keys()),
        })
    # The agent writes .agent/report.json itself. Claude Code 2.1.301 ignores
    # --json-schema when --agent is set (verified), so the schema check lives here.
    report_file = agent_dir / "report.json"
    if report_file.is_file():
        try:
            report = load_json(report_file)
        except ValueError as exc:
            report = {"_unparseable": str(exc)[:200]}
    agent["evidence"] = read_transcript(agent_dir / "transcript.jsonl")

    file_violations, accepted = check_files(agent_dir, allowed_modules, head)
    report_violations: list[str] = []
    findings: list[dict] = []
    dropped: list[dict] = []
    if report is not None:
        report_violations, findings, dropped = check_report(
            report, Path(args.schema), head, allowed_modules, allowed_stacks, set(accepted))
    elif agent["status"] == "success":
        report_violations.append("agent finished without a structured report")

    violations = file_violations + report_violations
    ok = not violations
    accepted_dir = out / "accepted"
    if accepted_dir.exists():
        shutil.rmtree(accepted_dir)
    if ok:
        for path in accepted:
            dst = accepted_dir / path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(agent_dir / "files" / path, dst)
        if report is not None:
            clean = dict(report)
            clean["findings"] = findings
            write_json(out / "report.json", clean)

    guard = {
        "schema_version": 1,
        "ok": ok,
        "accepted_files": accepted if ok else [],
        "violations": violations,
        "dropped_findings": dropped,
        "agent": agent,
    }
    write_json(out / "guard.json", guard)
    gh_output("ok", str(ok).lower())
    gh_output("accepted_count", str(len(accepted) if ok else 0))
    for v in violations:
        print(f"::error title=guard::{v}")
    for d in dropped:
        print(f"::warning title=guard::dropped finding {d['id']}: {d['why']}")
    print(f"guard ok={ok} accepted={len(accepted) if ok else 0} violations={len(violations)} "
          f"dropped_findings={len(dropped)} agent={agent['status']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
