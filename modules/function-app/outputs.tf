output "id" {
  description = "ID of the function app."
  value       = azurerm_linux_function_app.this.id
}

output "principal_id" {
  description = "Object ID of the app's managed identity."
  value       = azurerm_linux_function_app.this.identity[0].principal_id
}
