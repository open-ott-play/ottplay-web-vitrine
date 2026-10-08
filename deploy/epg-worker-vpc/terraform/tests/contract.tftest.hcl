mock_provider "cloudflare" {
  mock_resource "cloudflare_worker" {
    defaults = {
      subdomain = {
        url = "https://ottplay-epg-api.test-account.workers.dev"
      }
    }
  }
}

variables {
  account_id        = "00000000000000000000000000000000"
  tunnel_id         = "00000000-0000-0000-0000-000000000000"
  workers_namespace = "test-account"
  backend_ipv4      = "192.0.2.10"
  rate_namespace_ids = {
    match      = "91001"
    programmes = "91002"
    current    = "91003"
  }
}

run "private_by_default" {
  command = plan

  assert {
    condition     = !cloudflare_worker.epg.subdomain.enabled && !cloudflare_worker.epg.subdomain.previews_enabled
    error_message = "The first plan must not publish production or preview URLs."
  }
  assert {
    condition     = !cloudflare_worker.epg.observability.enabled && !cloudflare_worker.epg.logpush && length(cloudflare_worker.epg.tail_consumers) == 0
    error_message = "The EPG relay must not export request bodies or caller metadata through logs/tails."
  }
  assert {
    condition = (
      cloudflare_connectivity_directory_service.epg.type == "http" &&
      cloudflare_connectivity_directory_service.epg.host.ipv4 == "192.0.2.10" &&
      cloudflare_connectivity_directory_service.epg.http_port == 8080 &&
      cloudflare_connectivity_directory_service.epg.host.network.tunnel_id == var.tunnel_id
    )
    error_message = "Only the verified single EPG address and port may be reachable."
  }
  assert {
    condition = (
      length(cloudflare_worker_version.epg.bindings) == 5 &&
      length([for b in cloudflare_worker_version.epg.bindings : b if b.type == "ratelimit" && b.simple.period == 60]) == 3 &&
      length([for b in cloudflare_worker_version.epg.bindings : b if b.type == "vpc_network" || b.type == "secret_text"]) == 0
    )
    error_message = "Require one fixed VPC service, one host and three rate limits; no network-wide or credential binding."
  }
  assert {
    condition = (
      tolist([for binding in cloudflare_worker_version.epg.bindings : binding.name]) ==
      sort([for binding in cloudflare_worker_version.epg.bindings : binding.name])
    )
    error_message = "Keep bindings in API name order so provider readback cannot merge nested fields into another binding."
  }
}

run "explicit_publication" {
  command = plan
  variables {
    publish = true
  }
  assert {
    condition     = cloudflare_worker.epg.subdomain.enabled && !cloudflare_worker.epg.subdomain.previews_enabled
    error_message = "Publication must enable only the configured production hostname."
  }
  assert {
    condition     = output.api_origin == "https://ottplay-epg-api.test-account.workers.dev"
    error_message = "Do not infer or select a different account namespace."
  }
}

run "reject_shared_rate_counters" {
  command = plan
  variables {
    rate_namespace_ids = {
      match      = "91001"
      programmes = "91001"
      current    = "91003"
    }
  }
  expect_failures = [var.rate_namespace_ids]
}

run "reject_invalid_private_target" {
  command = plan
  variables {
    backend_ipv4 = "192.0.2.10/24"
  }
  expect_failures = [var.backend_ipv4]
}
