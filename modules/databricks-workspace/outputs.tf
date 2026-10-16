output "id" {
  description = "ID of the workspace."
  value       = azurerm_databricks_workspace.this.id
}

output "workspace_url" {
  description = "URL of the workspace."
  value       = azurerm_databricks_workspace.this.workspace_url
}
