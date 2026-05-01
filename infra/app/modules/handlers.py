"""Lambda function factories — container + ZIP variants.

Both factories create the function's CloudWatch log group up-front and
return `(function, log_group)`. Pre-creating the log group is a hard
requirement for the chatops-for-lambda subscription filter pattern: a
filter can't attach to a log group that doesn't exist yet, and
Lambda's auto-created log groups only appear on first invocation.

`make_container_handler` is the workhorse for the four export Lambdas
+ launch. `make_zip_handler` exists for the error notifier and any
future stdlib-only utility Lambda.
"""
from __future__ import annotations

import pulumi
import pulumi_aws as aws


def make_container_handler(
    *,
    name: str,
    image_uri: str,
    role_arn: str,
    cmd: str,
    memory: int,
    timeout: int,
    env: pulumi.Input[dict],
    retention_in_days: int = 14,
) -> tuple[aws.lambda_.Function, aws.cloudwatch.LogGroup]:
    """Container-image Lambda + pre-created log group."""
    log_group = aws.cloudwatch.LogGroup(
        f'{name}-logs',
        name=f'/aws/lambda/{name}',
        retention_in_days=retention_in_days,
    )
    fn = aws.lambda_.Function(
        name,
        name=name,
        package_type='Image',
        image_uri=image_uri,
        role=role_arn,
        memory_size=memory,
        timeout=timeout,
        image_config={'commands': [cmd]},
        environment={'variables': env},
        opts=pulumi.ResourceOptions(depends_on=[log_group]),
    )
    return fn, log_group


def make_zip_handler(
    *,
    name: str,
    runtime: str,
    handler: str,
    role_arn: str,
    code: pulumi.AssetArchive,
    memory: int,
    timeout: int,
    env: pulumi.Input[dict],
    retention_in_days: int = 14,
) -> tuple[aws.lambda_.Function, aws.cloudwatch.LogGroup]:
    """ZIP-based Lambda + pre-created log group. Use for stdlib-only
    utility Lambdas where cold-start latency matters."""
    log_group = aws.cloudwatch.LogGroup(
        f'{name}-logs',
        name=f'/aws/lambda/{name}',
        retention_in_days=retention_in_days,
    )
    fn = aws.lambda_.Function(
        name,
        name=name,
        runtime=runtime,
        handler=handler,
        role=role_arn,
        timeout=timeout,
        memory_size=memory,
        code=code,
        environment={'variables': env},
        opts=pulumi.ResourceOptions(depends_on=[log_group]),
    )
    return fn, log_group
