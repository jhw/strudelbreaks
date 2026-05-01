"""HTTP API + default $stage.

Caller passes the same `name` (typically `f'{project}-{stack}'`) that
the inline code used to use, so URNs stay stable. `allow_origins` /
`allow_methods` are caller-supplied because the export API has a
tighter origin allowlist (just `https://strudel.cc`) than a typical
SPA-fronted API.
"""
from __future__ import annotations

import pulumi_aws as aws


def create_http_api(
    *,
    name: str,
    allowed_origins: list[str],
    allowed_methods: list[str] | None = None,
) -> tuple[aws.apigatewayv2.Api, aws.apigatewayv2.Stage]:
    methods = allowed_methods or ['GET', 'POST', 'OPTIONS']
    api = aws.apigatewayv2.Api(
        f'{name}-api',
        name=name,
        protocol_type='HTTP',
        cors_configuration={
            'allow_origins': allowed_origins,
            'allow_methods': methods,
            'allow_headers': ['Authorization', 'Content-Type'],
            'expose_headers': ['Content-Disposition'],
            'max_age': 600,
        },
    )
    stage = aws.apigatewayv2.Stage(
        f'{name}-stage',
        api_id=api.id,
        name='$default',
        auto_deploy=True,
    )
    return api, stage
