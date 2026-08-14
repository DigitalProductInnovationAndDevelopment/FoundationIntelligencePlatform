resource "aws_db_subnet_group" "postgresql" {
  name       = "${local.name}-postgresql"
  subnet_ids = values(aws_subnet.database)[*].id

  tags = merge(local.common_tags, { Name = "${local.name}-postgresql" })
}

resource "aws_db_parameter_group" "postgresql" {
  name_prefix = "${local.name}-postgresql16-"
  family      = "postgres16"

  parameter {
    name  = "log_connections"
    value = "1"
  }

  parameter {
    name  = "log_disconnections"
    value = "1"
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  tags = merge(local.common_tags, { Name = "${local.name}-postgresql16" })
}

resource "aws_iam_role" "rds_monitoring" {
  name_prefix = "${local.name}-rds-monitoring-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "monitoring.rds.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

resource "aws_db_instance" "postgresql" {
  identifier = "${local.name}-postgresql"

  engine                        = "postgres"
  engine_version                = "16.4"
  instance_class                = var.rds_instance_class
  allocated_storage             = var.rds_allocated_storage_gib
  max_allocated_storage         = var.rds_max_storage_gib
  storage_type                  = "gp3"
  storage_encrypted             = true
  kms_key_id                    = aws_kms_key.platform.arn
  db_name                       = "foundation_intelligence"
  username                      = "foundation_admin"
  manage_master_user_password   = true
  master_user_secret_kms_key_id = aws_kms_key.platform.arn

  db_subnet_group_name   = aws_db_subnet_group.postgresql.name
  parameter_group_name   = aws_db_parameter_group.postgresql.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  multi_az               = var.rds_multi_az

  backup_retention_period   = var.rds_backup_retention_days
  backup_window             = "02:00-03:00"
  maintenance_window        = "sun:03:30-sun:04:30"
  copy_tags_to_snapshot     = true
  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name}-final"

  auto_minor_version_upgrade            = true
  enabled_cloudwatch_logs_exports       = ["postgresql", "upgrade"]
  monitoring_interval                   = 60
  monitoring_role_arn                   = aws_iam_role.rds_monitoring.arn
  performance_insights_enabled          = true
  performance_insights_kms_key_id       = aws_kms_key.platform.arn
  performance_insights_retention_period = 7

  apply_immediately = false

  tags = merge(local.common_tags, {
    Name              = "${local.name}-postgresql"
    BackupRetention   = tostring(var.rds_backup_retention_days)
    DeletionProtected = "true"
  })

  lifecycle {
    prevent_destroy = true
  }
}
