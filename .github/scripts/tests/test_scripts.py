"""Unit tests for the deterministic workflow scripts. Run: python -m pytest .github/scripts/tests"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
import detect_changes  # noqa: E402
import guard  # noqa: E402
import publish  # noqa: E402
import render_summary  # noqa: E402
import run_tests  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA = SCRIPTS.parent / "schemas" / "report.schema.json"

GOOD_TEST = """mock_provider "azurerm" {}

variables {
  name = "x"
}

run "name_is_set" {
  command = plan

  assert {
    condition     = azurerm_log_analytics_workspace.this.name == "x"
    error_message = "The name must come from var.name."
  }
}
"""


# ---------------------------------------------------------------- common

def test_parse_patch_maps_new_side_lines():
    patch = (
        "diff --git a/modules/a/main.tf b/modules/a/main.tf\n"
        "--- a/modules/a/main.tf\n"
        "+++ b/modules/a/main.tf\n"
        "@@ -10,4 +10,5 @@ resource \"x\" \"y\" {\n"
        "   keep = 1\n"
        "-  old = 2\n"
        "+  new = 2\n"
        "+  added = 3\n"
        "   tail = 4\n"
        "diff --git a/modules/a/gone.tf b/modules/a/gone.tf\n"
        "--- a/modules/a/gone.tf\n"
        "+++ /dev/null\n"
        "@@ -1,1 +0,0 @@\n"
        "-x\n"
    )
    lines = common.parse_patch(patch)
    assert lines["modules/a/main.tf"] == {10, 11, 12, 13}
    assert "modules/a/gone.tf" not in lines


def test_safe_text_neutralises_markup_mentions_and_length():
    s = common.safe_text("@octocat see [x](https://evil) <img src=x> `code`\n" + "a" * 1000, limit=80)
    assert "<img" not in s and "&lt;img" in s
    assert "@​octocat" in s
    assert "\\[x\\]\\(https://evil\\)" in s
    assert "\\`code\\`" in s
    assert len(s) == 80


# ---------------------------------------------------------------- detect

def make_tree(root: Path, modules=("network", "key-vault"), stacks=("data-platform",)):
    for m in modules:
        (root / "modules" / m).mkdir(parents=True)
        (root / "modules" / m / "main.tf").write_text("# x\n")
    for s in stacks:
        (root / "stacks" / s).mkdir(parents=True)
        (root / "stacks" / s / "main.tf").write_text("# x\n")


def pr_event(action="opened", labels=(), fork=False, sender="someone"):
    base = {"repo": {"full_name": "o/r"}, "sha": "b" * 40}
    head = {"repo": {"full_name": "fork/r" if fork else "o/r"}, "sha": "h" * 40}
    return {"action": action, "sender": {"login": sender},
            "pull_request": {"number": 7, "labels": [{"name": n} for n in labels], "base": base, "head": head}}


def run_detect(tmp_path, monkeypatch, files, event, head_msg=""):
    root = tmp_path / "head"
    make_tree(root)
    (tmp_path / "files.json").write_text(json.dumps([files]))
    (tmp_path / "event.json").write_text(json.dumps(event))
    (tmp_path / "commit.json").write_text(json.dumps({"message": head_msg}))
    out_file = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    monkeypatch.setattr(sys, "argv", ["detect", "--files", str(tmp_path / "files.json"),
                                      "--event", str(tmp_path / "event.json"),
                                      "--head-commit", str(tmp_path / "commit.json"),
                                      "--root", str(root), "--out-dir", str(tmp_path / "out")])
    assert detect_changes.main() == 0
    outputs = dict(line.split("=", 1) for line in out_file.read_text().splitlines())
    changes = json.loads((tmp_path / "out" / "changes.json").read_text())
    return changes, outputs


FILES = [
    {"filename": "modules/network/main.tf", "status": "modified", "patch": "@@ -1,1 +1,1 @@\n-a\n+b"},
    {"filename": "stacks/data-platform/main.tf", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"},
    {"filename": "README.md", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"},
    {"filename": "modules/deleted-module/main.tf", "status": "removed", "patch": "@@ -1 +0,0 @@\n-a"},
]


def test_detect_maps_files_to_modules_and_stacks(tmp_path, monkeypatch):
    changes, out = run_detect(tmp_path, monkeypatch, FILES, pr_event())
    assert changes["modules"] == ["modules/network"]
    assert changes["stacks"] == ["stacks/data-platform"]
    assert changes["removed"] == ["modules/deleted-module"]
    assert "README.md" not in (tmp_path / "out" / "changes.patch").read_text()
    assert out["agent_eligible"] == "true" and out["has_changes"] == "true"


def test_detect_fork_is_not_eligible(tmp_path, monkeypatch):
    _, out = run_detect(tmp_path, monkeypatch, FILES, pr_event(fork=True))
    assert out["agent_eligible"] == "false" and out["same_repo"] == "false"
    assert "fork" in out["agent_skip_reason"]


def test_detect_skip_label(tmp_path, monkeypatch):
    _, out = run_detect(tmp_path, monkeypatch, FILES, pr_event(labels=["tf-agent:skip"]))
    assert out["agent_eligible"] == "false"


def test_detect_agent_commit_on_synchronize_is_not_eligible(tmp_path, monkeypatch):
    msg = "test(network): add tests\n\nTf-Test-Agent: run 1"
    _, out = run_detect(tmp_path, monkeypatch, FILES, pr_event(action="synchronize"), head_msg=msg)
    assert out["agent_eligible"] == "false"
    # A label added later on the same head is a deliberate request and may run.
    _, out = run_detect(tmp_path / "again", monkeypatch, FILES,
                        pr_event(action="labeled", labels=["tf-agent:backfill"]), head_msg=msg)
    assert out["agent_eligible"] == "true"


def test_detect_backfill_lists_every_module(tmp_path, monkeypatch):
    changes, _ = run_detect(tmp_path, monkeypatch, FILES[2:3], pr_event(labels=["tf-agent:backfill"]))
    assert changes["mode"] == "backfill"
    assert changes["modules"] == ["modules/key-vault", "modules/network"]


# ---------------------------------------------------------------- guard

def agent_dir(tmp_path, entries: dict[str, tuple[str, str]], report=None, subtype="success"):
    d = tmp_path / "agent"
    (d / "files").mkdir(parents=True)
    tsv = []
    for path, (status, content) in entries.items():
        tsv.append(f"{status}\t{path}")
        if status != "D":
            p = d / "files" / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
    (d / "changes.tsv").write_text("\n".join(tsv) + "\n")
    result = {"type": "result", "subtype": subtype, "is_error": subtype != "success", "num_turns": 9,
              "duration_ms": 1000, "total_cost_usd": 0.5, "permission_denials": []}
    if report is not None:
        (d / "report.json").write_text(json.dumps(report))
    (d / "result.json").write_text(json.dumps(result))
    return d


def head_tree(tmp_path):
    h = tmp_path / "head"
    (h / "modules" / "log-analytics-workspace").mkdir(parents=True)
    (h / "modules" / "log-analytics-workspace" / "main.tf").write_text("a\nb\nc\n")
    return h


def report_for(findings=(), modules=None):
    return {
        "schema_version": 1,
        "modules": modules if modules is not None else [{
            "path": "modules/log-analytics-workspace", "decision": "created", "reason": "No tests existed.",
            "test_files": ["modules/log-analytics-workspace/tests/main.tftest.hcl"], "runs": ["name_is_set"]}],
        "findings": list(findings),
        "notes": [],
    }


def finding(line=2, fid="log-analytics-workspace/retention", file="modules/log-analytics-workspace/main.tf"):
    return {"id": fid, "file": file, "line": line, "severity": "low", "category": "cost",
            "confidence": "medium", "title": "t", "detail": "d", "suggestion": "s"}


def run_guard(tmp_path, monkeypatch, d, modules=("modules/log-analytics-workspace",)):
    changes = tmp_path / "changes.json"
    changes.write_text(json.dumps({"modules": list(modules), "stacks": []}))
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(sys, "argv", ["guard", "--changes", str(changes), "--agent-dir", str(d),
                                      "--head", str(head_tree(tmp_path)), "--schema", str(SCHEMA),
                                      "--out-dir", str(tmp_path / "out")])
    code = guard.main()
    return code, json.loads((tmp_path / "out" / "guard.json").read_text())


TEST_PATH = "modules/log-analytics-workspace/tests/main.tftest.hcl"


def test_guard_accepts_a_clean_run(tmp_path, monkeypatch):
    d = agent_dir(tmp_path, {TEST_PATH: ("A", GOOD_TEST)}, report_for([finding()]))
    code, g = run_guard(tmp_path, monkeypatch, d)
    assert code == 0 and g["ok"] and g["accepted_files"] == [TEST_PATH]
    assert (tmp_path / "out" / "accepted" / TEST_PATH).is_file()
    assert json.loads((tmp_path / "out" / "report.json").read_text())["findings"][0]["line"] == 2


@pytest.mark.parametrize("path,status,content,needle", [
    ("modules/log-analytics-workspace/main.tf", "M", "x", "outside the writable scope"),
    (TEST_PATH, "D", "", "status D"),
    ("modules/network/tests/main.tftest.hcl", "A", GOOD_TEST, "not in scope"),
    ("modules/log-analytics-workspace/tests/sub/x.tftest.hcl", "A", GOOD_TEST, "outside the writable scope"),
    (TEST_PATH, "A", GOOD_TEST.replace('mock_provider "azurerm" {}', ""), "mock_provider"),
    (TEST_PATH, "A", GOOD_TEST + '\nrun "x" {\n  module {\n    source = "./evil"\n  }\n}\n', "module"),
    (TEST_PATH, "A", 'provider "azurerm" {\n  features {}\n}\n' + GOOD_TEST, "real providers"),
    (TEST_PATH, "A", GOOD_TEST + "#" * 70000, "exceeds"),
], ids=["edits-main-tf", "deletes-test", "other-module", "nested-dir", "no-mock",
        "module-block", "real-provider", "too-large"])
def test_guard_rejects_out_of_scope_or_unsafe_changes(tmp_path, monkeypatch, path, status, content, needle):
    d = agent_dir(tmp_path, {path: (status, content)}, report_for(modules=[]))
    code, g = run_guard(tmp_path, monkeypatch, d)
    assert code == 1 and not g["ok"] and g["accepted_files"] == []
    assert any(needle in v for v in g["violations"]), g["violations"]
    assert not (tmp_path / "out" / "accepted").exists()


def test_guard_rejects_one_bad_file_and_accepts_none(tmp_path, monkeypatch):
    d = agent_dir(tmp_path, {TEST_PATH: ("A", GOOD_TEST),
                             "modules/log-analytics-workspace/variables.tf": ("M", "x")}, report_for())
    code, g = run_guard(tmp_path, monkeypatch, d)
    assert code == 1 and g["accepted_files"] == []


def test_guard_rejects_schema_invalid_report(tmp_path, monkeypatch):
    bad = report_for()
    bad["modules"][0]["decision"] = "rewrote-main-tf"
    d = agent_dir(tmp_path, {TEST_PATH: ("A", GOOD_TEST)}, bad)
    code, g = run_guard(tmp_path, monkeypatch, d)
    assert code == 1 and any("decision" in v for v in g["violations"])


def test_guard_drops_findings_with_bad_locations(tmp_path, monkeypatch):
    findings = [finding(line=99, fid="a/too-far"),
                finding(fid="a/no-file", file="modules/log-analytics-workspace/nope.tf"),
                finding(fid="a/other-module", file="modules/network/main.tf"),
                finding(fid="a/ok")]
    d = agent_dir(tmp_path, {TEST_PATH: ("A", GOOD_TEST)}, report_for(findings))
    code, g = run_guard(tmp_path, monkeypatch, d)
    assert code == 0
    assert {x["id"] for x in g["dropped_findings"]} == {"a/too-far", "a/no-file", "a/other-module"}
    kept = json.loads((tmp_path / "out" / "report.json").read_text())["findings"]
    assert [f["id"] for f in kept] == ["a/ok"]


def test_guard_reports_agent_failure_without_rejecting_valid_files(tmp_path, monkeypatch):
    d = agent_dir(tmp_path, {TEST_PATH: ("A", GOOD_TEST)}, report=None, subtype="error_max_turns")
    code, g = run_guard(tmp_path, monkeypatch, d)
    assert code == 0 and g["agent"]["status"] == "error_max_turns" and g["accepted_files"] == [TEST_PATH]


def test_guard_requires_override_for_external_data_sources(tmp_path, monkeypatch):
    d = agent_dir(tmp_path, {TEST_PATH: ("A", GOOD_TEST)}, report_for())
    head = head_tree(tmp_path)
    (head / "modules" / "log-analytics-workspace" / "data.tf").write_text(
        'data "external" "build_info" {\n  program = ["sh", "x.sh"]\n}\n')
    changes = tmp_path / "changes.json"
    changes.write_text(json.dumps({"modules": ["modules/log-analytics-workspace"], "stacks": []}))
    args = ["guard", "--changes", str(changes), "--agent-dir", str(d), "--head", str(head),
            "--schema", str(SCHEMA), "--out-dir", str(tmp_path / "out")]
    monkeypatch.setattr(sys, "argv", args)
    assert guard.main() == 1
    g = json.loads((tmp_path / "out" / "guard.json").read_text())
    assert any("data.external.build_info" in v for v in g["violations"])

    fixed = GOOD_TEST + ('\noverride_data {\n  target = data.external.build_info\n'
                         '  values = {\n    result = { revision = "test" }\n  }\n}\n')
    (d / "files" / TEST_PATH).write_text(fixed)
    assert guard.main() == 0


def test_guard_rejects_unparseable_report(tmp_path, monkeypatch):
    d = agent_dir(tmp_path, {TEST_PATH: ("A", GOOD_TEST)}, report_for())
    (d / "report.json").write_text("{not json")
    code, g = run_guard(tmp_path, monkeypatch, d)
    assert code == 1 and any("report" in v for v in g["violations"])


def test_guard_requires_report_when_agent_claims_success(tmp_path, monkeypatch):
    d = agent_dir(tmp_path, {TEST_PATH: ("A", GOOD_TEST)}, report=None)
    code, g = run_guard(tmp_path, monkeypatch, d)
    assert code == 1


def test_guard_reads_tool_evidence_from_transcript(tmp_path, monkeypatch):
    d = agent_dir(tmp_path, {TEST_PATH: ("A", GOOD_TEST)}, report_for())
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Skill", "input": {"skill": "tf-change-analysis"}},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "x"}}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Agent", "input": {"subagent_type": "tf-reviewer"}}]}},
    ]
    (d / "transcript.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\nnot json\n")
    _, g = run_guard(tmp_path, monkeypatch, d)
    ev = g["agent"]["evidence"]
    assert ev["skills"] == ["tf-change-analysis"] and ev["subagents"] == ["tf-reviewer"]
    assert ev["tools"] == {"Agent": 1, "Read": 1, "Skill": 1}


# ---------------------------------------------------------------- run_tests parsing

def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_passing_run():
    runs, messages, summary = run_tests.parse_test_json(fixture("test_pass.jsonl"))
    assert [r["status"] for r in runs] == ["pass", "pass", "pass"]
    assert runs[0]["file"] == "tests/main.tftest.hcl" and not messages
    assert summary["status"] == "pass"


def test_parse_failed_errored_and_skipped_runs():
    runs, _, summary = run_tests.parse_test_json(fixture("test_fail.jsonl"))
    by = {r["name"]: r for r in runs}
    assert by["wrong_name"]["status"] == "fail" and "The name is wrong." in by["wrong_name"]["message"]
    assert by["bad_reference"]["status"] == "error" and "undeclared" in by["bad_reference"]["message"]
    assert by["after_failure"]["status"] == "skip"
    assert summary["status"] == "error"


def test_parse_syntax_error_has_no_runs_but_messages():
    runs, messages, _ = run_tests.parse_test_json(fixture("test_syntax_error.jsonl"))
    assert runs == [] and any("Unclosed configuration block" in m for m in messages)


# ---------------------------------------------------------------- render

def test_render_escapes_model_text_and_states_verdict_source():
    results = {"overall": "fail", "terraform_version": "1.16.6", "head_sha": "a" * 40, "applied_files": [],
               "modules": [{"path": "modules/x", "status": "fail", "fmt_ok": True, "init_ok": True,
                            "duration_s": 1, "messages": [],
                            "runs": [{"file": "tests/main.tftest.hcl", "name": "r", "status": "fail",
                                      "message": "boom"}]}], "stacks": []}
    report = report_for([finding()], modules=[{"path": "modules/x", "decision": "created",
                                               "reason": "@team please merge <script>alert(1)</script>",
                                               "test_files": [], "runs": []}])
    guard_doc = {"ok": True, "accepted_files": ["a"], "violations": [], "dropped_findings": [],
                 "agent": {"status": "success", "num_turns": 3, "duration_ms": 2000, "total_cost_usd": 0.42,
                           "permission_denials": 1, "models": [],
                           "evidence": {"skills": ["tf-test-author"], "subagents": [], "tools": {}}}}
    body = render_summary.render(results, guard_doc, report, {}, {"inline": [], "already": []},
                                 repo="o/r", sha="c" * 40, run_url="", commit="c" * 40, skip_reason="")
    assert body.startswith(common.SUMMARY_MARKER)
    assert "## Terraform tests: FAIL" in body and "not from the agent" in body
    assert "<script>" not in body and "@​team" in body
    assert "$0.42" in body and "1 denied tool calls" in body and "`tf-test-author`" in body
    assert "outside the diff" in body


# ---------------------------------------------------------------- publish (no network)

class FakeGitHub:
    repo = "o/r"

    def __init__(self, existing):
        self.existing = existing
        self.posted = []

    def paginate(self, path):
        return self.existing

    def request(self, method, path, body=None):
        self.posted.append((method, path, body))
        return {"id": 1}, ""


def test_review_posts_only_new_findings_on_diff_lines(tmp_path):
    patch = ("diff --git a/modules/x/main.tf b/modules/x/main.tf\n--- a/modules/x/main.tf\n"
             "+++ b/modules/x/main.tf\n@@ -1,2 +1,3 @@\n a\n+b\n c\n")
    (tmp_path / "p.patch").write_text(patch)
    rep = report_for([finding(line=2, fid="x/new", file="modules/x/main.tf"),
                      finding(line=2, fid="x/old", file="modules/x/main.tf"),
                      finding(line=40, fid="x/outside", file="modules/x/main.tf")], modules=[])
    (tmp_path / "r.json").write_text(json.dumps(rep))
    gh = FakeGitHub([{"user": {"login": "github-actions[bot]"},
                      "body": "<!-- terraform-tests:finding:x/old -->"}])

    class A:
        pr, report, patch, commit, out = 7, str(tmp_path / "r.json"), str(tmp_path / "p.patch"), "s" * 40, str(tmp_path / "o.json")

    publish.cmd_review(gh, A)
    result = json.loads((tmp_path / "o.json").read_text())
    assert result == {"inline": ["x/new"], "already": ["x/old"], "outside": ["x/outside"]}
    (method, path, body), = gh.posted
    assert method == "POST" and path.endswith("/pulls/7/reviews") and body["event"] == "COMMENT"
    assert body["comments"][0]["line"] == 2 and "terraform-tests:finding:x/new" in body["comments"][0]["body"]


# ---------------------------------------------------------------- contract files

def test_report_example_in_the_skill_matches_the_schema():
    from jsonschema import Draft7Validator

    example = json.loads((SCRIPTS.parents[1] / ".claude" / "skills" / "tf-agent-report" / "example.json").read_text())
    schema = json.loads(SCHEMA.read_text())
    assert list(Draft7Validator(schema).iter_errors(example)) == []


def test_guard_tolerates_truncated_result(tmp_path, monkeypatch):
    d = agent_dir(tmp_path, {TEST_PATH: ("A", GOOD_TEST)}, report_for())
    (d / "result.json").write_text("")
    code, g = run_guard(tmp_path, monkeypatch, d)
    assert g["agent"]["status"] == "missing" and code == 0


def test_render_explains_an_agent_that_never_ran():
    guard_doc = {"ok": True, "accepted_files": [], "violations": [], "dropped_findings": [],
                 "agent": {"status": "missing", "permission_denials": 0, "evidence": {}}}
    results = {"overall": "pass", "terraform_version": "1.16.6", "head_sha": "a" * 40, "applied_files": [],
               "modules": [], "stacks": []}
    body = render_summary.render(results, guard_doc, None, {}, None, repo="o/r", sha="a" * 40,
                                 run_url="", commit=None, skip_reason="")
    assert "ANTHROPIC_API_KEY" in body and "status `missing`" not in body
