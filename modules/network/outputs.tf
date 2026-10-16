output "vnet_id" {
  description = "ID of the virtual network."
  value       = azurerm_virtual_network.this.id
}

output "subnet_ids" {
  description = "Subnet IDs by subnet key."
  value       = { for key, subnet in azurerm_subnet.this : key => subnet.id }
}
