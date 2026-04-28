#!/usr/bin/env python3
"""Three-step deploy:

  1. `pulumi up` on infra/pipeline (idempotent — almost always no-op).
  2. SHA-256 the source + Dockerfile. If unchanged vs. the marker in
     S3, skip the build. Otherwise upload source.zip to the artifacts
     bucket, kick off CodeBuild, stream logs, capture the resulting
     image digest, write the marker.
  3. `pulumi up` on infra/app with the new image URI as config.

Stage selector: `--stage dev` / `--stage prod` selects which Pulumi
stack file to use. `AWS_REGION` and `AUTH_TOKEN` (and `AUTH_TOKEN_*`
overrides) come from `config/setenv.sh`.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import subprocess
import sys
import time
import zipfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PIPELINE_DIR = REPO_ROOT / 'infra' / 'pipeline'
APP_DIR = REPO_ROOT / 'infra' / 'app'

# Files that go into source.zip (and into the content hash).
SOURCE_GLOB = ['app', 'docker', 'requirements.txt']


def run(cmd: list[str], *, cwd: pathlib.Path | None = None,
        env: dict | None = None, capture: bool = False) -> str:
    print('+', ' '.join(cmd), file=sys.stderr)
    if capture:
        out = subprocess.check_output(cmd, cwd=cwd, env=env, text=True)
        return out
    subprocess.check_call(cmd, cwd=cwd, env=env)
    return ''


def pulumi_output(stack_dir: pathlib.Path, stack: str, key: str) -> str:
    return run(
        ['pulumi', 'stack', 'output', '--stack', stack, key, '--show-secrets'],
        cwd=stack_dir, capture=True,
    ).strip()


def pulumi_up(stack_dir: pathlib.Path, stack: str) -> None:
    run(
        ['pulumi', 'up', '--stack', stack, '--yes', '--non-interactive'],
        cwd=stack_dir,
    )


def hash_source() -> str:
    """SHA-256 over every file under SOURCE_GLOB. Order-stable so the
    same content produces the same hash regardless of how os.walk
    happens to traverse on a given run."""
    h = hashlib.sha256()
    for root in SOURCE_GLOB:
        base = REPO_ROOT / root
        if base.is_file():
            files = [base]
        else:
            files = sorted(p for p in base.rglob('*') if p.is_file())
        for p in files:
            rel = p.relative_to(REPO_ROOT).as_posix()
            h.update(rel.encode())
            h.update(b'\0')
            h.update(p.read_bytes())
            h.update(b'\0')
    return h.hexdigest()


def build_source_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for root in SOURCE_GLOB:
            base = REPO_ROOT / root
            if base.is_file():
                z.write(base, base.relative_to(REPO_ROOT).as_posix())
                continue
            for p in sorted(base.rglob('*')):
                if not p.is_file():
                    continue
                z.write(p, p.relative_to(REPO_ROOT).as_posix())
    return buf.getvalue()


def s3_get_text(bucket: str, key: str) -> str | None:
    import boto3
    from botocore.exceptions import ClientError
    s3 = boto3.client('s3')
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response['Error']['Code'] in ('NoSuchKey', '404'):
            return None
        raise
    return obj['Body'].read().decode('utf-8')


def s3_put_bytes(bucket: str, key: str, body: bytes) -> None:
    import boto3
    boto3.client('s3').put_object(Bucket=bucket, Key=key, Body=body)


def trigger_codebuild(project: str) -> str:
    import boto3
    cb = boto3.client('codebuild')
    out = cb.start_build(projectName=project)
    return out['build']['id']


def wait_for_build(build_id: str) -> dict:
    import boto3
    cb = boto3.client('codebuild')
    print(f'Waiting on CodeBuild {build_id} ...', file=sys.stderr)
    while True:
        time.sleep(10)
        info = cb.batch_get_builds(ids=[build_id])['builds'][0]
        status = info['buildStatus']
        print(f'  status={status} phase={info.get("currentPhase")}', file=sys.stderr)
        if status not in ('IN_PROGRESS',):
            return info


def build_digest_from_info(info: dict) -> str | None:
    """CodeBuild surfaces our exported IMAGE_DIGEST under
    `exportedEnvironmentVariables`."""
    for env in info.get('exportedEnvironmentVariables') or []:
        if env.get('name') == 'IMAGE_DIGEST':
            return env.get('value')
    return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--stage', default='dev')
    p.add_argument('--force-build', action='store_true',
                   help='rebuild even if the source hash is unchanged')
    args = p.parse_args()

    stack = args.stage
    print(f'== infra/pipeline ({stack}) ==', file=sys.stderr)
    pulumi_up(PIPELINE_DIR, stack)

    artifacts_bucket = pulumi_output(PIPELINE_DIR, stack, 'artifacts_bucket')
    codebuild_project = pulumi_output(PIPELINE_DIR, stack, 'codebuild_project')
    ecr_repo_url = pulumi_output(PIPELINE_DIR, stack, 'ecr_repo_url')
    lambda_role_arn = pulumi_output(PIPELINE_DIR, stack, 'lambda_role_arn')
    oneshot_s3_uri = pulumi_output(PIPELINE_DIR, stack, 'oneshot_s3_uri')

    digest_marker_key = f'markers/{stack}/last-build.json'
    src_hash = hash_source()
    print(f'source hash: {src_hash}', file=sys.stderr)

    cached = s3_get_text(artifacts_bucket, digest_marker_key)
    cached_marker = json.loads(cached) if cached else None
    if (not args.force_build
            and cached_marker
            and cached_marker.get('source_hash') == src_hash
            and cached_marker.get('image_digest')):
        digest = cached_marker['image_digest']
        print(f'Source unchanged — reusing image digest {digest}', file=sys.stderr)
    else:
        print('== build ==', file=sys.stderr)
        zip_bytes = build_source_zip()
        s3_put_bytes(artifacts_bucket, 'source.zip', zip_bytes)
        build_id = trigger_codebuild(codebuild_project)
        info = wait_for_build(build_id)
        if info['buildStatus'] != 'SUCCEEDED':
            print(f'Build failed: {info["buildStatus"]}', file=sys.stderr)
            return 1
        digest = build_digest_from_info(info)
        if not digest:
            print('Build succeeded but no IMAGE_DIGEST was exported', file=sys.stderr)
            return 1
        s3_put_bytes(
            artifacts_bucket, digest_marker_key,
            json.dumps({
                'source_hash': src_hash,
                'image_digest': digest,
                'build_id': build_id,
            }).encode(),
        )
        print(f'Built image digest: {digest}', file=sys.stderr)

    image_uri = f'{ecr_repo_url}@{digest}'

    print(f'== infra/app ({stack}) ==', file=sys.stderr)
    run(['pulumi', 'config', 'set', '--stack', stack, 'image_uri', image_uri],
        cwd=APP_DIR)
    run(['pulumi', 'config', 'set', '--stack', stack, 'lambda_role_arn',
         lambda_role_arn], cwd=APP_DIR)
    run(['pulumi', 'config', 'set', '--stack', stack, 'oneshot_s3_uri',
         oneshot_s3_uri], cwd=APP_DIR)
    auth = os.environ.get('AUTH_TOKEN')
    if auth:
        run(['pulumi', 'config', 'set', '--stack', stack, '--secret',
             'auth_token', auth], cwd=APP_DIR)
    pulumi_up(APP_DIR, stack)

    api_endpoint = pulumi_output(APP_DIR, stack, 'api_endpoint')
    print(f'API endpoint: {api_endpoint}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
