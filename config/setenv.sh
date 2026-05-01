# Source before running scripts/stack/deploy.py.
#
#   source config/setenv.sh
#   python scripts/stack/deploy.py --stage dev
#
# Pulumi state is stored in a local file backend under each
# infra/{pipeline,app}/.pulumi/ directory — same layout as
# ../outrights-mip. Single-developer workflow; gitignored.
#
# AUTH_TOKEN is in the form "username:password" (HTTP Basic). Keep
# this file out of git or replace with a secrets-store lookup.
export AWS_REGION="${AWS_REGION:-eu-west-1}"
export PULUMI_BACKEND_URL="${PULUMI_BACKEND_URL:-file://.}"
export PULUMI_CONFIG_PASSPHRASE="${PULUMI_CONFIG_PASSPHRASE:-}"
export AUTH_TOKEN="${AUTH_TOKEN:-}"

# Custom domain wiring. Both must be set together, or both left
# unset (the API publishes only the *.execute-api endpoint).
#   strudelbeats.klingklangwol.com → wol-dev hosted zone
#     (Z08818266BL6N2UY3C67, created during the domain handover).
export STRUDELBREAKS_DOMAIN="${STRUDELBREAKS_DOMAIN:-strudelbeats.klingklangwol.com}"
export STRUDELBREAKS_HOSTED_ZONE_ID="${STRUDELBREAKS_HOSTED_ZONE_ID:-Z08818266BL6N2UY3C67}"

# One-shot drum-sample bucket. Read by both stacks.
export STRUDELBREAKS_ONESHOT_S3_URI="${STRUDELBREAKS_ONESHOT_S3_URI:-s3://wol-samplebank/samples/}"
