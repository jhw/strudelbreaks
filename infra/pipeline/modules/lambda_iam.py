"""IAM role assumed by every strudelbreaks Lambda.

Grants:
  - the standard Lambda execution / CloudWatch log permissions
  - s3:ListBucket + s3:GetObject on the configured one-shot bucket

The bucket is encoded into the policy at create time so Lambdas
can't read anything beyond the configured prefix. If multiple buckets
ever land here, extend `_s3_statements` to take a list and emit one
allow per bucket.
"""
from __future__ import annotations

import json

import pulumi_aws as aws


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith('s3://'):
        raise ValueError(f'expected s3:// URI, got {uri!r}')
    rest = uri[len('s3://'):]
    bucket, _, prefix = rest.partition('/')
    return bucket, prefix


def create_lambda_role(*, name: str, oneshot_s3_uri: str) -> aws.iam.Role:
    bucket, prefix = _parse_s3_uri(oneshot_s3_uri)
    bucket_arn = f'arn:aws:s3:::{bucket}'
    object_arn = f'arn:aws:s3:::{bucket}/{prefix}*' if prefix else f'arn:aws:s3:::{bucket}/*'

    role = aws.iam.Role(
        name,
        name=name,
        assume_role_policy=json.dumps({
            'Version': '2012-10-17',
            'Statement': [{
                'Effect': 'Allow',
                'Principal': {'Service': 'lambda.amazonaws.com'},
                'Action': 'sts:AssumeRole',
            }],
        }),
    )

    aws.iam.RolePolicyAttachment(
        f'{name}-basic-execution',
        role=role.name,
        policy_arn='arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole',
    )

    aws.iam.RolePolicy(
        f'{name}-s3',
        role=role.id,
        policy=json.dumps({
            'Version': '2012-10-17',
            'Statement': [
                {
                    'Effect': 'Allow',
                    'Action': ['s3:ListBucket'],
                    'Resource': bucket_arn,
                    **({'Condition': {'StringLike': {'s3:prefix': [f'{prefix}*']}}}
                       if prefix else {}),
                },
                {
                    'Effect': 'Allow',
                    'Action': ['s3:GetObject'],
                    'Resource': object_arn,
                },
            ],
        }),
    )
    return role
