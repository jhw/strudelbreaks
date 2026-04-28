"""App stack: four container Lambdas (one per export target) behind a
single HTTP API.

All four Lambdas run from the same image (the digest comes from
`pulumi config get image_uri`, written by the deploy script). Each
Lambda's CMD picks its own entry point. Memory/timeout track the
per-handler sizing in docs/planning/aws-deploy.md.

Routes:
  POST /api/export/strudel   → strudel handler
  POST /api/export/ot-basic  → ot_basic handler
  POST /api/export/ot-doom   → ot_doom handler
  POST /api/export/torso-s4  → torso_s4 handler

Auth: AUTH_TOKEN env var on every Lambda (HTTP Basic). Bucket: the
`oneshot_s3_uri` from the pipeline stack flows through to ONESHOT_S3_URI.
"""
from __future__ import annotations

import json

import pulumi
import pulumi_aws as aws


config = pulumi.Config()
project = pulumi.get_project()
stack = pulumi.get_stack()

# Image digest written by scripts/stack/deploy.py after CodeBuild
# finishes. Always a digest (sha256:...), never a mutable tag, so a
# rollback is `pulumi config set image_uri <prev-digest> && pulumi up`.
image_uri = config.require('image_uri')

# Pipeline-stack outputs needed here. We require them explicitly via
# `pulumi config set` rather than a StackReference so the two stacks
# can live in separate Pulumi backends if anyone ever wants that.
lambda_role_arn = config.require('lambda_role_arn')
oneshot_s3_uri = config.require('oneshot_s3_uri')

# Auth token in the form "user:password". `--secret` recommended.
auth_token = config.require_secret('auth_token')

LOG_RETENTION_DAYS = 14
TMP_DIR = '/tmp'

# Per-handler sizing — see docs/planning/aws-deploy.md §"Per-Lambda sizing".
HANDLERS = [
    {
        'key': 'strudel',
        'route': 'strudel',
        'cmd': 'app.api.strudel.handler.handler',
        'memory': 256,
        'timeout': 30,
    },
    {
        'key': 'ot-basic',
        'route': 'ot-basic',
        'cmd': 'app.api.ot_basic.handler.handler',
        'memory': 3008,
        'timeout': 120,
    },
    {
        'key': 'ot-doom',
        'route': 'ot-doom',
        'cmd': 'app.api.ot_doom.handler.handler',
        'memory': 3008,
        'timeout': 120,
    },
    {
        'key': 'torso-s4',
        'route': 'torso-s4',
        'cmd': 'app.api.torso_s4.handler.handler',
        'memory': 3008,
        'timeout': 120,
    },
]


api = aws.apigatewayv2.Api(
    f'{project}-{stack}-api',
    name=f'{project}-{stack}',
    protocol_type='HTTP',
    cors_configuration={
        'allow_origins': ['*'],
        'allow_methods': ['POST', 'OPTIONS'],
        'allow_headers': ['Authorization', 'Content-Type'],
        'expose_headers': ['Content-Disposition'],
        'max_age': 600,
    },
)

stage = aws.apigatewayv2.Stage(
    f'{project}-{stack}-stage',
    api_id=api.id,
    name='$default',
    auto_deploy=True,
)


def _make_lambda(spec: dict) -> aws.lambda_.Function:
    fn_name = f'{project}-{stack}-{spec["key"]}'
    log_group = aws.cloudwatch.LogGroup(
        f'{fn_name}-logs',
        name=f'/aws/lambda/{fn_name}',
        retention_in_days=LOG_RETENTION_DAYS,
    )
    fn = aws.lambda_.Function(
        fn_name,
        name=fn_name,
        package_type='Image',
        image_uri=image_uri,
        role=lambda_role_arn,
        memory_size=spec['memory'],
        timeout=spec['timeout'],
        image_config={'commands': [spec['cmd']]},
        environment={
            'variables': pulumi.Output.all(
                oneshot=oneshot_s3_uri, token=auth_token,
            ).apply(lambda v: {
                'ONESHOT_S3_URI': v['oneshot'],
                'STRUDELBREAKS_TMP': TMP_DIR,
                'AUTH_TOKEN': v['token'],
            }),
        },
        opts=pulumi.ResourceOptions(depends_on=[log_group]),
    )
    aws.lambda_.Permission(
        f'{fn_name}-apigw',
        action='lambda:InvokeFunction',
        function=fn.name,
        principal='apigateway.amazonaws.com',
        source_arn=pulumi.Output.concat(api.execution_arn, '/*/*'),
    )
    integration = aws.apigatewayv2.Integration(
        f'{fn_name}-integration',
        api_id=api.id,
        integration_type='AWS_PROXY',
        integration_method='POST',
        integration_uri=fn.invoke_arn,
        payload_format_version='2.0',
    )
    aws.apigatewayv2.Route(
        f'{fn_name}-route',
        api_id=api.id,
        route_key=f'POST /api/export/{spec["route"]}',
        target=integration.id.apply(lambda i: f'integrations/{i}'),
    )
    return fn


lambdas = {spec['key']: _make_lambda(spec) for spec in HANDLERS}

pulumi.export('api_endpoint', api.api_endpoint)
pulumi.export('lambda_arns', {k: fn.arn for k, fn in lambdas.items()})
