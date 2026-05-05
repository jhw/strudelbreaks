# Deploy strudelbreaks server to AWS Lambda (Pulumi)

Plan for replacing the FastAPI render server with raw AWS Lambda
handlers behind API Gateway, modelled wholesale on
`../outrights-mip`. Three motivations:

1. **No manual server start.** The tempera template currently fails
   if `./scripts/run.sh` isn't running. Deploying makes the endpoint
   always available.
2. **No ambient AWS credentials for S3.** Local dev pulls one-shot
   samples from `s3://wol-samplebank/samples/` via `aws s3 sync`, which
   requires the developer to be logged into AWS regularly. In Lambda,
   the function's IAM role grants the read directly — no per-laptop
   credential refresh.
3. **Drop FastAPI.** The router/middleware/Pydantic/uvicorn stack is
   only there because we needed *some* HTTP server for local dev. In
   a deployed-only world the request shape is "API Gateway event →
   handler function → API Gateway response", and FastAPI buys us
   nothing while costing image size, cold-start time, and an extra
   dependency surface.

## Reference: how outrights-mip does it

Two Pulumi stacks + a Docker image baked by CodeBuild:

```
infra/pipeline/   ECR + CodeBuild + S3 artifacts + IAM        (one-time)
docker/           Dockerfile + buildspec.yml                   (build inputs)
scripts/stack/    deploy.py — pipeline → build → app           (orchestration)
infra/app/        Lambda (container) + API Gateway HTTP API    (per-deploy)
```

`scripts/stack/deploy.py`:

1. `pulumi up` on `infra/pipeline` (idempotent — almost always no-op).
2. SHA-256 the source + Dockerfile; if unchanged vs. the hash stored
   in S3, skip the build. Otherwise upload source.zip to S3, kick
   off CodeBuild, stream logs, capture the resulting image digest.
3. `pulumi up` on `infra/app` with the new image URI as config.

Auth: HTTP Basic via an `AUTH_TOKEN` env var in the format
`username:password`; the handler decodes the `Authorization: Basic
<b64>` header and string-compares. Token is set in `config/setenv.sh`,
sourced before deploy, injected into the Lambda's environment.

Why containers and Pulumi (not ZIP + CloudFormation): the Lambda
ZIP ceiling (250 MB unpacked) is below numpy + scipy alone, and
CloudFormation can't sequence "create ECR → wait for build → create
Lambda" in one stack. Long-form rationale in
`outboard-brain/posts/markdown/lambda-container-deployments.md`.

## Why this fits strudelbreaks

The render coordinator (`app/exporters.py`) calls into `app/export/*`
which carries heavy deps:

- `beatwav` — pulls numpy + scipy (ZIP ceiling busted on its own).
- `octapy` — pure Python, but tied to the others.
- `pydub` — uses stdlib `wave`, no ffmpeg needed for our paths.

So: container Lambda. Same shape as outrights-mip. The container
also lets us drop fastapi/uvicorn/pydantic from `requirements.txt`
entirely — the only HTTP plumbing we need is the API Gateway
event/response shape, which is plain dicts.

## Where the design needs to differ

A few constraints don't carry over cleanly:

### 1. One raw Lambda per export target

Four handlers, four routes, four Lambda functions — proper
single-responsibility Lambda layout. Each export target becomes its
own deployable unit:

```
app/api/strudel/handler.py    → POST /api/export/strudel
app/api/ot_basic/handler.py   → POST /api/export/ot-basic
app/api/ot_doom/handler.py    → POST /api/export/ot-doom
app/api/torso_s4/handler.py   → POST /api/export/torso-s4
```

Per-handler shape (mirrors outrights-mip's `simulate/handler.py`):

1. Auth check (`check_auth(event)` — shared helper in `app/api/_auth.py`).
2. JSON-parse `event["body"]`.
3. Validate only the fields this target accepts. No Pydantic —
   outrights-mip's `_validate_*` pattern (one validator per field
   group, raises `ValueError`) is the model. The `target` field
   goes away from the body entirely; the route encodes the target.
   Per-target body shape:
   - **strudel**:   `payload`, optional `name`, `seed`
   - **ot-basic**:  `payload`, optional `name`, `seed`, `probability`, `flatten`
   - **ot-doom**:   `payload`, optional `name`, `seed`, `flatten`
   - **torso-s4**:  `payload`, optional `name`, `seed`, `source`
4. Call into `app.exporters.export_*` (unchanged).
5. Return `{statusCode, headers, body}`. For the three binary
   handlers: base64-encode the bytes and set `isBase64Encoded=True`
   so API Gateway hands raw bytes back to the browser. The strudel
   handler returns text directly.

Why one Lambda per target (not one fat dispatcher):

- **Independent rollback.** A bug in the ot-doom renderer doesn't
  force a redeploy of strudel. `pulumi config set ot_doom_image_uri
  <prev-digest> && pulumi up` rolls back just that function.
- **Independent memory + timeout.** `strudel` is a text template,
  ~30 s / 256 MB is plenty. The three audio renderers want
  ~120 s / 3 GB. Provisioning each separately = lower cost and
  fewer "why did the strudel export use 3 GB?" surprises.
- **Independent CloudWatch log groups.** One log stream per
  handler, easier to tail when debugging a specific target.
- **Independent IAM.** All four happen to share the same
  S3-read role today; that can stay one role attached to four
  functions. If any one target ever needs different perms (a
  separate bucket, KMS key, etc.), the per-function role
  swap-out is a one-line change.

All four handlers ship from the same container image — the image
is a self-contained bundle of `app/`, and each Lambda's
`image_config.commands` picks the right entry point
(`app.api.strudel.handler.handler`, etc.). One ECR repo, one build,
four CMD overrides at deploy time.

`app/main.py`, `app/config.py`, and `app/routes/` are deleted.
`app/exporters.py` and the entirety of `app/export/` stay as-is —
that's the actual render code, and it has zero FastAPI coupling.

`fastapi`, `uvicorn`, and `pydantic` come out of
`requirements.txt`. `mangum` doesn't go in.

### 2. S3 access for one-shot samples

Current `app/export/common/sample_source.py`:

```python
ONESHOT_S3_URI = 's3://wol-samplebank/samples/'
ONESHOT_CACHE = REPO_ROOT / 'tmp' / 'oneshots'

def ensure_oneshots_synced(verbose=False):
    if any(ONESHOT_CACHE.iterdir()):
        return ONESHOT_CACHE
    subprocess.run(['aws', 's3', 'sync', ONESHOT_S3_URI,
                    str(ONESHOT_CACHE) + '/'], check=True, ...)
    return ONESHOT_CACHE
```

Three problems for Lambda:

- `aws` CLI isn't in the base image — would have to install.
- `tmp/oneshots/` lives in the repo; in Lambda we get `/tmp` only
  (10 GB if we ask, but it's per-instance).
- The bucket URI is hard-coded.

**Plan:**

- Replace `subprocess.run(['aws', 's3', 'sync', ...])` with a small
  boto3 sync (list_objects_v2 paginator → download per object).
  boto3 is in the Lambda base image, so no extra install.
- Take the bucket URI from an env var (`ONESHOT_S3_URI`) which is
  set as a Pulumi config in `infra/app` and injected into the
  Lambda environment. **This is the user's "no code smell" fix —
  the bucket is a deploy-time argument, not a hard-coded path.**
- Cache to `/tmp/oneshots/` in Lambda; cache to
  `<repo>/tmp/oneshots/` locally. Path resolution flips on env:
  `STRUDELBREAKS_TMP=/tmp` in Lambda, defaults to repo-relative
  otherwise.
- Same pattern for `SAMPLES_CACHE` (the rendered per-break WAVs).

The IAM role created by `infra/app` grants `s3:GetObject` and
`s3:ListBucket` on the configured bucket. No more `aws sso login`.

### 3. Response size limits

Lambda sync response: 6 MB. API Gateway HTTP API: 10 MB.

Tempera-realistic exports today (1–4 rows × `|C|=4..8`) are well
under that. For `ot-doom` projects with many rows + per-stem chains
the zip could approach the limit; we'd have to either:

- (a) cap response size and 413 with a hint to use fewer rows, or
- (b) write the artifact to S3 and respond with a 5-minute presigned URL.

**Plan:** ship (a) — explicit limit, clear error — and note (b)
as a follow-up if real exports hit it. Avoids a second S3 bucket
and a second IAM grant for the v1 deploy.

### 4. Cold-start oneshot sync

First request after a cold start triggers the full S3 sync (a few
hundred MB, possibly). That's a 10–30 s cold-start penalty.

**Plan:** accept it for v1 — exports already take a few seconds, so
the first export of the day is slow but acceptable. Mitigations if
it bites:

- Bake a small "common" oneshot subset into the container image at
  build time (sub-100 MB) so the cache is warm.
- Provisioned concurrency = 1 (~$5/month) to keep one warm
  instance.
- Switch to EFS-mounted oneshots so every cold start sees the
  populated cache.

### 5. Output filename via `Content-Disposition`

The current responses set `Content-Disposition: attachment;
filename="..."`. API Gateway HTTP API forwards response headers
verbatim, so this should pass through unchanged. Verify in a smoke
test post-deploy.

### 6. Auth

**Plan:** HTTP Basic via `AUTH_TOKEN` env var, same as
outrights-mip. Per-handler check (no middleware to share — each
Lambda runs the same five lines):

```python
# Common helper used by both handlers (app/api/_auth.py).
import base64, os

def check_auth(event):
    expected = os.environ.get("AUTH_TOKEN")
    if not expected:
        return True  # auth disabled (local dev)
    headers = event.get("headers") or {}
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth.startswith("Basic "):
        return False
    try:
        return base64.b64decode(auth[6:]).decode("utf-8") == expected
    except Exception:
        return False
```

Each handler's first line:

```python
if not check_auth(event):
    return {"statusCode": 401,
            "headers": {"WWW-Authenticate": "Basic"},
            "body": "Unauthorized"}
```

Tempera-side: send `Authorization: Basic <b64>` on every export
POST. The credentials live in tempera's `localStorage` (one-time
prompt on first export, optionally with "remember me"); never in
the script source on jsDelivr. Or simpler v1: an inline constant
the user pastes in once and accepts the friction.

CORS: API Gateway HTTP API handles the preflight (allow `*`,
allow `Authorization` + `Content-Type` headers, allow `POST` +
`OPTIONS`). Same config block as outrights-mip's
`infra/app/__main__.py`.

### 7. Config that becomes Pulumi-managed

| Setting | Today | After deploy |
|---|---|---|
| Bucket name | `s3://wol-samplebank/samples/` (hard-coded) | `pulumi config set onesh ot_s3_uri ...`; `ONESHOT_S3_URI` env var on Lambda |
| Auth token | None (loopback) | `pulumi config set --secret auth_token ...`; `AUTH_TOKEN` env var on Lambda |
| Sample-rate constants | `app/export/common/devices.py` | unchanged — same in container |
| Tmp dir | `<repo>/tmp/` | `STRUDELBREAKS_TMP=/tmp` env var on Lambda |

Multiple buckets work the same way: separate config keys, separate
env vars, separate IAM grant statements per bucket. Today there's
just one; the structure supports more without code changes.

## Stack layout (proposed)

```
infra/pipeline/
  Pulumi.yaml
  Pulumi.strudelbreaks.yaml
  __main__.py              ECR + CodeBuild + S3 artifacts + IAM (lambda role)
  modules/
    artifacts.py
    registry.py
    lambda_iam.py          ← grants s3:GetObject on configured bucket
    codebuild_iam.py
    build_pipeline.py

infra/app/
  Pulumi.yaml
  Pulumi.strudelbreaks.yaml
  __main__.py              Four Lambda functions (one per export target),
                           one HTTP API, four routes, CORS, IAM permissions

docker/
  Dockerfile               public.ecr.aws/lambda/python:3.12 base
                           + beatwav (numpy/scipy) + octapy + pydub + app/
                           One image, four CMD entry points (set per-Lambda
                           via image_config.commands).
  buildspec.yml            ECR login + cache + build + push

scripts/stack/
  deploy.py                hash-or-build-or-skip orchestration
  smoke.py                 zero-arg smoke test against deployed dev stack

config/
  setenv.sh                AWS_REGION, AUTH_TOKEN, --stage args

app/                       (refactored)
  api/
    _auth.py               check_auth(event) helper, shared by all 4 handlers
    strudel/handler.py     POST /api/export/strudel
    ot_basic/handler.py    POST /api/export/ot-basic
    ot_doom/handler.py     POST /api/export/ot-doom
    torso_s4/handler.py    POST /api/export/torso-s4
  exporters.py             unchanged — render coordinator
  export/                  unchanged — per-target render code
                           (just sample_source.py reworked for boto3 + env paths)
```

Per-Lambda sizing (rough first cut, tune from CloudWatch):

| Handler | Memory | Timeout | Notes |
|---|---|---|---|
| `strudel`   | 256 MB  | 30 s  | text template, no audio |
| `ot-basic`  | 3 GB    | 120 s | beatwav per-stem render |
| `ot-doom`   | 3 GB    | 120 s | beatwav per-stem + chain build |
| `torso-s4`  | 3 GB    | 120 s | beatwav mixed render at 96 kHz |

Files removed: `app/main.py`, `app/config.py`, `app/routes/`,
`scripts/run.sh`. requirements.txt loses fastapi / uvicorn /
pydantic.

## Local dev

FastAPI / uvicorn / `./scripts/run.sh` go away. The deployed Lambda
becomes the only runtime. For offline dev (or pre-deploy debugging)
we have two options:

- **(A) Direct handler invocation.** A small `tools/serve_local.py`
  that uses stdlib `http.server`, parses incoming POSTs into the
  API-Gateway-shaped event dict, and calls
  `app.api.text_export.handler.handler(event, None)` /
  `app.api.binary_export.handler.handler(event, None)` directly.
  ~80 lines, no extra deps, single command to start. The same
  handler code is exercised in dev and prod, just driven by a
  different shim. Probably what we want.
- **(B) No local server.** Tempera always hits the deployed URL.
  Faster to ship, but no way to test renders without a deploy
  round-trip.

Either way, `AUTH_TOKEN` left unset → handler skips the check →
local invocations don't need a token.

## Tempera client changes

- `SERVER_URL` becomes the deployed API Gateway URL (config knob in
  the script header). Optional fallback to `127.0.0.1:8000` on a
  toggle so the user can hit the local server when offline.
- The two endpoint paths (`/api/export/text`, `/api/export/binary`)
  collapse into four (`/api/export/{strudel,ot-basic,ot-doom,torso-s4}`).
  `EXPORT_TARGETS` already carries the per-target label/spec; the
  endpoint URL just becomes `${SERVER_URL}/api/export/${spec.target}`.
- `target` field comes out of the request body (it's in the path
  now).
- Every `postExport` call gains an `Authorization: Basic ...`
  header. Credentials read from `localStorage` (prompt on first
  export, "remember me" via the existing `createPersistedStore`).
- New `notify(...)` already in place handles the 401 / 403 / 5xx
  responses without blocking playback.

## Deploy workflow

```bash
source config/setenv.sh                 # AUTH_TOKEN, AWS_REGION
python scripts/stack/deploy.py --stage dev    # pipeline → build → app
python scripts/stack/smoke.py --stage dev     # POST a fixture export, expect 200 + zip bytes
```

`--stage dev` and `--stage prod` map to different Pulumi stack files
+ different AWS profiles. Single-developer scope ⇒ probably just
one stage at first.

## Open questions

1. **Single bucket or many?** Today only `wol-samplebank`. If we ever
   add per-customer or per-template buckets, the config + IAM grant
   pattern scales — but the schema needs a small list-of-buckets
   shape rather than a single env var. Pick one now.
2. **Where do auth credentials live for the user?** localStorage
   prompt vs. inline constant in the pasted script vs. tempera fetch
   from a public URL. localStorage prompt is friendliest, but means
   one extra UI primitive. Inline constant is fastest to ship and
   acceptable for a single-developer workflow.
3. **Provisioned concurrency or accept cold starts?** Cost ≈ $5/mo
   per warm instance vs. 10–30 s on the first export of a session
   *per Lambda*. With four Lambdas the cold-start cost is per
   target — switching from a strudel export to an ot-doom export
   pays the cold-start tax twice. Default: accept cold start;
   revisit if a single target bites enough to warrant warming just
   that one.
5. **Domain / TLS.** API Gateway gives a generated `*.execute-api`
   URL out of the box. Custom domain (`api.strudelbreaks.dev` or
   similar) needs Route 53 + ACM. Skip for v1 unless there's a
   reason.
6. **Local dev shim or no?** Option (A) above (`tools/serve_local.py`
   stdlib HTTP wrapper) vs. option (B) deploy-only. (A) is ~80 LOC
   and means no AWS round-trip when iterating on render code; (B)
   ships faster.
7. **Rollback.** outrights-mip pattern uses image digests so rollback
   is `pulumi config set image_uri <prev-digest> && pulumi up`.
   Inherit that.

## Non-goals for v1

- Multi-region.
- Custom domain.
- Provisioned concurrency.
- S3-presigned-URL response path for >6 MB exports (defer; explicit
  413 is fine for now).
- Per-user credentials (single shared `AUTH_TOKEN` is fine for a
  single-developer workflow).
- WAF / rate limiting (the auth token is the rate limit).

## Order of work, once green-lit

1. Refactor `sample_source.py` to take bucket + tmp paths from env;
   replace `aws s3 sync` with boto3.
2. Carve four handlers out of the existing route files —
   `app/api/{strudel,ot_basic,ot_doom,torso_s4}/handler.py`. Each
   handler validates only the fields its target accepts (no
   `target` field in the body any more — it's in the route).
   Common `app/api/_auth.py` for the HTTP Basic check.
3. Delete `app/main.py`, `app/config.py`, `app/routes/`,
   `scripts/run.sh`. Drop fastapi / uvicorn / pydantic from
   requirements.txt.
4. (If keeping local dev) Write `tools/serve_local.py` stdlib HTTP
   shim that drives the handlers directly.
5. Write `docker/Dockerfile` + `buildspec.yml`.
6. Write `infra/pipeline/__main__.py` + modules (mostly mirrored
   from outrights-mip, with our IAM grants + bucket name
   substituted).
7. Write `infra/app/__main__.py` — one HTTP API, four Lambda
   functions (each with its own memory/timeout/CMD entrypoint),
   four routes, four `lambda:InvokeFunction` permissions. Shared
   IAM role across all four (S3 read on the configured bucket).
8. Write `scripts/stack/deploy.py` (port from outrights-mip; same
   hash-or-skip logic).
9. Smoke test deployed dev.
10. Update tempera to point at the deployed URL + send auth header.
11. Update `README.md` and `docs/export/README.md` for the new
    workflow.
12. Delete the now-stale per-target tests that hit the FastAPI
    surface (`tests/test_server.py`); add direct handler tests
    against the API-Gateway-shaped event dict.

## References

- `../outrights-mip/infra/{pipeline,app}/__main__.py` — the
  pattern this plan tracks.
- `../outrights-mip/scripts/stack/deploy.py` — the orchestration
  script to port.
- `../outboard-brain/posts/markdown/lambda-container-deployments.md`
  — long-form rationale for the container + Pulumi approach.
- `../outrights-mip/app/outrights_mip/api/simulate/handler.py:49`
  — HTTP Basic auth via `AUTH_TOKEN` env var.
- `app/export/common/sample_source.py` — current S3 sync code that
  needs the boto3 + env-var refactor.
