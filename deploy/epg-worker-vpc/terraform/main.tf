locals {
  worker_hostname = "${var.worker_name}.${var.workers_namespace}.workers.dev"
  relay_source    = "${path.module}/../relay.mjs"
  limiter_names = {
    match      = "MATCH_LIMITER"
    programmes = "PROGRAMME_LIMITER"
    current    = "CURRENT_LIMITER"
  }
}

# This binding grants one service address/port, never an entire private network.
resource "cloudflare_connectivity_directory_service" "epg" {
  account_id = var.account_id
  name       = "${var.worker_name}-backend"
  type       = "http"
  http_port  = var.backend_port
  host = {
    ipv4 = var.backend_ipv4
    network = {
      tunnel_id = var.tunnel_id
    }
  }
}

resource "cloudflare_worker" "epg" {
  account_id     = var.account_id
  name           = var.worker_name
  logpush        = false
  tail_consumers = []
  subdomain = {
    enabled          = var.publish
    previews_enabled = false
  }
  observability = {
    enabled = false
    logs = {
      enabled = false
    }
    traces = {
      enabled = false
    }
  }

  lifecycle {
    postcondition {
      condition = try(contains([
        local.worker_hostname,
        "https://${local.worker_hostname}",
      ], self.subdomain.url), false)
      error_message = "The account must already own the exact workers_namespace. This module never registers a namespace; verify it with publish=false."
    }
  }
}

resource "cloudflare_worker_version" "epg" {
  account_id         = var.account_id
  worker_id          = cloudflare_worker.epg.id
  compatibility_date = "2026-09-18"
  main_module        = "relay.mjs"
  modules = [{
    name           = "relay.mjs"
    content_type   = "application/javascript+module"
    content_base64 = filebase64(local.relay_source)
  }]
  # The provider merges API readback by position before restoring binding order.
  # Match the API's name order so rate-limit metadata cannot move to PUBLIC_HOST.
  bindings = concat([
    {
      name       = "BACKEND"
      type       = "vpc_service"
      service_id = cloudflare_connectivity_directory_service.epg.service_id
    },
    ], [for route, name in local.limiter_names : {
      name         = name
      type         = "ratelimit"
      namespace_id = var.rate_namespace_ids[route]
      simple = {
        limit  = var.requests_per_minute[route]
        period = 60
      }
    }], [
    {
      name = "PUBLIC_HOST"
      type = "plain_text"
      text = local.worker_hostname
    },
  ])

  lifecycle {
    create_before_destroy = true
  }
}

resource "cloudflare_workers_deployment" "epg" {
  account_id  = var.account_id
  script_name = cloudflare_worker.epg.name
  strategy    = "percentage"
  versions = [{
    version_id = cloudflare_worker_version.epg.id
    percentage = 100
  }]
}
