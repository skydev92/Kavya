terraform {
  required_providers {
    scaleway = {
      source  = "scaleway/scaleway"
      version = "~> 2.13.1"
    }
  }
}

provider "scaleway" {
  zone   = "nl-ams-1"
  region = "nl-ams"
}

resource "scaleway_container_namespace" "routellm" {
  name        = "routellm"
  description = "Namespace for RouteLLM containers"
}

resource "scaleway_container" "routellm" {
  namespace_id = scaleway_container_namespace.routellm.id
  name         = "routellm-server"
  registry_image = "rg.nl-ams.scw.cloud/${scaleway_container_namespace.routellm.name}/${var.repository_name}:${var.image_tag}"
  port         = 6060
  cpu_limit    = 1000
  memory_limit = 2048
  min_scale    = 1
  max_scale    = 5
  environment_variables = {
    OPENAI_API_KEY = var.openai_api_key
  }
}

resource "scaleway_domain_record" "routellm" {
  dns_zone = var.domain_name
  name     = "api"
  type     = "CNAME"
  data     = scaleway_container.routellm.domain_name
  ttl      = 60
}