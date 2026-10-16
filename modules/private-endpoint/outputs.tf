output "id" {
  description = "ID of the private endpoint."
  value       = azurerm_private_endpoint.this.id
}

output "private_ip_address" {
  description = "Private IP address of the endpoint."
  value       = azurerm_private_endpoint.this.private_service_connection[0].private_ip_address
}
