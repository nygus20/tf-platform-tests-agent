output "id" {
  description = "ID of the key vault."
  value       = azurerm_key_vault.this.id
}

output "vault_uri" {
  description = "URI of the key vault."
  value       = azurerm_key_vault.this.vault_uri
}
