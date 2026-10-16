---
name: tf-test-author
description: Write or update `terraform test` files (*.tftest.hcl) for a module with a mocked azurerm provider, following this repository's conventions. Use after tf-change-analysis decided a module needs tests.
allowed-tools: Read, Write, Edit
---

# Writing terraform tests

Read `reference.md` in this skill's directory before writing. It records how Terraform 1.16 and the mocked `azurerm` provider actually behave in this repository; each rule there was checked by running Terraform. Start new files from `template.txt` in the same directory (a .txt so `terraform fmt -recursive` ignores it).

## Conventions

- One file per module: `modules/<module>/tests/main.tftest.hcl`. Add a second file only when a different `mock_provider` setup is needed, and name it for that purpose (for example `wiring.tftest.hcl`).
- `mock_provider "azurerm" {}` at the top of every file. Never a real `provider` block, never a `module` block inside a `run`.
- One file-level `variables` block with the smallest valid input. Override per run only what that run is about.
- `command = plan` unless the reference says otherwise.
- One `run` per behaviour. Name it in snake_case as the expectation: `rejects_invalid_sku`, `creates_one_container_per_name`, `denies_network_access_by_default`.
- Every `assert` has an `error_message` that is a full sentence saying what must be true.
- Two-space indentation and aligned `=` in each block, as `terraform fmt` would write it. The workflow formats files anyway.

## When updating

- Keep existing runs and their names. Add runs for new behaviour next to related ones.
- Change an existing assertion only under the rules in the agent instructions: a deliberate non-hardening change in the diff. Mention every changed assertion in the module's reason.

## Before you finish a file

Check each assertion against these questions, using `reference.md`:

1. Is every value in the condition known at plan time? Inputs, literals, and arguments built only from them are. Computed attributes are not.
2. Do all variable values pass the provider's own validation? Names must be valid Azure names and IDs must be well-formed Azure resource IDs.
3. Does every `data "external"` in the module have an `override_data` in this file?
4. Does every `expect_failures` name a variable or check that really has a validation for that input?
5. Would a single plan error stop the later runs? It would, so keep risky runs in their own file or make sure they cannot error.
