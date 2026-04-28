# Source before running scripts/stack/deploy.py.
#
#   source config/setenv.sh
#   python scripts/stack/deploy.py --stage dev
#
# AUTH_TOKEN is in the form "username:password" (HTTP Basic). Keep
# this file out of git or replace these with `aws ssm get-parameter`
# lookups against a secrets store.
export AWS_REGION="${AWS_REGION:-eu-west-1}"
export AUTH_TOKEN="${AUTH_TOKEN:-}"
