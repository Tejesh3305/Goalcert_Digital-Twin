/**
 * network — VPC, subnets, NAT, and the security groups.
 *
 * THE SHAPE, AND WHY
 * ------------------
 * Two public subnets (ALB only) and two private subnets (tasks, RDS, Redis),
 * across two availability zones.
 *
 * The private/public split is the load-bearing part. RDS and ElastiCache have NO
 * route from the internet at all — not "a security group denies it", but no
 * route — so a misconfigured security group is a mistake rather than an
 * exposure. The tasks reach the internet outbound through NAT (they need
 * api.anthropic.com and ECR), and nothing reaches them inbound except the ALB.
 *
 * Two AZs, not three: it is the minimum an ALB requires and the minimum for RDS
 * Multi-AZ failover. A third buys marginal availability for a third more NAT and
 * data-transfer cost, and can be added later without moving anything.
 *
 * THE SECURITY GROUPS ARE CHAINED, NOT CIDR-BASED
 * -----------------------------------------------
 * Each rule references the SOURCE security group rather than a CIDR block. So
 * "the database accepts connections from the tasks" stays true when the subnets
 * are resized or a task moves — a CIDR rule silently becomes wrong or
 * over-permissive when the network changes underneath it.
 */

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "main" {
  cidr_block = var.vpc_cidr
  # Both required for RDS/ElastiCache private DNS names to resolve inside the
  # VPC. Without them the app resolves the RDS endpoint to its PUBLIC address
  # and the connection leaves the VPC and comes back — slower, billed, and it
  # fails outright when the instance is not publicly accessible (which it is not).
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(var.tags, { Name = "${var.name}-vpc" })
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = merge(var.tags, { Name = "${var.name}-igw" })
}

# ── Subnets ──────────────────────────────────────────────────────────────

resource "aws_subnet" "public" {
  count = 2

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = merge(var.tags, {
    Name = "${var.name}-public-${local.azs[count.index]}"
    Tier = "public"
  })
}

resource "aws_subnet" "private" {
  count = 2

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = local.azs[count.index]

  tags = merge(var.tags, {
    Name = "${var.name}-private-${local.azs[count.index]}"
    Tier = "private"
  })
}

# ── NAT ──────────────────────────────────────────────────────────────────
#
# ONE NAT gateway, not one per AZ. A per-AZ NAT survives an AZ failure without
# losing outbound connectivity for the surviving AZ's tasks; a single one is
# ~$35/month cheaper and is a single point of failure for OUTBOUND traffic only
# (inbound requests keep being served, the LLM calls stop). For a platform at
# this stage that is the right trade, and `single_nat_gateway = false` flips it.

resource "aws_eip" "nat" {
  count  = var.single_nat_gateway ? 1 : 2
  domain = "vpc"
  tags   = merge(var.tags, { Name = "${var.name}-nat-${count.index}" })
}

resource "aws_nat_gateway" "main" {
  count = var.single_nat_gateway ? 1 : 2

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = merge(var.tags, { Name = "${var.name}-nat-${count.index}" })

  depends_on = [aws_internet_gateway.main]
}

# ── Routing ──────────────────────────────────────────────────────────────

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = merge(var.tags, { Name = "${var.name}-public" })
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count  = 2
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[var.single_nat_gateway ? 0 : count.index].id
  }

  tags = merge(var.tags, { Name = "${var.name}-private-${count.index}" })
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# ── VPC endpoints ────────────────────────────────────────────────────────
#
# S3 through a GATEWAY endpoint. Without it every blob read and write goes out
# through NAT and is billed per gigabyte — and the 3-D platform moves GLBs, so
# that is real money rather than rounding. A gateway endpoint costs nothing.

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id

  tags = merge(var.tags, { Name = "${var.name}-s3" })
}

# ── Security groups ──────────────────────────────────────────────────────

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "Public HTTPS ingress to the load balancer"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTPS from the internet"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Port 80 exists ONLY to redirect to 443 (see the edge module's listener). It
  # serves no content, so a user who types the bare hostname is corrected rather
  # than given a connection error.
  ingress {
    description = "HTTP, redirected to HTTPS"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.name}-alb" })
}

resource "aws_security_group" "tasks" {
  name        = "${var.name}-tasks"
  description = "ECS tasks: ingress from the ALB only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "App port, from the load balancer only"
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  # Outbound is open because the tasks legitimately need ECR, Secrets Manager,
  # CloudWatch, S3, Neo4j Aura and api.anthropic.com. Restricting it to a set of
  # CIDRs would mean tracking AWS's published ranges forever and breaking on the
  # day they change.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.name}-tasks" })
}

resource "aws_security_group" "database" {
  name        = "${var.name}-database"
  description = "Postgres: reachable only from the tasks"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Postgres from the tasks"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.tasks.id]
  }

  tags = merge(var.tags, { Name = "${var.name}-database" })
}

resource "aws_security_group" "cache" {
  name        = "${var.name}-cache"
  description = "Redis: reachable only from the tasks"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Redis from the tasks"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.tasks.id]
  }

  tags = merge(var.tags, { Name = "${var.name}-cache" })
}
