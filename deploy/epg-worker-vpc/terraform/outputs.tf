output "api_origin" {
  description = "Proposed machine API origin; not live until publish=true and transport acceptance succeeds."
  value       = "https://${local.worker_hostname}"
}

output "relay_sha256" {
  description = "SHA-256 of the exact Worker source included in this deployment."
  value       = filesha256(local.relay_source)
}
