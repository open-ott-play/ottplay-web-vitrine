variable "account_id" {
  description = "Existing account owning the tunnel and workers.dev namespace."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.account_id))
    error_message = "account_id must be 32 lowercase hexadecimal characters."
  }
}

variable "tunnel_id" {
  description = "Existing tunnel; every connector must reach the same dedicated EPG service."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.tunnel_id))
    error_message = "tunnel_id must be the existing tunnel's lowercase UUID."
  }
}

variable "workers_namespace" {
  description = "Existing workers.dev namespace label; the module never creates or changes one."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.workers_namespace))
    error_message = "workers_namespace must be one lowercase DNS label of at most 63 characters."
  }
}

variable "worker_name" {
  description = "Unused dedicated EPG Worker name; do not adopt another application's Worker."
  type        = string
  default     = "ottplay-epg-api"
  nullable    = false
  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.worker_name))
    error_message = "worker_name must be one lowercase DNS label of at most 63 characters."
  }
}

variable "backend_ipv4" {
  description = "Verified dedicated EPG Service IPv4 as reached from every selected tunnel connector. No public origin, hostname, or caller-selected target."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", var.backend_ipv4)) && can(cidrnetmask("${var.backend_ipv4}/32"))
    error_message = "backend_ipv4 must be one IPv4 address, without port or prefix."
  }
}

variable "backend_port" {
  description = "Verified dedicated EPG HTTP service port."
  type        = number
  default     = 8080
  nullable    = false
  validation {
    condition     = var.backend_port == floor(var.backend_port) && var.backend_port >= 1 && var.backend_port <= 65535
    error_message = "backend_port must be an integer from 1 through 65535."
  }
}

variable "rate_namespace_ids" {
  description = "Three unused account rate-limit namespace IDs, isolated from other Workers and each other."
  type = object({
    match      = string
    programmes = string
    current    = string
  })
  nullable = false
  validation {
    condition = alltrue([
      for id in values(var.rate_namespace_ids) : can(regex("^[1-9][0-9]{0,14}$", id))
    ]) && length(distinct(values(var.rate_namespace_ids))) == 3
    error_message = "Use three distinct positive integer strings, at most 15 digits each."
  }
}

variable "requests_per_minute" {
  description = "Per-route, per-connecting-address burst limits at each Cloudflare location. Not global accounting or here.now's end-user hourly limits."
  type = object({
    match      = number
    programmes = number
    current    = number
  })
  default = {
    match      = 120
    programmes = 600
    current    = 60
  }
  nullable = false
  validation {
    condition = alltrue([
      for limit in values(var.requests_per_minute) : limit == floor(limit) && limit >= 1 && limit <= 100000
    ])
    error_message = "Each burst limit must be a positive integer no greater than 100000."
  }
}

variable "publish" {
  description = "Enable the dedicated production workers.dev URL only after review and private backend verification."
  type        = bool
  default     = false
  nullable    = false
}
