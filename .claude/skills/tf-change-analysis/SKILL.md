---
name: tf-change-analysis
description: Decide, per Terraform module in a pull request, whether tests must be created, updated, or left alone, based on the diff and the existing tests. Use before writing any terraform test file.
allowed-tools: Read, Glob, Grep
---

# Change analysis

Produce one decision per module listed in `.agent/changes.json` under `modules`. Stacks are reviewed, not tested.

## For each module

1. Read every `*.tf` file in the module and every file in its `tests/` directory.
2. From `.agent/changes.patch`, list what changed in that module. Classify each change:
   - **interface**: variable added, removed, renamed, type or default changed, validation added or changed; output added, removed or changed.
   - **resource**: resource or data source added or removed; `for_each` or `count` key logic changed; an argument value or expression changed; a dynamic block changed.
   - **hardening**: any change to the attributes listed as hardening in your agent instructions.
   - **cosmetic**: comments, formatting, descriptions only.
3. Inventory the existing tests: each `run` name, its `command`, the attributes it asserts, and any `expect_failures`.
4. List every data source that the `azurerm` mock does not serve. Today that means any `data "external"`. Each one needs an `override_data` in every test file of that module.
5. Decide:
   - **created**: the module has no test file. In `backfill` mode this applies to every untested module.
   - **updated**: an existing test asserts something the diff changed, or the diff adds behaviour no test covers (new variable with validation, new resource, new conditional, new output).
   - **unchanged**: only cosmetic changes, or the existing tests already cover the change. Say which run covers it.
   - **skipped**: the module cannot be tested with a mocked plan at all. Explain why. This should be rare.

## What to cover when creating tests

Aim for the behaviour a reviewer would want to stay fixed, not for line coverage:

- every variable `validation` block: one accepting case (the file-level variables) and one rejecting case with `expect_failures`;
- every conditional (`for_each` over a map or set, `dynamic` blocks, ternaries): one case each way;
- hardening attributes and their defaults;
- naming conventions built from inputs;
- inputs that are passed through to arguments (wiring), and outputs that expose them.

Do not plan tests for computed values such as `id`, `principal_id`, URLs or endpoints. The mock invents them.

Carry the decisions and the reason for each into the report. A reason is one or two plain sentences that name the change and the runs that cover it.
