"""ECR repository for the strudelbreaks Lambda image."""
from __future__ import annotations

import pulumi_aws as aws


def create_ecr_repo(name: str) -> aws.ecr.Repository:
    repo = aws.ecr.Repository(
        name,
        name=name,
        force_delete=True,
        image_scanning_configuration={'scan_on_push': True},
        image_tag_mutability='MUTABLE',
    )
    # Keep the last 10 images so rollback to the previous deploy is
    # always available without re-building. Older digests get GC'd.
    aws.ecr.LifecyclePolicy(
        f'{name}-lifecycle',
        repository=repo.name,
        policy="""{
            "rules": [
                {
                    "rulePriority": 1,
                    "description": "Keep last 10 images",
                    "selection": {
                        "tagStatus": "any",
                        "countType": "imageCountMoreThan",
                        "countNumber": 10
                    },
                    "action": {"type": "expire"}
                }
            ]
        }""",
    )
    return repo
