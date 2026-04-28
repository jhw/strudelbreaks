"""CodeBuild project that bakes the strudelbreaks Lambda image.

Source: S3 (artifacts bucket, key supplied per-build by the deploy
script). Buildspec: `docker/buildspec.yml` from the source zip.
Privileged mode is on so the build can run `docker build`.
"""
from __future__ import annotations

import pulumi
import pulumi_aws as aws


def create_codebuild_project(
    *,
    name: str,
    role_arn: pulumi.Output[str],
    artifacts_bucket: pulumi.Output[str],
    ecr_repo: aws.ecr.Repository,
) -> aws.codebuild.Project:
    region = aws.get_region().name

    return aws.codebuild.Project(
        name,
        name=name,
        service_role=role_arn,
        artifacts={'type': 'NO_ARTIFACTS'},
        source={
            'type': 'S3',
            'location': pulumi.Output.concat(artifacts_bucket, '/source.zip'),
            'buildspec': 'docker/buildspec.yml',
        },
        environment={
            'compute_type': 'BUILD_GENERAL1_MEDIUM',
            'image': 'aws/codebuild/standard:7.0',
            'type': 'LINUX_CONTAINER',
            'privileged_mode': True,
            'environment_variables': [
                {'name': 'AWS_DEFAULT_REGION', 'value': region},
                {
                    'name': 'ECR_REGISTRY',
                    'value': ecr_repo.repository_url.apply(lambda u: u.split('/', 1)[0]),
                },
                {'name': 'ECR_REPO_NAME', 'value': ecr_repo.name},
            ],
        },
        logs_config={
            'cloudwatch_logs': {'status': 'ENABLED'},
        },
    )
