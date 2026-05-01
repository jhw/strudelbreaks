"""App stack: five container Lambdas behind one HTTP API, optionally
fronted by a custom domain.

All Lambdas run from the same image (digest comes from `pulumi config
get image_uri`, written by the deploy script). Each Lambda's CMD picks
its own entry point. Memory/timeout track the per-handler sizing in
docs/planning/aws-deploy.md.

Routes:
  POST /api/export/strudel   → strudel handler  (text)
  POST /api/export/ot-basic  → ot_basic handler (binary)
  POST /api/export/ot-doom   → ot_doom handler  (binary)
  POST /api/export/torso-s4  → torso_s4 handler (binary)
  GET  /launch               → launch handler   (302 to strudel.cc)

Auth: AUTH_TOKEN env var (HTTP Basic) gates the four export routes.
The launch route is public — its handler never inspects the auth
header. Bucket: `oneshot_s3_uri` flows through to ONESHOT_S3_URI.

Custom domain (optional): set `domain_name` + `hosted_zone_id` in the
stack config and an ACM cert (DNS-validated in the same region) gets
provisioned, an API Gateway DomainName + ApiMapping bind the cert to
the HTTP API, and a Route 53 A-alias record points the domain at the
regional endpoint. Skip both config keys to deploy without one.
"""
from __future__ import annotations

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

# Optional custom domain. Both must be set together; both unset means
# we publish only the *.execute-api.<region>.amazonaws.com endpoint.
domain_name = config.get('domain_name')
hosted_zone_id = config.get('hosted_zone_id')
if bool(domain_name) != bool(hosted_zone_id):
    raise ValueError(
        'domain_name and hosted_zone_id must be set together '
        '(or both omitted)'
    )

# Per-environment defaults the launch handler bakes into
# tempera.strudel.js when it serves /launch. All four are optional —
# unset values fall through to whatever's in the committed file.
launch_gist_user = config.get('launch_gist_user') or ''
launch_gist_id = config.get('launch_gist_id') or ''
launch_bpm = config.get('launch_bpm') or ''
launch_seed = config.get('launch_seed') or ''

# Origin allowlist for cross-origin fetches from tempera. The launch
# route is a top-level GET (no CORS preflight), so this only really
# gates the four /api/export/* POSTs — which are only ever called
# from a tempera instance running on strudel.cc.
ALLOWED_ORIGINS = ['https://strudel.cc']

LOG_RETENTION_DAYS = 14
TMP_DIR = '/tmp'

# Per-handler sizing — see docs/planning/aws-deploy.md §"Per-Lambda sizing".
# `route_key` is the API Gateway HTTP API "METHOD /path" form.
HANDLERS = [
    {
        'key': 'strudel',
        'route_key': 'POST /api/export/strudel',
        'cmd': 'app.api.strudel.handler.handler',
        'memory': 256,
        'timeout': 30,
    },
    {
        'key': 'ot-basic',
        'route_key': 'POST /api/export/ot-basic',
        'cmd': 'app.api.ot_basic.handler.handler',
        'memory': 3008,
        'timeout': 120,
    },
    {
        'key': 'ot-doom',
        'route_key': 'POST /api/export/ot-doom',
        'cmd': 'app.api.ot_doom.handler.handler',
        'memory': 3008,
        'timeout': 120,
    },
    {
        'key': 'torso-s4',
        'route_key': 'POST /api/export/torso-s4',
        'cmd': 'app.api.torso_s4.handler.handler',
        'memory': 3008,
        'timeout': 120,
    },
    {
        'key': 'launch',
        'route_key': 'GET /launch',
        'cmd': 'app.api.launch.handler.handler',
        'memory': 256,
        'timeout': 10,
        # Per-handler env merged on top of the base env every Lambda
        # carries (AUTH_TOKEN, ONESHOT_S3_URI, STRUDELBREAKS_TMP).
        # Empty values are dropped so we don't ship `LAUNCH_BPM=`.
        'env': {
            'LAUNCH_GIST_USER': launch_gist_user,
            'LAUNCH_GIST_ID':   launch_gist_id,
            'LAUNCH_BPM':       launch_bpm,
            'LAUNCH_SEED':      launch_seed,
        },
    },
]


api = aws.apigatewayv2.Api(
    f'{project}-{stack}-api',
    name=f'{project}-{stack}',
    protocol_type='HTTP',
    cors_configuration={
        # Only tempera (running on strudel.cc) calls the export POSTs
        # cross-origin. /launch is a top-level GET so it never triggers
        # a preflight; including GET here just lets dev tools poke at
        # it from the browser console.
        'allow_origins': ALLOWED_ORIGINS,
        'allow_methods': ['GET', 'POST', 'OPTIONS'],
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
    extra_env = {k: v for k, v in (spec.get('env') or {}).items() if v}
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
                **extra_env,
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
    # AWS_PROXY integrations always invoke the Lambda over POST,
    # regardless of the *route's* HTTP method (which is captured in
    # `route_key` below — `GET /launch` for the launch route, `POST
    # /api/export/...` for the rest). Setting integration_method to
    # anything other than POST trips API Gateway's "HttpMethod must be
    # POST for AWS_PROXY" validator.
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
        route_key=spec['route_key'],
        target=integration.id.apply(lambda i: f'integrations/{i}'),
    )
    return fn


lambdas = {spec['key']: _make_lambda(spec) for spec in HANDLERS}


# --- Optional custom domain ---
#
# ACM cert is DNS-validated against the configured Route 53 zone.
# The cert lives in the same region as the API because HTTP APIs use
# regional custom domains (only REST APIs with EDGE endpoints need
# us-east-1). DomainName + ApiMapping bind the cert to the API; an
# A-alias record in the zone points the domain at the regional
# endpoint AWS provisions for the DomainName.
custom_domain_url: pulumi.Output[str] | None = None
if domain_name and hosted_zone_id:
    cert = aws.acm.Certificate(
        f'{project}-{stack}-cert',
        domain_name=domain_name,
        validation_method='DNS',
    )

    # Pulumi's `domain_validation_options` is an Output[list]; index [0]
    # via apply because we're creating exactly one cert (one domain).
    validation_record = aws.route53.Record(
        f'{project}-{stack}-cert-validation',
        zone_id=hosted_zone_id,
        name=cert.domain_validation_options[0].resource_record_name,
        type=cert.domain_validation_options[0].resource_record_type,
        records=[cert.domain_validation_options[0].resource_record_value],
        ttl=60,
        allow_overwrite=True,
    )

    cert_validation = aws.acm.CertificateValidation(
        f'{project}-{stack}-cert-validation-wait',
        certificate_arn=cert.arn,
        validation_record_fqdns=[validation_record.fqdn],
    )

    api_domain = aws.apigatewayv2.DomainName(
        f'{project}-{stack}-domain',
        domain_name=domain_name,
        domain_name_configuration={
            'certificate_arn': cert_validation.certificate_arn,
            'endpoint_type': 'REGIONAL',
            'security_policy': 'TLS_1_2',
        },
    )

    aws.apigatewayv2.ApiMapping(
        f'{project}-{stack}-mapping',
        api_id=api.id,
        domain_name=api_domain.id,
        stage=stage.id,
    )

    aws.route53.Record(
        f'{project}-{stack}-domain-record',
        zone_id=hosted_zone_id,
        name=domain_name,
        type='A',
        aliases=[{
            'name': api_domain.domain_name_configuration.target_domain_name,
            'zone_id': api_domain.domain_name_configuration.hosted_zone_id,
            'evaluate_target_health': False,
        }],
    )

    custom_domain_url = pulumi.Output.concat('https://', domain_name)


pulumi.export('api_endpoint', api.api_endpoint)
pulumi.export('lambda_arns', {k: fn.arn for k, fn in lambdas.items()})
if custom_domain_url is not None:
    pulumi.export('custom_domain_url', custom_domain_url)
