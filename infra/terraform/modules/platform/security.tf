resource "aws_security_group" "alb" {
  name_prefix = "${local.name}-alb-"
  description = "Public HTTPS ingress to the application load balancer"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.common_tags, { Name = "${local.name}-alb" })
}

resource "aws_security_group" "ecs" {
  name_prefix = "${local.name}-ecs-"
  description = "Private API and worker tasks"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.common_tags, { Name = "${local.name}-ecs" })
}

resource "aws_security_group" "rds" {
  name_prefix = "${local.name}-rds-"
  description = "Private PostgreSQL from application tasks only"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.common_tags, { Name = "${local.name}-rds" })
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP redirect or approved dev ingress"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_api" {
  security_group_id            = aws_security_group.alb.id
  description                  = "API target traffic only"
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
  referenced_security_group_id = aws_security_group.ecs.id
}

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.ecs.id
  description                  = "API traffic from ALB"
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_egress_rule" "ecs_https" {
  security_group_id = aws_security_group.ecs.id
  description       = "TLS to AWS endpoints and approved external sources"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "ecs_postgresql" {
  security_group_id            = aws_security_group.ecs.id
  description                  = "PostgreSQL"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.rds.id
}

resource "aws_vpc_security_group_ingress_rule" "postgresql_from_ecs" {
  security_group_id            = aws_security_group.rds.id
  description                  = "PostgreSQL from ECS"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.ecs.id
}
