"""IAM role assumed by CodeBuild during the image build.

Grants:
  - read on the artifacts bucket (source.zip + markers)
  - push to the ECR repo we own
  - read on the configured one-shot bucket prefix (the buildspec
    `aws s3 sync`s the oneshots into the build context so they get
    baked into the image at /opt/oneshots, eliminating the cold-start
    sync on the audio Lambdas)
  - the standard CloudWatch log permissions
"""
from __future__ import annotations

import json

import pulumi
import pulumi_aws as aws


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith('s3://'):
        raise ValueError(f'expected s3:// URI, got {uri!r}')
    rest = uri[len('s3://'):]
    bucket, _, prefix = rest.partition('/')
    return bucket, prefix


def create_codebuild_role(
    *,
    name: str,
    artifacts_bucket_arn: pulumi.Output[str],
    ecr_repo_arn: pulumi.Output[str],
    oneshot_s3_uri: str,
) -> aws.iam.Role:
    oneshot_bucket, oneshot_prefix = _parse_s3_uri(oneshot_s3_uri)
    oneshot_bucket_arn = f'arn:aws:s3:::{oneshot_bucket}'
    oneshot_object_arn = (
        f'arn:aws:s3:::{oneshot_bucket}/{oneshot_prefix}*'
        if oneshot_prefix else f'arn:aws:s3:::{oneshot_bucket}/*'
    )

    # Path /app/ + DeveloperBoundary required by the wol-dev IAM
    # contract — see lambda_iam.py for the same change.
    account_id = aws.get_caller_identity().account_id
    boundary_arn = f'arn:aws:iam::{account_id}:policy/DeveloperBoundary'

    role = aws.iam.Role(
        name,
        name=name,
        path='/app/',
        permissions_boundary=boundary_arn,
        assume_role_policy=json.dumps({
            'Version': '2012-10-17',
            'Statement': [{
                'Effect': 'Allow',
                'Principal': {'Service': 'codebuild.amazonaws.com'},
                'Action': 'sts:AssumeRole',
            }],
        }),
        opts=pulumi.ResourceOptions(delete_before_replace=True),
    )

    aws.iam.RolePolicy(
        f'{name}-policy',
        role=role.id,
        policy=pulumi.Output.all(
            artifacts_bucket_arn=artifacts_bucket_arn,
            ecr_repo_arn=ecr_repo_arn,
        ).apply(lambda a: json.dumps({
            'Version': '2012-10-17',
            'Statement': [
                {
                    'Effect': 'Allow',
                    'Action': [
                        'logs:CreateLogGroup',
                        'logs:CreateLogStream',
                        'logs:PutLogEvents',
                    ],
                    'Resource': '*',
                },
                {
                    'Effect': 'Allow',
                    'Action': [
                        's3:GetObject',
                        's3:GetObjectVersion',
                        's3:PutObject',
                        's3:ListBucket',
                    ],
                    'Resource': [
                        a['artifacts_bucket_arn'],
                        f"{a['artifacts_bucket_arn']}/*",
                    ],
                },
                # Same prefix-scoped read as the Lambda role: the
                # buildspec mirrors this prefix into the image.
                {
                    'Effect': 'Allow',
                    'Action': ['s3:ListBucket'],
                    'Resource': oneshot_bucket_arn,
                    **({'Condition': {'StringLike': {'s3:prefix': [f'{oneshot_prefix}*']}}}
                       if oneshot_prefix else {}),
                },
                {
                    'Effect': 'Allow',
                    'Action': ['s3:GetObject'],
                    'Resource': oneshot_object_arn,
                },
                {
                    'Effect': 'Allow',
                    'Action': ['ecr:GetAuthorizationToken'],
                    'Resource': '*',
                },
                {
                    'Effect': 'Allow',
                    'Action': [
                        'ecr:BatchCheckLayerAvailability',
                        'ecr:CompleteLayerUpload',
                        'ecr:InitiateLayerUpload',
                        'ecr:PutImage',
                        'ecr:UploadLayerPart',
                        'ecr:BatchGetImage',
                        'ecr:GetDownloadUrlForLayer',
                    ],
                    'Resource': a['ecr_repo_arn'],
                },
            ],
        })),
    )
    return role
