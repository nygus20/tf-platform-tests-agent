resource "azurerm_resource_group" "this" {
  name     = "rg-${local.prefix}"
  location = var.location
  tags     = local.tags
}

module "network" {
  source = "../../modules/network"

  name                = local.prefix
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  address_space       = var.vnet_address_space
  subnets             = local.subnets
  tags                = local.tags
}

module "log_analytics" {
  source = "../../modules/log-analytics-workspace"

  name                = "log-${local.prefix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  tags                = local.tags
}

module "data_lake" {
  source   = "../../modules/storage-account"
  for_each = var.data_lakes

  name                = "st${replace(local.prefix, "-", "")}${each.key}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  is_data_lake        = true
  containers          = each.value.containers
  allowed_subnet_ids  = [module.network.subnet_ids["apps"]]
  tags                = local.tags
}

module "key_vault" {
  source = "../../modules/key-vault"

  name                = "kv-${local.prefix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  tenant_id           = var.tenant_id
  allowed_subnet_ids  = [module.network.subnet_ids["apps"]]
  secret_readers = {
    data_factory = module.data_factory.principal_id
    function_app = module.function_app.principal_id
  }
  tags = local.tags
}

module "data_factory" {
  source = "../../modules/data-factory"

  name                       = "adf-${local.prefix}"
  resource_group_name        = azurerm_resource_group.this.name
  location                   = var.location
  public_network_enabled     = var.public_access_enabled
  log_analytics_workspace_id = module.log_analytics.id
  linked_storage_accounts    = { for key, lake in module.data_lake : key => lake.primary_dfs_endpoint }
  tags                       = local.tags
}

module "databricks" {
  source = "../../modules/databricks-workspace"

  name                          = "dbw-${local.prefix}"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = var.location
  public_network_access_enabled = var.public_access_enabled
  tags                          = local.tags
}

module "function_app" {
  source = "../../modules/function-app"

  name                          = "func-${local.prefix}"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = var.location
  storage_account_name          = module.data_lake["raw"].name
  storage_account_id            = module.data_lake["raw"].id
  public_network_access_enabled = var.public_access_enabled
  tags                          = local.tags
}

module "lake_endpoint" {
  source   = "../../modules/private-endpoint"
  for_each = local.lake_endpoints

  name                = "${local.prefix}-${each.key}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  subnet_id           = module.network.subnet_ids["endpoints"]
  target_resource_id  = module.data_lake[each.value.lake].id
  subresource_name    = each.value.sub
  tags                = local.tags
}

module "key_vault_endpoint" {
  source = "../../modules/private-endpoint"

  name                = "${local.prefix}-vault"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  subnet_id           = module.network.subnet_ids["endpoints"]
  target_resource_id  = module.key_vault.id
  subresource_name    = "vault"
  tags                = local.tags
}
