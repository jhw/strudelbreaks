"""IAM role assumed by CodeBuild during the image build.

Grants:
  - read on the artifacts bucket (source.zip + markers)
  - push to the ECR repo we own
  - the standard CloudWatch log permissions
"""
from __future__ import annotations

import json

import pulumi
import pulumi_aws as aws


def create_codebuild_role(
    *,
    name: str,
    artifacts_bucket_arn: pulumi.Output[str],
    ecr_repo_arn: pulumi.Output[str],
) -> aws.iam.Role:
    role = aws.iam.Role(
        name,
        name=name,
        assume_role_policy=json.dumps({
            'Version': '2012-10-17',
            'Statement': [{
                'Effect': 'Allow',
                'Principal': {'Service': 'codebuild.amazonaws.com'},
                'Action': 'sts:AssumeRole',
            }],
        }),
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
