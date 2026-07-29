/**
 * secrets — the Secrets Manager entries, and deliberately NOT their values.
 *
 * TERRAFORM CREATES THE CONTAINERS. YOU SET THE CONTENTS.
 *
 * That split is the whole design of this module and it is worth being explicit
 * about why, because the convenient alternative is very tempting:
 *
 *   * A secret passed in as a variable lives in a `.tfvars` file, which lives in
 *     git. That is the failure this is avoiding.
 *   * A secret set by Terraform lives in TERRAFORM STATE, in plaintext, forever
 *     — including in every historical version of the state file in the S3
 *     bucket. Anyone who can read state can read every credential, and state
 *     access is normally granted far more widely than production credentials.
 *
 * So `aws_secretsmanager_secret_version` is created ONCE with a placeholder and
 * then ignored (`ignore_changes = [secret_string]`), which means:
 *
 *   - the ARNs exist, so the task definition and IAM policies can reference them
 *   - `terraform apply` never reads, writes or diffs a real credential
 *   - rotating a secret is `aws secretsmanager put-secret-value`, with no
 *     Terraform run and no state churn
 *
 * TWO OF THESE ARE GENERATED, NOT TYPED
 * -------------------------------------
 * The JWT signing key and the secret pepper have no external source — they just
 * need to be long random strings that never change. Generating them here is
 * safe in a way that a database password is not: they are created by, and only
 * ever known to, this infrastructure. They still land in state, so treat state
 * as sensitive regardless (it always was — RDS and Redis passwords are in the
 * data module).
 */

terraform {
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

locals {
  # name -> whether Terraform seeds a generated value or leaves a placeholder for
  # a human. See the module docstring for why the split exists.
  managed = {
    "database-url" = {
      description = "NXR_DATABASE_URL — full postgresql:// URL including credentials and sslmode=require."
      generated   = true
    }
    "redis-url" = {
      description = "NXR_REDIS_URL — rediss:// URL with the ElastiCache AUTH token."
      generated   = true
    }
    "jwt-secret" = {
      description = "NXR_JWT_SECRET — HS256 signing key for access tokens. Rotating it signs everyone out."
      generated   = true
    }
    "secret-pepper" = {
      description = "NXR_SECRET_PEPPER — server-side pepper for API-key and refresh-token hashes. ROTATING THIS INVALIDATES EVERY ISSUED API KEY AND SESSION."
      generated   = true
    }
    "anthropic-api-key" = {
      description = "ANTHROPIC_API_KEY — the single LLM credential for agents/ and copilot/. Set this manually."
      generated   = false
    }
    "neo4j-password" = {
      description = "NEO4J_PASSWORD — graph database credential. Set this manually."
      generated   = false
    }
  }
}

resource "aws_secretsmanager_secret" "main" {
  for_each = local.managed

  name        = "${var.name}/${each.key}"
  description = each.value.description

  # Zero, so a `terraform destroy` in staging can immediately recreate the same
  # names. Without it the names are reserved for 7-30 days and the next apply
  # fails with "already scheduled for deletion", which is a confusing wall to hit.
  # PRODUCTION SHOULD OVERRIDE THIS to 7 or more — it is the undo button for an
  # accidental deletion.
  recovery_window_in_days = var.recovery_window_days

  tags = merge(var.tags, { Name = "${var.name}/${each.key}" })
}

resource "random_password" "jwt" {
  length  = 64
  special = false # base62 — avoids any shell/JSON quoting hazard downstream
}

resource "random_password" "pepper" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret_version" "seed" {
  for_each = local.managed

  secret_id = aws_secretsmanager_secret.main[each.key].id

  secret_string = (
    each.key == "database-url" ? var.database_url :
    each.key == "redis-url" ? var.redis_url :
    each.key == "jwt-secret" ? random_password.jwt.result :
    each.key == "secret-pepper" ? random_password.pepper.result :
    # A placeholder that is obviously not a credential. The app fails loudly on
    # it rather than half-working, which is the desired outcome for "you forgot
    # to set this".
    "REPLACE_ME"
  )

  lifecycle {
    # THE LINE THAT MAKES THIS SAFE. After the first apply Terraform never looks
    # at these values again, so rotating a secret out of band does not show as
    # drift and `terraform apply` cannot revert a rotation.
    ignore_changes = [secret_string]
  }
}
