# Terraform Platform Base

A small Terraform repository for a data platform on Azure: networking, storage, secrets, monitoring, data integration, analytics and a function app.

## Layout

```text
modules/            Reusable modules. Each has main.tf, variables.tf, outputs.tf and versions.tf.
  <module>/tests/   Tests for a module, where they exist. Run them from the module folder.
stacks/
  data-platform/    The stack that uses the modules. One set of variables per environment in envs/.
```

## Requirements

- Terraform 1.9 or newer
- Internet access to download providers
- No Azure subscription or credentials

## Run the tests of a module

The tests use a mocked `azurerm` provider, so they need no credentials.

```sh
cd modules/storage-account
terraform init -backend=false
terraform test
```

## Check the stack

```sh
cd stacks/data-platform
terraform init -backend=false
terraform fmt -check -recursive ../..
terraform validate
```

The stack has no backend configured. A real deployment would need one, and credentials for it.

## Test agent

Pull requests that change Terraform get tests written by a Claude Code agent, run by `terraform test`, and reported back to the pull request. See [SOLUTION.md](SOLUTION.md) for how it works and why. To run it locally: `scripts/agent-local.sh --base origin/main`.

## Conventions

- Resource files in a stack and modules use `main.tf`, `variables.tf`, `outputs.tf` and `versions.tf`.
- Use `for_each` rather than `count` for repeated resources.
- Environments are `dev`, `uat` and `prod`.
- Format code with `terraform fmt -recursive` before committing.
