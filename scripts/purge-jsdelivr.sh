#!/usr/bin/env bash
# Purge jsDelivr's upstream cache for strudelbreaks breaks.js.
#
# The `?_=${Date.now()}` cache-bust in the loader handles the edge
# cache, but jsDelivr has a separate upstream cache on the branch-tip
# resolution (e.g. "what SHA does @main point at right now?") that
# ignores query strings and can serve a stale ref for up to 12 hours
# after a push. Run this after pushing if you need the new HEAD live
# immediately via @main.
#
# Usage:
#   scripts/purge-jsdelivr.sh          # purges @main
#   scripts/purge-jsdelivr.sh <ref>    # purges @<ref> (branch, tag, sha)
set -euo pipefail

REF="${1:-main}"
URL="https://purge.jsdelivr.net/gh/jhw/strudelbreaks@${REF}/breaks.js"

echo "purging: $URL"
curl -fsS "$URL"
echo
