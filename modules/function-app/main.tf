data "external" "build_info" {
  program = ["sh", "${path.module}/scripts/build-info.sh"]
}

resource "azurerm_service_plan" "this" {
  name                = "asp-${var.name}"
  resource_group_name = var.resource_group_name
  location            = var.location
  os_type             = "Linux"
  sku_name            = "EP1"
  tags                = var.tags
}

resource "azurerm_linux_function_app" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  service_plan_id     = azurerm_service_plan.this.id

  storage_account_name          = var.storage_account_name
  storage_uses_managed_identity = true

  https_only                    = true
  public_network_access_enabled = var.public_network_access_enabled

  identity {
    type = "SystemAssigned"
  }

  site_config {
    minimum_tls_version = "1.2"
    ftps_state          = "AllAllowed"

    application_stack {
      python_version = var.python_version
    }
  }

  app_settings = merge(var.app_settings, {
    BUILD_REVISION = data.external.build_info.result.revision
  })

  tags = var.tags
}

resource "azurerm_role_assignment" "runtime_storage" {
  scope                = var.storage_account_id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = azurerm_linux_function_app.this.identity[0].principal_id
}
