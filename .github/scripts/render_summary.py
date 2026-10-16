#!/usr/bin/env python3
"""Render the sticky PR comment (and the job summary) from JSON files.

Nothing here is written by the model. Model-written strings from the report are
escaped with safe_text() and shown as quoted, length-capped data.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SUMMARY_MARKER, load_json, safe_text, step_summary  # noqa: E402

VERDICT = {"pass": "PASS", "fail": "FAIL", "error": "ERROR"}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def blob_link(repo: str, sha: str, file: str, line: int, end: int | None = None) -> str:
    anchor = f"#L{line}" + (f"-L{end}" if end and end != line else "")
    return f"[`{file}:{line}`](https://github.com/{repo}/blob/{sha}/{file}{anchor})"


def render(results, guard, report, changes, review, *, repo: str, sha: str, run_url: str,
           commit: str | None, skip_reason: str) -> str:
    out: list[str] = [SUMMARY_MARKER]
    overall = (results or {}).get("overall", "error")
    out.append(f"## Terraform tests: {VERDICT.get(overall, overall.upper())}")
    out.append("")
    tested = (results or {}).get("head_sha") or sha
    line = (f"Terraform {(results or {}).get('terraform_version', '?')} ran `terraform test` "
            f"on `{tested[:7]}`")
    if commit:
        line += f" plus the agent's test files, committed as `{commit[:7]}`"
    out.append(line + ". The verdict comes from that run, not from the agent.")
    out.append("")

    decisions = {m["path"]: m for m in (report or {}).get("modules", [])}
    if results and results.get("modules"):
        out.append("| Module | Result | Runs (pass / fail / error / skip) | Agent decision |")
        out.append("|---|---|---|---|")
        for m in results["modules"]:
            c = {s: sum(1 for r in m["runs"] if r["status"] == s) for s in ("pass", "fail", "error", "skip")}
            d = decisions.get(m["path"])
            dec = d["decision"] if d else "n/a"
            fmt = "" if m["fmt_ok"] else " (fmt)"
            out.append(f"| `{m['path']}` | {m['status'].upper()}{fmt} | "
                       f"{c['pass']} / {c['fail']} / {c['error']} / {c['skip']} | {dec} |")
        out.append("")
    for s in (results or {}).get("stacks", []):
        state = "valid" if s["validate_ok"] else "INVALID"
        out.append(f"Stack `{s['path']}`: `terraform validate` {state}.")
        for msg in s["messages"][:5]:
            out.append(f"> {safe_text(msg, 300)}")
        out.append("")

    problems = []
    for m in (results or {}).get("modules", []):
        for msg in m["messages"]:
            problems.append(f"- `{m['path']}`: {safe_text(msg, 400)}")
        for r in m["runs"]:
            if r["status"] in ("fail", "error"):
                problems.append(f"- `{m['path']}` run `{r['name']}` {r['status']}: {safe_text(r['message'], 400)}")
    if problems:
        out.append("<details open><summary>Failures and messages</summary>")
        out.append("")
        out.extend(problems[:40])
        out.append("")
        out.append("</details>")
        out.append("")

    findings = sorted((report or {}).get("findings", []),
                      key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["file"], f["line"]))
    inline = set((review or {}).get("inline", [])) | set((review or {}).get("already", []))
    if findings:
        out.append(f"### Review findings ({len(findings)})")
        out.append("")
        out.append("Reported for a human decision. The agent never changes infrastructure code.")
        out.append("")
        out.append("| Severity | Where | Finding | Inline |")
        out.append("|---|---|---|---|")
        for f in findings:
            where = blob_link(repo, sha, f["file"], f["line"], f.get("end_line"))
            if review is None:
                mark = "not posted"
            else:
                mark = "yes" if f["id"] in inline else "no, outside the diff"
            out.append(f"| {f['severity']} | {where} | {safe_text(f['title'], 120)} | {mark} |")
        out.append("")
        outside = [f for f in findings if f["id"] not in inline]
        if outside:
            label = "Finding details" if review is None else "Findings outside the diff"
            out.append(f"<details><summary>{label}</summary>")
            out.append("")
            for f in outside:
                out.append(f"- **{safe_text(f['title'], 120)}** ({f['severity']}, {f['category']}, "
                           f"confidence {f['confidence']}) {blob_link(repo, sha, f['file'], f['line'])}")
                out.append(f"  > {safe_text(f['detail'], 800)}")
                if f.get("suggestion"):
                    out.append(f"  > Suggestion: {safe_text(f['suggestion'], 500)}")
            out.append("")
            out.append("</details>")
            out.append("")

    agent = (guard or {}).get("agent") or {}
    out.append("### Agent")
    out.append("")
    if skip_reason:
        out.append(f"The agent did not run: {safe_text(skip_reason, 200)}.")
    elif not guard:
        out.append("The agent ran but produced no output the guard could read.")
    else:
        status = agent.get("status", "missing")
        cost = agent.get("total_cost_usd")
        dur = agent.get("duration_ms")
        bits = [f"status `{status}`"]
        if agent.get("num_turns") is not None:
            bits.append(f"{agent['num_turns']} turns")
        if dur is not None:
            bits.append(f"{dur / 1000:.0f} s")
        if cost is not None:
            bits.append(f"${cost:.2f}")
        bits.append(f"{agent.get('permission_denials', 0)} denied tool calls")
        out.append("Run: " + ", ".join(bits) + ".")
        ev = agent.get("evidence") or {}
        if ev.get("skills"):
            out.append("Skills invoked (from the transcript): " + ", ".join(f"`{s}`" for s in ev["skills"]) + ".")
        if ev.get("subagents"):
            out.append("Subagents: " + ", ".join(f"`{s}`" for s in ev["subagents"]) + ".")
        if agent.get("models"):
            out.append("Models: " + ", ".join(f"`{m}`" for m in agent["models"]) + ".")
        out.append("")
        if guard.get("ok"):
            n = len(guard.get("accepted_files", []))
            out.append(f"Guard: passed. {n} test file(s) accepted.")
        else:
            out.append("Guard: **rejected the agent's output.** Nothing it wrote was tested or committed.")
            for v in guard.get("violations", [])[:20]:
                out.append(f"- {safe_text(v, 300)}")
        for d in guard.get("dropped_findings", [])[:20]:
            out.append(f"- Dropped finding `{safe_text(d['id'], 100)}`: {safe_text(d['why'], 200)}")
        out.append("")
        if decisions:
            out.append("<details><summary>Agent decisions (model-written text, shown as data)</summary>")
            out.append("")
            for path, d in sorted(decisions.items()):
                out.append(f"- `{path}` {d['decision']}: {safe_text(d['reason'], 600)}")
            for note in (report or {}).get("notes", []):
                out.append(f"- Note: {safe_text(note, 300)}")
            out.append("")
            out.append("</details>")
            out.append("")

    footer = "Mocked plan tests prove module logic, not that Azure accepts the configuration."
    if run_url:
        footer += f" [Workflow run]({run_url})."
    out.append(f"<sub>{footer}</sub>")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results")
    ap.add_argument("--guard")
    ap.add_argument("--report")
    ap.add_argument("--changes")
    ap.add_argument("--review")
    ap.add_argument("--sha", default="")
    ap.add_argument("--commit", default="")
    ap.add_argument("--skip-reason", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--step-summary", action="store_true")
    args = ap.parse_args()

    repo = os.environ.get("GITHUB_REPOSITORY", "owner/repo")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_id = os.environ.get("GITHUB_RUN_ID")
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if run_id else ""
    body = render(load_json(args.results), load_json(args.guard), load_json(args.report),
                  load_json(args.changes), load_json(args.review), repo=repo,
                  sha=args.commit or args.sha, run_url=run_url, commit=args.commit or None,
                  skip_reason=args.skip_reason)
    Path(args.out).write_text(body, encoding="utf-8", newline="\n")
    if args.step_summary:
        step_summary(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
