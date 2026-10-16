resource "azurerm_key_vault" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  tenant_id           = var.tenant_id
  sku_name            = "standard"

  rbac_authorization_enabled = true
  purge_protection_enabled   = true
  soft_delete_retention_days = 30

  network_acls {
    default_action             = "Deny"
    bypass                     = "AzureServices"
    virtual_network_subnet_ids = var.allowed_subnet_ids
  }

  tags = var.tags
}

data "azurerm_role_definition" "secrets_user" {
  name  = "Key Vault Secrets User"
  scope = azurerm_key_vault.this.id
}

resource "azurerm_role_assignment" "secret_reader" {
  for_each = var.secret_readers

  scope              = azurerm_key_vault.this.id
  role_definition_id = data.azurerm_role_definition.secrets_user.id
  principal_id       = each.value
}
