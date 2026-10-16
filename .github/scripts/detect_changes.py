#!/usr/bin/env python3
"""Work out which Terraform modules and stacks a pull request touches and
whether the agent may run. Writes changes.json and changes.patch.

CI:    detect_changes.py --files files.json --event "$GITHUB_EVENT_PATH" \
           --head-commit commit.json --root head --out-dir out
Local: detect_changes.py --git-base origin/main --root . --out-dir out
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import AGENT_TRAILER, gh_output, load_json, module_of, stack_of, write_json  # noqa: E402

SKIP_LABEL = "tf-agent:skip"
BACKFILL_LABEL = "tf-agent:backfill"
MAX_PATCH_BYTES = 300_000
MAX_FILE_PATCH_BYTES = 60_000


def flatten_api_files(data) -> list[dict]:
    """`gh api --paginate --slurp` yields a list of pages; plain calls yield one list."""
    if data and isinstance(data, list) and all(isinstance(x, list) for x in data):
        return [f for page in data for f in page]
    return list(data or [])


def files_from_git(base: str, root: Path) -> list[dict]:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
            encoding="utf-8",
        ).stdout

    status_map = {"A": "added", "D": "removed", "M": "modified", "T": "modified"}
    out = []
    raw = git("diff", "--name-status", "--no-renames", "-z", f"{base}...HEAD")
    parts = [p for p in raw.split("\0") if p]
    for status, name in zip(parts[0::2], parts[1::2]):
        patch = git("diff", "--no-renames", "--no-color", f"{base}...HEAD", "--", name)
        body = patch.split("\n@@", 1)
        hunks = "@@" + body[1] if len(body) == 2 else ""
        out.append({"filename": name, "status": status_map.get(status[0], "modified"), "patch": hunks})
    return out


def build_patch(files: list[dict]) -> tuple[str, bool]:
    chunks: list[str] = []
    size = 0
    truncated = False
    for f in files:
        name = f["filename"]
        prev = f.get("previous_filename") or name
        status = f.get("status", "modified")
        old = "/dev/null" if status == "added" else f"a/{prev}"
        new = "/dev/null" if status == "removed" else f"b/{name}"
        patch = f.get("patch") or ""
        if not patch:
            patch = "(no textual diff available: binary or too large)"
        if len(patch.encode("utf-8")) > MAX_FILE_PATCH_BYTES:
            patch = patch.encode("utf-8")[:MAX_FILE_PATCH_BYTES].decode("utf-8", "ignore")
            patch += "\n(diff truncated, read the file instead)"
            truncated = True
        chunk = f"diff --git a/{prev} b/{name}\n--- {old}\n+++ {new}\n{patch.rstrip()}\n"
        if size + len(chunk) > MAX_PATCH_BYTES:
            truncated = True
            chunks.append(f"diff --git a/{prev} b/{name}\n(omitted: patch size limit reached)\n")
            continue
        chunks.append(chunk)
        size += len(chunk)
    return "".join(chunks), truncated


def list_dirs(root: Path, parent: str) -> list[str]:
    base = root / parent
    if not base.is_dir():
        return []
    return sorted(f"{parent}/{p.name}" for p in base.iterdir() if p.is_dir() and (p / "main.tf").is_file())


def main() -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--files", help="PR files JSON from the GitHub API")
    src.add_argument("--git-base", help="local mode: diff base...HEAD")
    ap.add_argument("--event", help="GitHub event payload (pull_request)")
    ap.add_argument("--head-commit", help="JSON with the head commit message: {\"message\": ...}")
    ap.add_argument("--root", required=True, help="checkout of the PR head")
    ap.add_argument("--backfill", action="store_true", help="local mode: consider every module")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    root = Path(args.root)
    event = load_json(args.event, {}) or {}
    pr = event.get("pull_request") or {}
    labels = sorted(lbl.get("name", "") for lbl in pr.get("labels", []))

    if args.files:
        files = flatten_api_files(load_json(args.files, []))
    else:
        files = files_from_git(args.git_base, root)

    touched_modules: set[str] = set()
    touched_stacks: set[str] = set()
    relevant: list[dict] = []
    for f in files:
        names = [f["filename"]] + ([f["previous_filename"]] if f.get("previous_filename") else [])
        hit = False
        for name in names:
            if m := module_of(name):
                touched_modules.add(m)
                hit = True
            if s := stack_of(name):
                touched_stacks.add(s)
                hit = True
        if hit:
            relevant.append(f)

    existing_modules = list_dirs(root, "modules")
    existing_stacks = list_dirs(root, "stacks")
    backfill = args.backfill or BACKFILL_LABEL in labels
    if backfill:
        modules = existing_modules
    else:
        modules = sorted(m for m in touched_modules if m in existing_modules)
    stacks = sorted(s for s in touched_stacks if s in existing_stacks)
    removed = sorted((touched_modules | touched_stacks) - set(existing_modules) - set(existing_stacks))

    patch, truncated = build_patch(relevant)
    has_changes = bool(modules or stacks)

    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    base_repo = ((pr.get("base") or {}).get("repo") or {}).get("full_name")
    same_repo = True if not pr else (head_repo is not None and head_repo == base_repo)
    actor = (event.get("sender") or {}).get("login", "")
    head_msg = (load_json(args.head_commit, {}) or {}).get("message", "")

    reason = ""
    if not has_changes:
        reason = "no module or stack changes"
    elif SKIP_LABEL in labels:
        reason = f"label {SKIP_LABEL} is set"
    elif not same_repo:
        reason = "pull request from a fork: secrets are not available"
    elif actor == "dependabot[bot]":
        reason = "Dependabot pull request"
    elif event.get("action") == "synchronize" and AGENT_TRAILER in head_msg:
        reason = "head commit was written by the agent"
    eligible = reason == ""

    changes = {
        "schema_version": 1,
        "pr": pr.get("number"),
        "head_sha": (pr.get("head") or {}).get("sha"),
        "base_sha": (pr.get("base") or {}).get("sha"),
        "mode": "backfill" if backfill else "changed",
        "modules": modules,
        "stacks": stacks,
        "removed": removed,
        "files": [{"path": f["filename"], "status": f.get("status", "modified")} for f in relevant],
        "patch_truncated": truncated,
        "labels": labels,
    }
    out = Path(args.out_dir)
    write_json(out / "changes.json", changes)
    (out / "changes.patch").write_text(patch, encoding="utf-8", newline="\n")

    gh_output("has_changes", str(has_changes).lower())
    gh_output("agent_eligible", str(eligible).lower())
    gh_output("agent_skip_reason", reason)
    gh_output("same_repo", str(same_repo).lower())
    gh_output("modules", json.dumps(modules))
    print(json.dumps({"modules": modules, "stacks": stacks, "mode": changes["mode"],
                      "agent_eligible": eligible, "reason": reason}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
