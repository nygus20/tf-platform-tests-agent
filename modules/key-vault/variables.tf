variable "name" {
  description = "Key vault name, 3 to 24 characters."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that holds the vault."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "tenant_id" {
  description = "Microsoft Entra tenant that owns the vault."
  type        = string
}

variable "secret_readers" {
  description = "Object IDs of identities that may read secrets, by a short key."
  type        = map(string)
  default     = {}
}

variable "allowed_subnet_ids" {
  description = "Subnets that may reach the vault. Everything else is denied."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}
