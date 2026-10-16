# Terraform test reference for this repository

Terraform 1.16.6, `hashicorp/azurerm` 4.81.0, `hashicorp/external` 2.4.2. Every rule below was confirmed by running `terraform test` against these modules.

## What the mock does

- `mock_provider "azurerm" {}` uses the real provider schema and needs no credentials.
- The provider's own argument validation still runs on values known at plan time. A key vault named `kv` fails with "may only contain alphanumeric characters and dashes and must be between 3-24 chars". So test inputs must be valid Azure values: real-looking names and well-formed resource IDs such as `/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Storage/storageAccounts/sttest001`.
- Computed attributes (`id`, `principal_id`, `identity[0].principal_id`, `vault_uri`, `primary_dfs_endpoint`, `workspace_url`, private IP addresses) are unknown at plan.
- Under `command = apply` the mock fills computed attributes with random strings such as `l2a6u8fb`. When such a value flows into an argument that must be an Azure ID (for example a role assignment `scope`), the provider rejects it. Do not use `command = apply` unless every computed value that flows into another resource has a `mock_resource` default.

## Plan-time assertions

Known at plan, safe to assert:
- arguments set from literals or variables: `azurerm_key_vault.this.purge_protection_enabled == true`, `azurerm_storage_account.this.min_tls_version == "TLS1_2"`;
- names built from inputs: `azurerm_virtual_network.this.name == "vnet-tp-test"`;
- nested blocks by index: `azurerm_key_vault.this.network_acls[0].default_action == "Deny"`, `azurerm_linux_function_app.this.site_config[0].minimum_tls_version == "1.2"`;
- instance counts of `for_each` resources: `length(azurerm_storage_container.this) == 2`;
- instance keys: `contains(keys(azurerm_subnet.this), "apps")`;
- wiring from a variable: `azurerm_role_assignment.runtime_storage.scope == var.storage_account_id`;
- a `dynamic` block's presence: `length(azurerm_private_endpoint.this.private_dns_zone_group) == 0`.

Not known at plan:
- any computed attribute, and any comparison that involves one, such as `azurerm_role_assignment.x.scope == azurerm_key_vault.this.id`. This fails with "Unknown condition value", and every later run in the same file is then skipped.
- outputs that expose computed attributes.

## Checking wiring between resources

To assert that one resource uses another's computed attribute, give the mock a realistic value and generate it at plan:

```hcl
mock_provider "azurerm" {
  mock_resource "azurerm_key_vault" {
    override_during = plan
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.KeyVault/vaults/kv-test-001"
    }
  }
}
```

With that, `azurerm_role_assignment.secret_reader["adf"].scope == azurerm_key_vault.this.id` passes at plan. `override_during = plan` on `mock_provider` alone is not enough. It needs a `mock_resource`, `mock_data` or override block that supplies the value.

Put this in its own file (for example `tests/wiring.tftest.hcl`) so the main file keeps the plain mock.

## Data sources the mock does not cover

`data "external"` belongs to the `external` provider. The azurerm mock does not touch it, and without an override Terraform runs the program during plan. Every test file of such a module must override it at file level:

```hcl
override_data {
  target = data.external.build_info
  values = {
    result = {
      revision = "test-revision"
    }
  }
}
```

The workflow's guard rejects a test file that lacks this override. With it, assertions can use the value: `azurerm_linux_function_app.this.app_settings["BUILD_REVISION"] == "test-revision"`.

`azurerm` data sources, such as `data.azurerm_role_definition`, are mocked. Their computed attributes are unknown at plan, like resources.

## Validation failures

```hcl
run "rejects_invalid_sku" {
  command = plan

  variables {
    sku = "basic"
  }

  expect_failures = [var.sku]
}
```

`expect_failures` names the variable whose `validation` block should fail. It passes only if that validation fails, so use it only where the module defines one.

## Run order and failures

- Runs in a file execute in order and share state.
- A failed assertion does not stop later runs.
- A plan error, such as an unknown condition, a provider validation error or a bad reference, marks that run as an error and skips every later run in the file.

## Module-specific notes

- `function-app`: has `data "external" "build_info"`. Override it. `site_config` and `identity` are nested blocks, so index them with `[0]`. `app_settings` is a map that merges `var.app_settings` with `BUILD_REVISION`.
- `key-vault`: names must be 3 to 24 characters of letters, digits and dashes. `secret_readers` is a map, and the role assignment scope is computed.
- `storage-account`: the name validation is `^[a-z0-9]{3,24}$`. `containers` is a set of strings. The network rules live in `azurerm_storage_account_network_rules.this`, not on the account.
- `network`: `subnets` is a map of objects with optional `service_endpoints`. The address space validation uses `cidrhost`.
- `databricks-workspace`: `sku` has a validation (`standard` or `premium`). `custom_parameters` is a nested block.
- `private-endpoint`: `private_dns_zone_group` is a dynamic block, present only when `private_dns_zone_id` is set. `subnet_id`, `target_resource_id` and `private_dns_zone_id` must be well-formed Azure IDs.
- `data-factory`: `enabled_log` appears twice in the diagnostic setting. `linked_storage_accounts` is a map of DFS endpoint URLs, so use URLs like `https://sttest001.dfs.core.windows.net/`.
