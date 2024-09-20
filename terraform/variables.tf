variable "image_tag" {
  description = "The tag of the Docker image to deploy"
  type        = string
}

variable "openai_api_key" {
  description = "The OpenAI API key"
  type        = string
}

variable "repository_name" {
  description = "The name of the GitHub repository"
  type        = string
}

variable "domain_name" {
  description = "The domain name for the API"
  type        = string
}