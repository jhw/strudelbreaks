"""App stack: five container Lambdas + a sixth ZIP-based error notifier
behind one HTTP API, optionally fronted by a custom domain.

All container Lambdas run from the same image (digest comes from
`pulumi config get image_uri`, written by the deploy script). Each
Lambda's CMD picks its own entry point. Memory/timeout track the
per-handler sizing in docs/planning/aws-deploy.md.

Routes:
  POST /api/export/strudel   → strudel handler  (text)
  POST /api/export/ot-basic  → ot_basic handler (binary)
  POST /api/export/ot-doom   → ot_doom handler  (binary)
  POST /api/export/torso-s4  → torso_s4 handler (binary)
  GET  /launch               → launch handler   (302 to strudel.cc)

Plus a CloudWatch Logs subscription filter on every application
Lambda's log group that fans out
`?ERROR ?Exception ?Traceback ?"Task timed out"` matches to the
`error-notifier` Lambda — chatops-for-lambda pattern.

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

import pathlib

import pulumi
import pulumi_aws as aws

from modules import api as api_mod
from modules import api_routes
from modules import chatops
from modules import custom_domain as custom_domain_mod
from modules import handlers as handlers_mod


config = pulumi.Config()
project = pulumi.get_project()
stack = pulumi.get_stack()
name_prefix = f'{project}-{stack}'

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

image_uri = config.require('image_uri')
lambda_role_arn = config.require('lambda_role_arn')
oneshot_s3_uri = config.require('oneshot_s3_uri')
artifacts_bucket = config.require('artifacts_bucket')
auth_token = config.require_secret('auth_token')
slack_webhook_url = config.get_secret('slack_webhook_url')

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
TMP_DIR = '/tmp'

# Per-handler sizing — see docs/planning/aws-deploy.md §"Per-Lambda sizing".
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
        # LAUNCH_DEFAULTS_S3_URI points at the single S3 key the
        # handler reads/writes to remember query-string args across
        # visits — see app/api/launch/handler.py.
        'env': {
            'LAUNCH_GIST_USER': launch_gist_user,
            'LAUNCH_GIST_ID':   launch_gist_id,
            'LAUNCH_BPM':       launch_bpm,
            'LAUNCH_SEED':      launch_seed,
            'LAUNCH_DEFAULTS_S3_URI':
                f's3://{artifacts_bucket}/launch-defaults/global.json',
        },
    },
]


def _build_env(extra: dict) -> pulumi.Output[dict]:
    """Merge the per-handler `extra` env dict (with empty values
    dropped) on top of the base env every Lambda carries."""
    extra_filtered = {k: v for k, v in (extra or {}).items() if v}
    return pulumi.Output.all(
        oneshot=oneshot_s3_uri, token=auth_token,
    ).apply(lambda v: {
        'ONESHOT_S3_URI': v['oneshot'],
        'STRUDELBREAKS_TMP': TMP_DIR,
        'AUTH_TOKEN': v['token'],
        **extra_filtered,
    })


# --- HTTP API + Lambdas ----------------------------------------------------

api, stage = api_mod.create_http_api(
    name=name_prefix, allowed_origins=ALLOWED_ORIGINS,
    allowed_methods=['GET', 'POST', 'OPTIONS'],
)

lambdas: dict[str, aws.lambda_.Function] = {}
log_groups: dict[str, aws.cloudwatch.LogGroup] = {}

for spec in HANDLERS:
    fn_name = f'{name_prefix}-{spec["key"]}'
    fn, log_group = handlers_mod.make_container_handler(
        name=fn_name,
        image_uri=image_uri,
        role_arn=lambda_role_arn,
        cmd=spec['cmd'],
        memory=spec['memory'],
        timeout=spec['timeout'],
        env=_build_env(spec.get('env')),
    )
    lambdas[spec['key']] = fn
    log_groups[spec['key']] = log_group
    api_routes.attach_route(
        name_prefix=fn_name, api=api, function=fn,
        route_key=spec['route_key'],
    )


# --- ChatOps error notifier ------------------------------------------------

notifier_fn = chatops.attach_error_notifier(
    name_prefix=name_prefix,
    handler_path=REPO_ROOT / 'app' / 'api' / 'error_notifier' / 'handler.py',
    log_groups=log_groups,
    slack_webhook_url=slack_webhook_url,
    account_id=aws.get_caller_identity().account_id,
)


# --- Optional custom domain ------------------------------------------------

custom_domain_url: pulumi.Output[str] | None = None
if domain_name and hosted_zone_id:
    custom_domain_url = custom_domain_mod.attach_custom_domain(
        name_prefix=name_prefix, api=api, stage=stage,
        domain_name=domain_name, hosted_zone_id=hosted_zone_id,
    )


# --- Exports ---------------------------------------------------------------

pulumi.export('api_endpoint', api.api_endpoint)
pulumi.export('lambda_arns', {k: fn.arn for k, fn in lambdas.items()})
pulumi.export('error_notifier_arn', notifier_fn.arn)
if custom_domain_url is not None:
    pulumi.export('custom_domain_url', custom_domain_url)
