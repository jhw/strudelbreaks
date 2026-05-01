"""ACM cert + Route 53 alias + API Gateway custom-domain mapping.

End-to-end "give the API a friendly hostname" wiring. Caller supplies
the domain + hosted zone ID; this emits the cert (DNS-validated in the
same region as the API), the validation record, the ACM validation
wait, the API Gateway DomainName + ApiMapping, and the A-alias record
that points the domain at the regional endpoint.

Region note: HTTP APIs use *regional* custom domains, so the cert
lives in the same region as the API. (Only REST APIs with EDGE
endpoints need a us-east-1 cert.)

Returns the public URL as `https://<domain_name>` so the caller can
export it.
"""
from __future__ import annotations

import pulumi
import pulumi_aws as aws


def attach_custom_domain(
    *,
    name_prefix: str,
    api: aws.apigatewayv2.Api,
    stage: aws.apigatewayv2.Stage,
    domain_name: str,
    hosted_zone_id: str,
) -> pulumi.Output[str]:
    cert = aws.acm.Certificate(
        f'{name_prefix}-cert',
        domain_name=domain_name,
        validation_method='DNS',
    )

    # `domain_validation_options` is an Output[list]; index [0] inside
    # an apply is fine because we only ever produce one cert per call.
    validation_record = aws.route53.Record(
        f'{name_prefix}-cert-validation',
        zone_id=hosted_zone_id,
        name=cert.domain_validation_options[0].resource_record_name,
        type=cert.domain_validation_options[0].resource_record_type,
        records=[cert.domain_validation_options[0].resource_record_value],
        ttl=60,
        allow_overwrite=True,
    )

    cert_validation = aws.acm.CertificateValidation(
        f'{name_prefix}-cert-validation-wait',
        certificate_arn=cert.arn,
        validation_record_fqdns=[validation_record.fqdn],
    )

    api_domain = aws.apigatewayv2.DomainName(
        f'{name_prefix}-domain',
        domain_name=domain_name,
        domain_name_configuration={
            'certificate_arn': cert_validation.certificate_arn,
            'endpoint_type': 'REGIONAL',
            'security_policy': 'TLS_1_2',
        },
    )

    aws.apigatewayv2.ApiMapping(
        f'{name_prefix}-mapping',
        api_id=api.id,
        domain_name=api_domain.id,
        stage=stage.id,
    )

    aws.route53.Record(
        f'{name_prefix}-domain-record',
        zone_id=hosted_zone_id,
        name=domain_name,
        type='A',
        aliases=[{
            'name': api_domain.domain_name_configuration.target_domain_name,
            'zone_id': api_domain.domain_name_configuration.hosted_zone_id,
            'evaluate_target_health': False,
        }],
    )

    return pulumi.Output.concat('https://', domain_name)
