group "default" {
  targets = ["backend", "frontend"]
}

target "backend" {
  context    = "."
  dockerfile = "Dockerfile"
  target     = "backend-runtime"
  tags       = ["foundation-intelligence-backend:local"]
  platforms  = ["linux/amd64", "linux/arm64"]
}

target "frontend" {
  context    = "."
  dockerfile = "Dockerfile"
  target     = "frontend-runtime"
  tags       = ["foundation-intelligence-frontend:local"]
  platforms  = ["linux/amd64", "linux/arm64"]
}
