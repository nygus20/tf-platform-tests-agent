output "id" {
  description = "ID of the data factory."
  value       = azurerm_data_factory.this.id
}

output "principal_id" {
  description = "Object ID of the factory's managed identity."
  value       = azurerm_data_factory.this.identity[0].principal_id
}
