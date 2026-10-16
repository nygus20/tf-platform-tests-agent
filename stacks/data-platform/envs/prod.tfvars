subscription_id    = "00000000-0000-0000-0000-000000000002"
tenant_id          = "00000000-0000-0000-0000-000000000000"
environment        = "prod"
vnet_address_space = ["10.30.0.0/16"]

data_lakes = {
  raw     = { containers = ["landing", "bronze"] }
  curated = { containers = ["silver", "gold"] }
}
