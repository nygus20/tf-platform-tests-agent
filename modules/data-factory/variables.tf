variable "name" {
  description = "Data factory name."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that holds the factory."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "public_network_enabled" {
  description = "Allow access to the factory from the public internet."
  type        = bool
  default     = true
}

variable "log_analytics_workspace_id" {
  description = "Workspace that receives the factory's diagnostic logs."
  type        = string
}

variable "linked_storage_accounts" {
  description = "Data lake accounts to link, by a short key. The value is the account's DFS endpoint."
  type        = map(string)
  default     = {}
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}
