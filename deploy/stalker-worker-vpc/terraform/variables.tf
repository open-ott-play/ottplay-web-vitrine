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
  description = "Existing tunnel; every connector must reach the same dedicated Stalker relay service."
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
  description = "Unused dedicated Stalker relay Worker name; do not adopt another application's Worker."
  type        = string
  default     = "ottplay-stalker-api"
  nullable    = false
  validation {
    condition     = length(var.worker_name) <= 63 && can(regex("^ottplay-stalker-api(-[a-z0-9]+)*$", var.worker_name))
    error_message = "worker_name must be ottplay-stalker-api or its dedicated hyphenated suffix, at most 63 characters."
  }
}

variable "backend_ipv4" {
  description = "Verified dedicated Stalker relay Service IPv4 as reached from every selected tunnel connector. No public origin, hostname, or caller-selected target."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", var.backend_ipv4)) && can(cidrnetmask("${var.backend_ipv4}/32"))
    error_message = "backend_ipv4 must be one IPv4 address, without port or prefix."
  }
}

variable "backend_port" {
  description = "Verified dedicated Stalker relay HTTP service port."
  type        = number
  default     = 8080
  nullable    = false
  validation {
    condition     = var.backend_port == 8080
    error_message = "backend_port must be the dedicated relay port 8080."
  }
}

variable "rate_namespace_id" {
  description = "One unused account rate-limit namespace ID; never share another Worker's counter."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^[1-9][0-9]{0,14}$", var.rate_namespace_id))
    error_message = "Use one distinct positive integer string of at most 15 digits."
  }
}

variable "requests_per_minute" {
  description = "Explicit qualified per-connecting-address rate; here.now may share an egress address."
  type        = number
  nullable    = false
  validation {
    condition     = var.requests_per_minute == floor(var.requests_per_minute) && var.requests_per_minute >= 1 && var.requests_per_minute <= 100000
    error_message = "Set the qualified positive integer limit explicitly."
  }
}

variable "publish" {
  description = "Enable the dedicated production workers.dev URL only after review and private backend verification."
  type        = bool
  default     = false
  nullable    = false
}
