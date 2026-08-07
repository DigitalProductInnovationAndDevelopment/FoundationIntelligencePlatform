output "deployment_contract" {
  value = {
    api_dns         = module.platform.api_load_balancer_dns_name
    frontend_dns    = module.platform.frontend_distribution_domain_name
    deployment_role = module.platform.deployment_role_arn
    schedules       = module.platform.schedules_enabled
  }
}

output "release_gate" {
  value = module.platform.release_gate
}
