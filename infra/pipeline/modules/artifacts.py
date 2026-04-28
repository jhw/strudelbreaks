"""Source-artifact bucket for CodeBuild input.

The deploy script uploads `source.zip` here under a content-addressed
key; CodeBuild reads it as the build's source. A small marker file
under `markers/<sha>.txt` records the last successfully-built hash
so the deploy script can short-circuit unchanged builds.
"""
from __future__ import annotations

import pulumi_aws as aws


def create_artifacts_bucket(name: str) -> aws.s3.Bucket:
    bucket = aws.s3.Bucket(
        name,
        bucket=name,
        force_destroy=True,
    )
    aws.s3.BucketVersioningV2(
        f'{name}-versioning',
        bucket=bucket.id,
        versioning_configuration={'status': 'Enabled'},
    )
    aws.s3.BucketPublicAccessBlock(
        f'{name}-public-access',
        bucket=bucket.id,
        block_public_acls=True,
        block_public_policy=True,
        ignore_public_acls=True,
        restrict_public_buckets=True,
    )
    return bucket
