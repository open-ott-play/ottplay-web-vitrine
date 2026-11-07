terraform {
  required_version = ">= 1.7.0, < 2.0.0"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "5.28.1"
    }
  }
}

# Use the established operator environment; never place a token in TF variables.
provider "cloudflare" {}
