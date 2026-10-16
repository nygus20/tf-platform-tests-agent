output "resource_group_name" {
  description = "Resource group of the platform."
  value       = azurerm_resource_group.this.name
}

output "databricks_workspace_url" {
  description = "URL of the Databricks workspace."
  value       = module.databricks.workspace_url
}

output "key_vault_uri" {
  description = "URI of the key vault."
  value       = module.key_vault.vault_uri
}
