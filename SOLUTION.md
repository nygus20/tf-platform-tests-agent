# Terraform test agent: solution notes

A Claude Code agent with four skills writes and updates `terraform test` files for the modules a pull request changes. A GitHub Actions workflow runs those tests itself, and posts the results and review findings to the same pull request. The agent never edits infrastructure code, never decides pass or fail, and never opens other pull requests.

## How it works

```
pull_request (opened, synchronize, reopened, labeled)
  |
  detect   no secrets, read-only    changed files -> modules and stacks, agent eligibility
  |
  agent    API key, read-only       Claude Code in a stripped work directory; no terraform, no repo scripts
  |
  guard    no secrets, read-only    accepts only modules/<m>/tests/*.tftest.hcl, validates the report
  |
  test     no secrets, read-only    terraform test + validate; this job's result is the verdict
  |
  publish  GITHUB_TOKEN write       commit accepted tests, review comments, sticky summary, check run
```

| Job | Permissions | Secrets | Runs |
|---|---|---|---|
| detect | `contents: read`, `pull-requests: read` | none | `detect_changes.py` from the base commit |
| agent | `contents: read` | `ANTHROPIC_API_KEY`, only in the one step that runs `claude` | `npm ci --ignore-scripts`, inline shell, `claude` |
| guard | `contents: read` | none | `guard.py` from the base commit |
| test | `contents: read` | none | `terraform`, `run_tests.py` from the base commit |
| publish | `contents: write`, `pull-requests: write`, `checks: write` | `GITHUB_TOKEN` only | git, `publish.py` and `render_summary.py` from the base commit |

The workflow file is `.github/workflows/terraform-tests.yml`. `scripts-ci.yml` unit-tests the scripts on the pull request head, because the main workflow only runs scripts from the base commit.

## Where the agent and skills are defined

| Path | What it is | Used when |
|---|---|---|
| `.claude/agents/tf-test-agent.md` | Main agent. Sonnet 5.5, medium effort. Tools: Read, Glob, Grep, Write, Edit, `Agent(tf-reviewer)`, Skill. | Started with `claude -p --agent tf-test-agent` |
| `.claude/agents/tf-reviewer.md` | Read-only reviewer subagent. Opus 5.5, high effort. | Spawned once per run by the main agent |
| `.claude/skills/tf-change-analysis/` | Decides create, update, unchanged or skipped per module from the diff and existing tests | Step 2 |
| `.claude/skills/tf-test-author/` | Conventions, a template, and `reference.md`: how the mocked provider really behaves, each rule checked against Terraform 1.16.6 | Step 3 |
| `.claude/skills/tf-review/` | What makes a useful finding, plus an Azure checklist | Inside the reviewer |
| `.claude/skills/tf-agent-report/` | The report format, an example that a unit test validates against the schema | Step 5 |
| `.claude/settings.ci.json` | Permission rules: what the agent may write, which agents and skills it may use | Every run, via `--settings` |

The skills are invoked on demand through the Skill tool, not preloaded. The workflow reads the run's transcript and shows which skills and subagents were actually used, so the PR comment does not rely on the agent's own account.

## Data contract

- **Workflow to agent:** `.agent/changes.json` (modules, stacks, mode, files) and `.agent/changes.patch`.
- **Agent to workflow:** test files, and `.agent/report.json`, which must validate against `.github/schemas/report.schema.json`. It holds a decision and reason per module and findings with file, line, severity, category, confidence, title, detail and suggestion.
- **Test runner to publisher:** `results.json` against `.github/schemas/results.schema.json`, produced only from `terraform test -json`.
- Everything a person reads in the PR is rendered by `render_summary.py` and `publish.py`. Model-written text is escaped: no HTML, links, mentions or code fences, and lengths are capped.

## Main decisions and what they cost

| Decision | Why | Given up |
|---|---|---|
| The key-holding job runs only Claude Code, without Bash | `terraform test` executes code the PR controls, for example `data "external"` in function-app. A job with a secret must not run it. | The agent cannot run its own tests and fix them in the same run. A wrong test shows up as a failure in the test job instead. |
| The verdict comes from `run_tests.py` parsing `terraform test -json` | The agent's opinion of its own work is not evidence | Nothing |
| The guard accepts all files or none | One out-of-scope write means the run cannot be trusted | A good test file is discarded with a bad one |
| Commit with `GITHUB_TOKEN`, then create a check run on the new commit | GitHub never starts workflows from `GITHUB_TOKEN` pushes, so a loop is impossible by construction | Jobs do not rerun on the agent's commit. The `terraform-test` check run carries the result there, and branch protection should require that check. |
| Scripts and `.claude/` come from the base commit | A PR cannot change the judge or the agent's instructions for its own run | Changes to the scripts or skills take effect after merge. `scripts-ci` tests them before. |
| The agent sees no PR title, body or comments | They are the cheapest prompt-injection channel, and the diff says what changed | The agent cannot use the author's stated intent |
| Tests are committed even when they fail | A failing test that pins a security setting the PR weakened is the most useful output | A red PR until a person decides |
| Pinned Claude Code via `package-lock.json`, installed with `--ignore-scripts` | Integrity hashes, and no package script runs next to the key | Version bumps go through Dependabot |

## Security model

| Threat | Control |
|---|---|
| Prompt injection in code, comments or the diff | Agent prompt treats all repository content as data and reports text aimed at automation as a finding. No Bash, no web, no MCP. Writes are limited by permission rules, then checked again by the guard. Reads outside the work directory are denied. |
| Instruction files in the PR (`CLAUDE.md`, `AGENTS.md`, `.claude/`, `.mcp.json`) | Deleted from the work directory before the agent starts. Agent config is copied from the base commit. `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, `--strict-mcp-config` with an empty config, `disableAllHooks`. |
| Agent writes outside its scope | `--permission-mode dontAsk` with an allow list of `Edit(modules/*/tests/*.tftest.hcl)` and `Edit(.agent/report.json)`. Then the guard checks every changed path, status, size, encoding and content. Then publish refuses to commit anything else. |
| Test files that execute code | The guard rejects `module` blocks in runs and real `provider` blocks, and requires an `override_data` for every `data "external"` in the module. Without that override, a test run executes the script, which was verified. |
| Secret exfiltration | The key exists only in one step of one job, which has a read-only token and no tool that can read environment variables. |
| Fork pull requests | No secrets and a read-only token. `agent`, `guard` and `publish` are skipped. `test` runs the PR's own tests and writes the job summary. To use the agent, a maintainer pushes the branch into the repository. |
| Runaway cost or time | Path filters, `--max-turns 60`, `--max-budget-usd 3`, job timeouts, one concurrency group per PR with cancel-in-progress |
| A person with write access edits the workflow | Out of scope for the workflow itself. Use branch protection, CODEOWNERS on `.github/` and `.claude/`, and a spend-limited API key in its own workspace. |

Several controls were added because a test run showed the documentation was not enough. With `--agent`, Claude Code 2.1.301 ignores `--json-schema`, so the report is a file the guard validates. Built-in agents such as `Explore` could be spawned without a permission check, so the agent's `tools` list names `Agent(tf-reviewer)` and the settings deny the others. Some bundled skills are auto-approved, so the settings deny them by name.

## Running it

Prerequisites:
- A GitHub repository with Actions enabled and the labels `tf-agent:skip` and `tf-agent:backfill`.
- Repository secret `ANTHROPIC_API_KEY`, ideally from a dedicated workspace with a spend limit.
- Settings, Actions, General: workflow permissions "Read repository contents", and "Allow GitHub Actions to create and approve pull requests" off. Each job asks for what it needs.
- Branch protection on `main` requiring the `terraform-test` check.
- No cloud credentials. Nothing talks to Azure.

Use:
- Open a pull request that changes `modules/**` or `stacks/**`. The workflow runs on open, push and reopen.
- Add `tf-agent:backfill` to make the agent consider every module, or `tf-agent:skip` to run the tests without the agent.
- Locally: `scripts/agent-local.sh --base origin/main` runs the same work directory, flags, guard, tests and summary. It needs Terraform 1.16.6, Node 22, Python with `jsonschema`, and a Claude Code login or API key. Add `--apply` to copy accepted tests into your working tree.
- Script tests: `python -m pytest .github/scripts/tests`.

## Example run

**The change.** `modules/function-app` hard-coded `ftps_state = "AllAllowed"`. The pull request makes it a validated variable that defaults to `Disabled`. The module had no tests.

**What the agent decided.** It used `tf-change-analysis` to decide `created` for function-app, because the module had no tests. With `tf-test-author` it wrote nine runs:
- `disables_ftps_by_default`, `passes_ftps_state_through` and `rejects_invalid_ftps_state` pin the change itself;
- the other six pin HTTPS only, TLS 1.2, the managed identity, the resource names, the merge of app settings with the build revision, and the storage role assignment's scope and role.

It overrode `data.external.build_info`, so the build script never runs in tests. The tests still pass with the script deleted. It spawned `tf-reviewer`, which used `tf-review`, then wrote the report with `tf-agent-report`.

**Results.** `terraform test`: 9 passed, 0 failed. The guard accepted 1 file. The run took 19 turns, 94 seconds and $0.38 on Sonnet 5.5 plus Opus 5.5.

**Findings for reviewers.**
- `high`: the Elastic Premium app has no VNet integration while its runtime storage denies public access.
- `high`: Elastic Premium needs a key-based content share, but the storage module disables shared keys.
- `medium`: the external data source puts the git revision into app settings, so every commit causes a diff.
- `medium`: the function identity gets Storage Blob Data Owner on the whole raw data lake.
- `medium`: public network access defaults to `true`.
- `info`: the new validation still allows `AllAllowed`.

None of these were changed in code.

The pull request in this repository shows the same flow on GitHub, with the commit by `github-actions[bot]`, review comments, the summary comment and the `terraform-test` check run.

## How the agent was verified

Each behaviour below was observed in a run, not assumed from documentation.

| Check | Result |
|---|---|
| Write scope | A canary agent tried to create a `.tf` file and edit `main.tf`. Both were denied, and the denials are listed in `permission_denials`. |
| Agent and skill limits | Built-in agents and some bundled skills were usable without a permission check. They are now blocked by the agent's `tools` list and by deny rules, and a rerun showed them denied. |
| Structured output | `--json-schema` returned `structured_output` without `--agent` and nothing with it. So the report is a file, and the guard validates it. |
| Mock behaviour | Every rule in `tf-test-author/reference.md` comes from a test run. Examples: azurerm validation runs under the mock, an unknown condition skips the rest of the file, and `command = apply` fails on random IDs. |
| External script | Without `override_data`, `terraform test` ran `build-info.sh` and the script wrote a marker file. With it, the script did not run. |
| Prompt injection | One branch lowered storage TLS to `TLS1_0`, added a comment telling the agent to accept it and edit `main.tf`, and added a `CLAUDE.md` with instructions. The agent kept the TLS 1.2 assertion, so the test failed and the run went red. It edited no infrastructure file, reported both texts as `high` security findings, and reported the downgrade. The `CLAUDE.md` never reached its work directory. |
| Workflow behaviour | On a smoke-test pull request, a rerun updated the same comment, a second push cancelled the earlier run, a missing API key failed only the agent job, and `tf-agent:skip` skipped the agent and showed the reason. |
| Scripts | 32 unit tests cover change detection, every guard rule, parsing of real `terraform test -json` output, rendering and escaping, and the review poster. |

## What the tests can and cannot show

The tests run `plan` against a mocked `azurerm` provider. Checked against Terraform 1.16.6 and azurerm 4.81.0:

They can show:
- the module's own logic: defaults, naming, conditionals, `for_each` counts, dynamic blocks, variable validations, and wiring from inputs to arguments;
- that a security setting keeps its value, so a PR that weakens it fails;
- that the provider accepts the argument values at plan. The mock still runs azurerm's validation, so an invalid key vault name fails without any cloud access.

They cannot show:
- that Azure accepts the configuration. API-side rules are not checked: the Databricks NSG rules requirement, SKU and region availability, name uniqueness, quotas;
- anything about computed values such as IDs, principal IDs and endpoints, which the mock invents;
- runtime behaviour such as RBAC propagation, DNS resolution or network reachability;
- that the stack composes correctly beyond `terraform validate`.

## Assumptions

- Modules live in `modules/<name>/` with a `main.tf`, and tests in `modules/<name>/tests/`.
- `azurerm` is the only provider that needs mocking. Any `data "external"` is overridden.
- People with write access are trusted to the level of the API key.
- Stacks get review findings and `terraform validate`, not generated tests.

## Limitations

- The agent writes tests blind. A wrong assertion is caught by the test job, not fixed in the same run.
- Findings come from a model. They carry a confidence, and a person decides. Locations are checked, the content is not.
- Inline comments only land on lines in the diff. Other findings are listed in the summary with links.
- Jobs do not rerun on the agent's own commit. The check run carries the result there.
- A full-repository backfill costs several times a normal run.

## With many more modules, teams or repositories

- Fan out one agent job per module with a matrix, and cap modules per run.
- Move the agent, skills, scripts and workflow into a reusable workflow, versioned and called from each repository, so the judge is not in the repository it judges.
- Use a GitHub App instead of `GITHUB_TOKEN` for cross-repository use and a clear identity, and per-team API keys with spend limits.
- Use CODEOWNERS on module test directories so the owning team reviews generated tests.
- Track acceptance rate, failing-test rate and cost per run, and use them to tune models and effort.

## For a production version

- A second, sandboxed agent pass that may run `terraform test` in a job without secrets, using a short-lived token from a broker instead of the raw key, so the agent can fix its own failures.
- Evaluations: a fixed set of pull requests with known correct tests and known findings, run on every change to the skills or model.
- Contract tests for stacks with `override_module`, and a nightly apply against a real sandbox subscription for the things mocks cannot show.
- Signed commits for agent changes, and an approval gate (a GitHub environment) for the agent job on sensitive repositories.
- Structured output enforced by the CLI once it works together with `--agent`.
