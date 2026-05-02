"""Pipeline stack: ECR + CodeBuild + source artifact bucket + IAM.

Idempotent — `pulumi up` on this stack is almost always a no-op once
created. The deploy script (`scripts/stack/deploy.py`) re-runs it on
every deploy so the pipeline drifts back into shape if anything was
hand-edited.

Outputs:
  - ecr_repo_url        the registry URL the build pushes to
  - ecr_repo_name       the repository name (used by the build env)
  - artifacts_bucket    S3 bucket for source.zip + content-hash markers
  - codebuild_project   the build project name (used by deploy.py)
  - lambda_role_arn     IAM role assumed by the four Lambdas (consumed
                        by infra/app)
"""
from __future__ import annotations

import pulumi

from modules import artifacts, registry, codebuild_iam, lambda_iam, build_pipeline


config = pulumi.Config()
project_name = pulumi.get_project()
stack = pulumi.get_stack()

# Bucket the Lambda role gets s3:GetObject on. Pulled from stack
# config so the same code can target different sample banks per
# environment without code edits — the user's "no code smell" fix.
oneshot_s3_uri = config.require('oneshot_s3_uri')

repo = registry.create_ecr_repo(f'{project_name}-{stack}')
bucket = artifacts.create_artifacts_bucket(f'{project_name}-{stack}-artifacts')

cb_role = codebuild_iam.create_codebuild_role(
    name=f'{project_name}-{stack}-codebuild',
    artifacts_bucket_arn=bucket.arn,
    ecr_repo_arn=repo.arn,
    oneshot_s3_uri=oneshot_s3_uri,
)

lambda_role = lambda_iam.create_lambda_role(
    name=f'{project_name}-{stack}-lambda',
    oneshot_s3_uri=oneshot_s3_uri,
    artifacts_bucket=bucket.bucket,
)

project = build_pipeline.create_codebuild_project(
    name=f'{project_name}-{stack}-build',
    role_arn=cb_role.arn,
    artifacts_bucket=bucket.bucket,
    ecr_repo=repo,
    oneshot_s3_uri=oneshot_s3_uri,
)

pulumi.export('ecr_repo_url', repo.repository_url)
pulumi.export('ecr_repo_name', repo.name)
pulumi.export('artifacts_bucket', bucket.bucket)
pulumi.export('codebuild_project', project.name)
pulumi.export('lambda_role_arn', lambda_role.arn)
pulumi.export('oneshot_s3_uri', oneshot_s3_uri)
