"""ChatOps-for-Lambda: error-notifier + per-Lambda subscription filters.

The full pattern from
`../outboard-brain/posts/markdown/chatops-for-lambda.md` packaged as a
single function. Caller supplies a dict of `{key: log_group}` for the
log groups to subscribe; this module wires up:

  - a small ZIP-based notifier Lambda (stdlib-only — sub-500ms cold
    start matters on the alerting path)
  - its own IAM role at `/app/` with `DeveloperBoundary` attached
  - basic CloudWatch-Logs execution policy
  - an `aws.lambda_.Permission` so CloudWatch Logs can invoke it
  - one `aws.cloudwatch.LogSubscriptionFilter` per supplied log group
    matching `?ERROR ?Exception ?Traceback ?"Task timed out"`

The notifier's OWN log group is deliberately not subscribed —
otherwise a Slack-post failure that logged ERROR would loop forever.

Returns the notifier function so the caller can export its ARN.
"""
from __future__ import annotations

import json
import pathlib

import pulumi
import pulumi_aws as aws

from . import handlers as handlers_module


ERROR_FILTER_PATTERN = '?ERROR ?Exception ?Traceback ?"Task timed out"'


def attach_error_notifier(
    *,
    name_prefix: str,
    handler_path: pathlib.Path,
    log_groups: dict[str, aws.cloudwatch.LogGroup],
    slack_webhook_url: pulumi.Input[str | None],
    account_id: str,
) -> aws.lambda_.Function:
    notifier_name = f'{name_prefix}-error-notifier'
    boundary_arn = f'arn:aws:iam::{account_id}:policy/DeveloperBoundary'

    role = aws.iam.Role(
        f'{notifier_name}-role',
        name=notifier_name,
        path='/app/',
        permissions_boundary=boundary_arn,
        assume_role_policy=json.dumps({
            'Version': '2012-10-17',
            'Statement': [{
                'Effect': 'Allow',
                'Principal': {'Service': 'lambda.amazonaws.com'},
                'Action': 'sts:AssumeRole',
            }],
        }),
        opts=pulumi.ResourceOptions(delete_before_replace=True),
    )

    aws.iam.RolePolicyAttachment(
        f'{notifier_name}-basic-execution',
        role=role.name,
        policy_arn='arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole',
    )

    env = pulumi.Output.from_input(slack_webhook_url).apply(
        lambda url: {'SLACK_WEBHOOK_URL': url or ''}
    )

    fn, _log_group = handlers_module.make_zip_handler(
        name=notifier_name,
        runtime='python3.12',
        handler='handler.handler',
        role_arn=role.arn,
        code=pulumi.AssetArchive({
            'handler.py': pulumi.FileAsset(str(handler_path)),
        }),
        memory=128,
        timeout=10,
        env=env,
    )

    perm = aws.lambda_.Permission(
        f'{notifier_name}-logs-perm',
        action='lambda:InvokeFunction',
        function=fn.name,
        principal='logs.amazonaws.com',
        source_account=account_id,
    )

    for key, log_group in log_groups.items():
        aws.cloudwatch.LogSubscriptionFilter(
            f'{name_prefix}-{key}-error-sub',
            name=f'{name_prefix}-{key}-errors',
            log_group=log_group.name,
            filter_pattern=ERROR_FILTER_PATTERN,
            destination_arn=fn.arn,
            opts=pulumi.ResourceOptions(depends_on=[perm]),
        )

    return fn
