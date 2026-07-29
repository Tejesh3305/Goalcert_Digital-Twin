# Infrastructure as code

Everything AWS_DEPLOYMENT.md used to describe as a list of `aws` CLI commands,
expressed as Terraform. That document remains the explanation of *why* the
topology is what it is; this directory is the executable form of it.

## Why this exists

A runbook of CLI commands is not a deployment. It cannot be reviewed as a diff,
cannot be rolled back, drifts silently the moment someone clicks something in the
console, and cannot be reproduced for a staging environment without a person
retyping it correctly. Every one of those is a real failure and the last one is
the common one: staging and production diverge, so staging stops predicting
anything.

## What it creates

| Module | Resources |
|---|---|
| `network` | VPC, 2× public + 2× private subnets across AZs, NAT, security groups |
| `data` | RDS Postgres 16 (Multi-AZ, encrypted), ElastiCache Redis 7, S3 bucket |
| `secrets` | Secrets Manager entries for the DB URL, JWT key, pepper, Anthropic key |
| `compute` | ECR repo, ECS cluster + Fargate service, task definition, autoscaling |
| `edge` | ALB, HTTPS listener, ACM certificate, target group on `/health/ready` |
| `observability` | CloudWatch log group, alarms, dashboard |
| `iam` | Task + execution roles, and the GitHub OIDC role CI deploys with |

## Usage

```bash
cd infra/terraform/environments/prod

terraform init
terraform plan  -var-file=prod.tfvars   # read this. every time.
terraform apply -var-file=prod.tfvars
```

State is in S3 with DynamoDB locking (`backend.tf`). Create those two once, by
hand, before the first `init` — a bootstrap chicken-and-egg that every Terraform
setup has:

```bash
aws s3api create-bucket --bucket nextxr-tfstate --region eu-west-1 \
  --create-bucket-configuration LocationConstraint=eu-west-1
aws s3api put-bucket-versioning --bucket nextxr-tfstate \
  --versioning-configuration Status=Enabled
aws dynamodb create-table --table-name nextxr-tflock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
```

## What Terraform does NOT own

Deliberate omissions, each with a reason:

- **Secret VALUES.** Terraform creates the Secrets Manager entries and their IAM
  policies; it does not set their contents. A secret in a `.tfvars` file is a
  secret in git, and a secret in Terraform state is readable by anyone with state
  access. Set them once with `aws secretsmanager put-secret-value`.
- **The container image.** CI builds and pushes it (`.github/workflows/deploy.yml`)
  and updates the service. Terraform manages the *service*, not what runs in it,
  so a deploy does not require a Terraform run.
- **Database migrations.** A one-off ECS task, run before the service rolls. See
  `db/migrations.py` and the `migrate` job in the deploy workflow.
- **Neo4j.** Not in here yet. Aura is a separate SaaS signup, and self-hosting it
  on ECS is a different topology decision than the rest of this — see
  AWS_DEPLOYMENT.md §6. Its URI and credentials are consumed as inputs.

## Cost

Roughly, in `eu-west-1`, at the defaults in `prod.tfvars.example`:

| | Monthly |
|---|---|
| ECS Fargate (2 × 1 vCPU / 2 GB) | ~$70 |
| RDS `db.t4g.medium` Multi-AZ | ~$130 |
| ElastiCache `cache.t4g.micro` | ~$15 |
| ALB | ~$20 |
| NAT gateway | ~$35 |
| S3 + CloudWatch + data transfer | ~$15 |
| **Total** | **~$285** |

`environments/staging` uses single-AZ RDS, one task and no NAT (public subnets
with `assign_public_ip`), which is about $90. Set `multi_az = false` and
`desired_count = 1` in prod too if this is a pilot rather than a product.
