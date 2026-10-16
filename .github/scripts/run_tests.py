#!/usr/bin/env python3
"""Run `terraform test` for every module in scope and `terraform validate` for
every stack in scope. This script, not the agent, decides pass or fail.

Accepted agent files are overlaid onto the PR checkout first and formatted with
`terraform fmt` (parsing only, no code runs). The formatted copies are written
to --formatted-dir so the publish job commits exactly what was tested.

Exit code: 0 when overall is pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import gh_output, load_json, write_json  # noqa: E402

STEP_TIMEOUT_S = 300


def run(cmd: list[str], cwd: Path, timeout: int = STEP_TIMEOUT_S) -> subprocess.CompletedProcess:
    env = dict(os.environ, TF_IN_AUTOMATION="1", TF_INPUT="0", CHECKPOINT_DISABLE="1")
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(cmd, 124, exc.stdout or "", f"timed out after {timeout}s")


def first_error(text: str, limit: int = 400) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if ln.startswith("Error:"):
            return " ".join(lines[i:i + 3])[:limit]
    return (lines[-1] if lines else "")[:limit]


def norm(path: str) -> str:
    return path.replace("\\", "/")


def parse_test_json(stdout: str) -> tuple[list[dict], list[str], dict | None]:
    """Turn `terraform test -json` lines into run results, file-level messages and the summary."""
    runs: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    messages: list[str] = []
    summary = None
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        kind = ev.get("type")
        if kind == "test_abstract":
            for file, names in (ev.get("test_abstract") or {}).items():
                for name in names:
                    key = (norm(file), name)
                    if key not in runs:
                        runs[key] = {"file": norm(file), "name": name, "status": "skip", "message": ""}
                        order.append(key)
        elif kind == "test_run":
            tr = ev.get("test_run") or {}
            if tr.get("progress") != "complete":
                continue
            key = (norm(tr.get("path", "")), tr.get("run", ""))
            if key not in runs:
                runs[key] = {"file": key[0], "name": key[1], "status": "skip", "message": ""}
                order.append(key)
            runs[key]["status"] = tr.get("status", "error")
        elif kind == "diagnostic" and ev.get("@level") == "error":
            d = ev.get("diagnostic") or {}
            text = f"{d.get('summary', '')}: {d.get('detail', '')}".strip(": ")
            rng = d.get("range") or {}
            if rng.get("filename"):
                text += f" ({norm(rng['filename'])}:{(rng.get('start') or {}).get('line', '?')})"
            run_name = ev.get("@testrun")
            file = norm(ev.get("@testfile", ""))
            if run_name and (file, run_name) in runs:
                r = runs[(file, run_name)]
                r["message"] = (r["message"] + " | " if r["message"] else "") + text[:400]
            else:
                messages.append(text[:400])
        elif kind == "test_summary":
            summary = ev.get("test_summary")
    return [runs[k] for k in order], messages, summary


def test_module(root: Path, module: str, tf: str, junit_dir: Path | None) -> dict:
    mdir = root / module
    started = time.monotonic()
    res = {"path": module, "status": "no-tests", "fmt_ok": True, "init_ok": False,
           "runs": [], "messages": [], "duration_s": 0.0}
    tests = sorted((mdir / "tests").glob("*.tftest.hcl")) + sorted(mdir.glob("*.tftest.hcl"))
    fmt = run([tf, "fmt", "-check", "-recursive", "-no-color"], mdir)
    res["fmt_ok"] = fmt.returncode == 0
    if not res["fmt_ok"]:
        unformatted = ", ".join(norm(x) for x in fmt.stdout.split())
        res["messages"].append(f"terraform fmt would change: {unformatted}"[:400])
    if not tests:
        res["duration_s"] = round(time.monotonic() - started, 1)
        return res
    init = run([tf, "init", "-backend=false", "-lockfile=readonly", "-input=false", "-no-color"], mdir)
    res["init_ok"] = init.returncode == 0
    if not res["init_ok"]:
        res["status"] = "error"
        res["messages"].append("terraform init failed: " + first_error(init.stdout + init.stderr))
        res["duration_s"] = round(time.monotonic() - started, 1)
        return res
    cmd = [tf, "test", "-json", "-no-color"]
    if junit_dir is not None:
        junit_dir.mkdir(parents=True, exist_ok=True)
        cmd.append(f"-junit-xml={(junit_dir / (module.replace('/', '-') + '.xml')).resolve()}")
    proc = run(cmd, mdir)
    runs, messages, summary = parse_test_json(proc.stdout)
    res["runs"] = runs
    res["messages"].extend(messages)
    if proc.returncode == 124:
        res["messages"].append(proc.stderr)
    statuses = {r["status"] for r in runs}
    executed = [r for r in runs if r["status"] in ("pass", "fail", "error")]
    if proc.returncode not in (0, 1) or not executed or "error" in statuses or (
        proc.returncode == 1 and not ({"fail"} & statuses)
    ):
        res["status"] = "error"
        if not executed and not res["messages"]:
            res["messages"].append("no run block was executed; check the test files")
    elif "fail" in statuses:
        res["status"] = "fail"
    elif proc.returncode == 0 and summary and summary.get("status") == "pass":
        res["status"] = "pass"
    else:
        res["status"] = "error"
        res["messages"].append(f"unexpected terraform exit code {proc.returncode}")
    res["duration_s"] = round(time.monotonic() - started, 1)
    return res


def validate_stack(root: Path, stack: str, tf: str) -> dict:
    sdir = root / stack
    res = {"path": stack, "validate_ok": False, "messages": []}
    init = run([tf, "init", "-backend=false", "-lockfile=readonly", "-input=false", "-no-color"], sdir)
    if init.returncode != 0:
        res["messages"].append("terraform init failed: " + first_error(init.stdout + init.stderr))
        return res
    val = run([tf, "validate", "-json", "-no-color"], sdir)
    try:
        data = json.loads(val.stdout)
        res["validate_ok"] = bool(data.get("valid"))
        for d in data.get("diagnostics", []):
            if d.get("severity") == "error":
                res["messages"].append(f"{d.get('summary', '')}: {d.get('detail', '')}"[:400])
    except ValueError:
        res["messages"].append("terraform validate failed: " + first_error(val.stdout + val.stderr))
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="checkout of the PR head (modified in place)")
    ap.add_argument("--changes", required=True)
    ap.add_argument("--overlay", help="guard's accepted/ directory")
    ap.add_argument("--terraform", default="terraform")
    ap.add_argument("--out", required=True)
    ap.add_argument("--junit-dir")
    ap.add_argument("--formatted-dir")
    args = ap.parse_args()

    root = Path(args.root)
    changes = load_json(args.changes)
    tf = shutil.which(args.terraform) or args.terraform

    applied: list[str] = []
    if args.overlay and Path(args.overlay).is_dir():
        overlay = Path(args.overlay)
        for src in sorted(p for p in overlay.rglob("*.tftest.hcl") if p.is_file()):
            rel = src.relative_to(overlay).as_posix()
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            run([tf, "fmt", "-no-color", str(dst)], root)
            applied.append(rel)
            if args.formatted_dir:
                out = Path(args.formatted_dir) / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(dst, out)

    version = run([tf, "version", "-json"], root)
    try:
        tf_version = json.loads(version.stdout).get("terraform_version", "unknown")
    except ValueError:
        tf_version = "unknown"

    junit = Path(args.junit_dir) if args.junit_dir else None
    modules = [test_module(root, m, tf, junit) for m in changes["modules"]]
    stacks = [validate_stack(root, s, tf) for s in changes["stacks"]]

    if any(m["status"] == "error" for m in modules):
        overall = "error"
    elif any(m["status"] == "fail" for m in modules) or any(not s["validate_ok"] for s in stacks):
        overall = "fail"
    else:
        overall = "pass"

    results = {
        "schema_version": 1,
        "terraform_version": tf_version,
        "head_sha": changes.get("head_sha") or "",
        "overall": overall,
        "applied_files": applied,
        "modules": modules,
        "stacks": stacks,
    }
    write_json(args.out, results)
    gh_output("overall", overall)
    for m in modules:
        counts = {s: sum(1 for r in m["runs"] if r["status"] == s) for s in ("pass", "fail", "error", "skip")}
        print(f"{m['path']}: {m['status']} {counts} fmt_ok={m['fmt_ok']}")
        for msg in m["messages"]:
            print(f"  {msg}")
        for r in m["runs"]:
            if r["status"] in ("fail", "error"):
                print(f"::error title={m['path']} {r['name']}::{r['message'] or r['status']}")
    for s in stacks:
        print(f"{s['path']}: validate_ok={s['validate_ok']} {s['messages']}")
    print(f"overall: {overall}")
    return 0 if overall == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
