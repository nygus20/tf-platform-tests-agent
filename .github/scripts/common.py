"""Helpers shared by the workflow scripts. Standard library only."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

MODULE_DIR_RE = re.compile(r"^modules/([a-z0-9][a-z0-9-]*)/")
STACK_DIR_RE = re.compile(r"^stacks/([a-z0-9][a-z0-9-]*)/")
TEST_FILE_RE = re.compile(
    r"^modules/[a-z0-9][a-z0-9-]*/tests/[A-Za-z0-9][A-Za-z0-9_-]*\.tftest\.hcl$"
)
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

SUMMARY_MARKER = "<!-- terraform-tests:summary -->"
FINDING_MARKER = "<!-- terraform-tests:finding:{id} -->"
CHECK_NAME = "terraform-test"
AGENT_TRAILER = "Tf-Test-Agent:"


def load_json(path: str | Path | None, default: Any = None) -> Any:
    if not path:
        return default
    p = Path(path)
    if not p.is_file():
        return default
    with p.open(encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, sort_keys=False)
        fh.write("\n")


def module_of(path: str) -> str | None:
    m = MODULE_DIR_RE.match(path)
    return f"modules/{m.group(1)}" if m else None


def stack_of(path: str) -> str | None:
    m = STACK_DIR_RE.match(path)
    return f"stacks/{m.group(1)}" if m else None


def parse_patch(patch_text: str) -> dict[str, set[int]]:
    """Map each file in a unified diff to the new-side line numbers GitHub accepts
    for review comments (added and context lines inside hunks)."""
    lines_by_file: dict[str, set[int]] = {}
    current: set[int] | None = None
    new_line = 0
    in_hunk = False
    for raw in patch_text.splitlines():
        if raw.startswith("diff --git "):
            current = None
            in_hunk = False
            continue
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            if target == "/dev/null":
                current = None
            else:
                name = target[2:] if target.startswith("b/") else target
                current = lines_by_file.setdefault(name, set())
            in_hunk = False
            continue
        if raw.startswith("--- "):
            in_hunk = False
            continue
        m = HUNK_RE.match(raw)
        if m:
            new_line = int(m.group(1))
            in_hunk = True
            continue
        if not in_hunk or current is None:
            continue
        if raw.startswith("\\"):
            continue
        tag = raw[:1]
        if tag == "+":
            current.add(new_line)
            new_line += 1
        elif tag == " " or raw == "":
            current.add(new_line)
            new_line += 1
        elif tag == "-":
            pass
    return lines_by_file


def gh_output(name: str, value: str) -> None:
    """Write a step output when running in GitHub Actions, print otherwise."""
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")
    else:
        print(f"[output] {name}={value}")


def step_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown)
            fh.write("\n")


_MENTION_RE = re.compile(r"@(?=[A-Za-z0-9_-])")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def safe_text(text: Any, limit: int = 600) -> str:
    """Neutralise model-written text before it lands in a GitHub comment:
    no HTML, no links or images, no mentions, no code fences, capped length."""
    s = "" if text is None else str(text)
    s = _CTRL_RE.sub("", s)
    s = s.replace("\r", "")
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for ch in "[]()`*_|#!":
        s = s.replace(ch, "\\" + ch)
    s = _MENTION_RE.sub("@​", s)
    s = " ".join(s.split())
    if len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s
