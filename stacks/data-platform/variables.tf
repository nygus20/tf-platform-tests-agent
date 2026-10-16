variable "subscription_id" {
  description = "Azure subscription that receives the resources."
  type        = string
}

variable "tenant_id" {
  description = "Microsoft Entra tenant."
  type        = string
}

variable "environment" {
  description = "Environment name."
  type        = string

  validation {
    condition     = contains(["dev", "uat", "prod"], var.environment)
    error_message = "The environment must be dev, uat or prod."
  }
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "westeurope"
}

variable "vnet_address_space" {
  description = "Address range of the virtual network."
  type        = list(string)
}

variable "data_lakes" {
  description = "Data lake storage accounts by short key, with their containers."
  type = map(object({
    containers = set(string)
  }))
}

variable "public_access_enabled" {
  description = "Allow public network access to the platform services. Meant for dev only."
  type        = bool
  default     = false
}
