variable "name" {
  description = "Name suffix of the endpoint."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that holds the endpoint."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "subnet_id" {
  description = "Subnet that gets the endpoint's network interface."
  type        = string
}

variable "target_resource_id" {
  description = "Resource the endpoint connects to."
  type        = string
}

variable "subresource_name" {
  description = "Sub-resource of the target, for example blob, dfs, vault or dataFactory."
  type        = string
}

variable "private_dns_zone_id" {
  description = "Private DNS zone that resolves the endpoint. Leave empty for none."
  type        = string
  default     = null
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}
