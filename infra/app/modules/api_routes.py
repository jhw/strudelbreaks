"""Wire one API Gateway HTTP-API route to a Lambda function.

Strudelbreaks runs one route per Lambda, so the URN scheme has no
per-route slug suffix. (Compare picobeats, where the api Lambda hosts
~10 routes and needs a slug discriminator.)

Each route emits three resources:

  - aws.lambda_.Permission     — lets API Gateway invoke the Lambda
  - aws.apigatewayv2.Integration — AWS_PROXY against the Lambda
  - aws.apigatewayv2.Route       — `METHOD /path` → the integration

`AWS_PROXY` integrations always invoke Lambda over POST regardless of
the route's actual HTTP method — setting `integration_method` to
anything else trips API Gateway's own validator.
"""
from __future__ import annotations

import pulumi
import pulumi_aws as aws


def attach_route(
    *,
    name_prefix: str,
    api: aws.apigatewayv2.Api,
    function: aws.lambda_.Function,
    route_key: str,
) -> None:
    aws.lambda_.Permission(
        f'{name_prefix}-apigw',
        action='lambda:InvokeFunction',
        function=function.name,
        principal='apigateway.amazonaws.com',
        source_arn=pulumi.Output.concat(api.execution_arn, '/*/*'),
    )
    integration = aws.apigatewayv2.Integration(
        f'{name_prefix}-integration',
        api_id=api.id,
        integration_type='AWS_PROXY',
        integration_method='POST',
        integration_uri=function.invoke_arn,
        payload_format_version='2.0',
    )
    aws.apigatewayv2.Route(
        f'{name_prefix}-route',
        api_id=api.id,
        route_key=route_key,
        target=integration.id.apply(lambda i: f'integrations/{i}'),
    )
