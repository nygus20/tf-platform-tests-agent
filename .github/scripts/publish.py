#!/usr/bin/env python3
"""Publish results to the pull request with GITHUB_TOKEN. Idempotent:

  review   one review with an inline comment per finding on a diff line,
           skipping findings already posted (matched by a hidden marker)
  comment  create or update the single sticky summary comment
  check    create the `terraform-test` Check Run on a commit

Needs GITHUB_TOKEN, GITHUB_REPOSITORY and optionally GITHUB_API_URL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (CHECK_NAME, FINDING_MARKER, SUMMARY_MARKER, load_json, parse_patch,  # noqa: E402
                    safe_text, write_json)

BOT_LOGIN = "github-actions[bot]"
MAX_INLINE = 25
MAX_CHECK_TEXT = 60000


class GitHub:
    def __init__(self) -> None:
        self.token = os.environ["GITHUB_TOKEN"]
        self.repo = os.environ["GITHUB_REPOSITORY"]
        self.api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")

    def request(self, method: str, path: str, body=None):
        url = path if path.startswith("http") else f"{self.api}{path}"
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(4):
            req = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "tf-test-agent-publisher",
                **({"Content-Type": "application/json"} if data else {}),
            })
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    link = resp.headers.get("Link", "")
                    return (json.loads(raw) if raw else None), link
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:500]
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise SystemExit(f"GitHub API {method} {path} failed: {exc.code} {detail}")
        raise SystemExit(f"GitHub API {method} {path} failed after retries")

    def paginate(self, path: str) -> list:
        items: list = []
        url = f"{path}{'&' if '?' in path else '?'}per_page=100"
        while url:
            page, link = self.request("GET", url)
            items.extend(page or [])
            m = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
            url = m.group(1) if m else ""
        return items


def finding_body(f: dict) -> str:
    lines = [
        FINDING_MARKER.format(id=f["id"]),
        f"**{safe_text(f['title'], 120)}**",
        "",
        f"Severity {f['severity']}, {f['category']}, confidence {f['confidence']}. "
        "Found by the Terraform test agent. A person decides whether to change the code.",
        "",
        f"> {safe_text(f['detail'], 800)}",
    ]
    if f.get("suggestion"):
        lines += ["", f"Suggestion: {safe_text(f['suggestion'], 500)}"]
    return "\n".join(lines)


def cmd_review(gh: GitHub, args) -> int:
    report = load_json(args.report, {}) or {}
    findings = report.get("findings", [])
    patch = Path(args.patch).read_text(encoding="utf-8") if args.patch and Path(args.patch).is_file() else ""
    commentable = parse_patch(patch)
    existing = gh.paginate(f"/repos/{gh.repo}/pulls/{args.pr}/comments")
    posted_ids = set()
    for c in existing:
        if (c.get("user") or {}).get("login") != BOT_LOGIN:
            continue
        for m in re.finditer(r"terraform-tests:finding:([a-z0-9/-]+)", c.get("body", "")):
            posted_ids.add(m.group(1))

    comments, inline, already, outside = [], [], [], []
    for f in findings:
        if f["id"] in posted_ids:
            already.append(f["id"])
            continue
        lines = commentable.get(f["file"], set())
        end = f.get("end_line", f["line"])
        if f["line"] in lines and end in lines and len(comments) < MAX_INLINE:
            c = {"path": f["file"], "line": end, "side": "RIGHT", "body": finding_body(f)}
            if end != f["line"]:
                c.update(start_line=f["line"], start_side="RIGHT")
            comments.append(c)
            inline.append(f["id"])
        else:
            outside.append(f["id"])

    if comments:
        gh.request("POST", f"/repos/{gh.repo}/pulls/{args.pr}/reviews", {
            "commit_id": args.commit,
            "event": "COMMENT",
            "body": f"Terraform test agent: {len(comments)} new finding(s) on changed lines. "
                    "See the summary comment for the full list.",
            "comments": comments,
        })
    write_json(args.out, {"inline": inline, "already": already, "outside": outside})
    print(f"review: {len(inline)} new inline, {len(already)} already posted, {len(outside)} outside the diff")
    return 0


def cmd_comment(gh: GitHub, args) -> int:
    body = Path(args.body).read_text(encoding="utf-8")
    if len(body) > 60000:
        body = body[:59000] + "\n\n(truncated, see the workflow run)\n"
    for c in gh.paginate(f"/repos/{gh.repo}/issues/{args.pr}/comments"):
        if (c.get("user") or {}).get("login") == BOT_LOGIN and SUMMARY_MARKER in c.get("body", ""):
            gh.request("PATCH", f"/repos/{gh.repo}/issues/comments/{c['id']}", {"body": body})
            print(f"updated summary comment {c['id']}")
            return 0
    created, _ = gh.request("POST", f"/repos/{gh.repo}/issues/{args.pr}/comments", {"body": body})
    print(f"created summary comment {created['id']}")
    return 0


def cmd_check(gh: GitHub, args) -> int:
    results = load_json(args.results, {}) or {}
    guard = load_json(args.guard, None)
    overall = results.get("overall", "error")
    conclusion = "success" if overall == "pass" else "failure"
    title = f"terraform test: {overall}"
    if guard is not None and not guard.get("ok", False):
        conclusion, title = "failure", "guard rejected the agent output"
    text = Path(args.summary).read_text(encoding="utf-8") if args.summary else ""
    gh.request("POST", f"/repos/{gh.repo}/check-runs", {
        "name": CHECK_NAME,
        "head_sha": args.sha,
        "status": "completed",
        "conclusion": conclusion,
        "details_url": args.details_url or None,
        "output": {"title": title, "summary": text[:MAX_CHECK_TEXT]},
    })
    print(f"check run {CHECK_NAME} on {args.sha[:7]}: {conclusion}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review")
    r.add_argument("--pr", required=True, type=int)
    r.add_argument("--report", required=True)
    r.add_argument("--patch", required=True)
    r.add_argument("--commit", required=True)
    r.add_argument("--out", required=True)
    c = sub.add_parser("comment")
    c.add_argument("--pr", required=True, type=int)
    c.add_argument("--body", required=True)
    k = sub.add_parser("check")
    k.add_argument("--sha", required=True)
    k.add_argument("--results", required=True)
    k.add_argument("--guard")
    k.add_argument("--summary")
    k.add_argument("--details-url", default="")
    args = ap.parse_args()
    gh = GitHub()
    return {"review": cmd_review, "comment": cmd_comment, "check": cmd_check}[args.cmd](gh, args)


if __name__ == "__main__":
    sys.exit(main())
