variable "name" {
  description = "Name suffix used for the network resources."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that holds the network."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "address_space" {
  description = "Address ranges of the virtual network, in CIDR notation."
  type        = list(string)

  validation {
    condition     = alltrue([for range in var.address_space : can(cidrhost(range, 0))])
    error_message = "Every address range must be a valid CIDR block."
  }
}

variable "subnets" {
  description = "Subnets by key. Each subnet gets its own network security group."
  type = map(object({
    address_prefixes  = list(string)
    service_endpoints = optional(list(string), [])
  }))
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}
