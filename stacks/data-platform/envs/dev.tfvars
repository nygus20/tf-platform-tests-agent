subscription_id       = "00000000-0000-0000-0000-000000000001"
tenant_id             = "00000000-0000-0000-0000-000000000000"
environment           = "dev"
vnet_address_space    = ["10.20.0.0/16"]
public_access_enabled = true

data_lakes = {
  raw     = { containers = ["landing", "bronze"] }
  curated = { containers = ["silver", "gold"] }
}
