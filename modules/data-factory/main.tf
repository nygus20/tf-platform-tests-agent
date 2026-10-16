resource "azurerm_data_factory" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location

  public_network_enabled = var.public_network_enabled

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

resource "azurerm_data_factory_linked_service_data_lake_storage_gen2" "this" {
  for_each = var.linked_storage_accounts

  name                 = "ls-${each.key}"
  data_factory_id      = azurerm_data_factory.this.id
  url                  = each.value
  use_managed_identity = true
}

resource "azurerm_monitor_diagnostic_setting" "this" {
  name                       = "diag-${var.name}"
  target_resource_id         = azurerm_data_factory.this.id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log {
    category = "PipelineRuns"
  }

  enabled_log {
    category = "ActivityRuns"
  }
}
