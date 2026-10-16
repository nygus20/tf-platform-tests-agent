---
name: tf-review
description: Review Terraform modules and stacks in a pull request for security, reliability and correctness issues a human reviewer should know about, and return them as JSON findings with file and line. Use when asked to review Terraform code; read-only.
allowed-tools: Read, Glob, Grep
---

# Terraform review

## Scope

Review the modules and stacks the caller names. For a module, also read how `stacks/` uses it, because a safe module default can be overridden unsafely there. Read `checklist.md` in this skill's directory first.

Prioritise what the diff changes. In backfill mode, when there is no meaningful diff, review the whole module.

## What makes a good finding

- It is about the code as written, with a concrete consequence: "the function app accepts plain FTP uploads", not "consider security".
- It points at the line where a person would make the change. For a missing argument, point at the resource's opening line.
- It is new information. Do not report that tests are missing; the agent handles tests.
- It is honest about certainty. Use `confidence: low` when it depends on intent you cannot see, such as a deliberate dev-only setting.
- Six good findings beat twenty weak ones. Report at most 12 per run, highest severity first.

Severity:
- `high`: exposes data or access, or will fail on deploy in a supported environment.
- `medium`: weakens a security control or will cause drift or outages under realistic use.
- `low`: maintainability, cost, or convention problems with a real consequence.
- `info`: worth knowing, no action needed.

## Output

Reply with a JSON array. Each element:

```json
{
  "id": "function-app/ftps-allowed",
  "file": "modules/function-app/main.tf",
  "line": 32,
  "end_line": 32,
  "severity": "medium",
  "category": "security",
  "confidence": "high",
  "title": "FTP deployment is allowed on the function app",
  "detail": "site_config sets ftps_state to AllAllowed, so plain FTP and FTPS uploads are both accepted. Credentials and code can travel unencrypted.",
  "suggestion": "Set ftps_state to Disabled, or FtpsOnly if FTP deployment is really needed."
}
```

- `id`: `<module-or-stack-name>/<short-slug>`, lowercase letters, digits and dashes. Keep it stable so a rerun produces the same id for the same issue.
- `file`: a path relative to the repository root, starting with `modules/` or `stacks/`.
- `line` and `end_line`: 1-based line numbers in the file as it is now. Confirm them by reading the file. Omit `end_line` for a single line.
- `category`: one of `security`, `reliability`, `correctness`, `cost`, `maintainability`, `testability`.
- `title`: at most 120 characters. `detail`: at most 800 characters, plain text, no Markdown. `suggestion`: at most 500 characters, describing a change a person could make. Never apply it.
