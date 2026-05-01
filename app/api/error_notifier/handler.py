"""Error notifier — CloudWatch Logs subscription target → Slack webhook.

Subscribed to every other strudelbreaks Lambda's log group via a
`LogSubscriptionFilter`. CloudWatch Logs delivers matching log events
in a base64-encoded gzipped envelope under `event['awslogs']['data']`;
this handler decodes them, formats each one as a Block Kit message,
and POSTs to `SLACK_WEBHOOK_URL`.

Deliberately stdlib-only — packaged as a ZIP-based Lambda so it cold-
starts in under 500 ms. The export container image would work, but
its ~600 MB download isn't worth wearing on the alerting path.

This Lambda is NOT subscribed to its own log group: a Slack-post
failure that logged "ERROR" would loop forever.
"""
from __future__ import annotations

import base64
import gzip
import json
import logging
import os
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


# Slack section blocks have a 3000-char hard cap; leave headroom for
# the surrounding code-fence + truncation marker.
MAX_MESSAGE_CHARS = 2500


def _slack_blocks(function_name: str, log_event: dict, log_stream: str) -> dict:
    ts_ms = log_event.get('timestamp')
    if ts_ms is not None:
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        ts_str = ts.strftime('%Y-%m-%d %H:%M:%S UTC')
    else:
        ts_str = 'unknown'
    message = (log_event.get('message') or '').strip()
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[:MAX_MESSAGE_CHARS] + '\n…(truncated)'
    return {
        'blocks': [
            {
                'type': 'header',
                'text': {
                    'type': 'plain_text',
                    'text': f'🚨 Lambda error: {function_name}',
                },
            },
            {
                'type': 'section',
                'fields': [
                    {'type': 'mrkdwn', 'text': f'*Function*\n`{function_name}`'},
                    {'type': 'mrkdwn', 'text': f'*Time*\n{ts_str}'},
                ],
            },
            {
                'type': 'section',
                'text': {
                    'type': 'mrkdwn',
                    'text': f'*Error*\n```{message}```',
                },
            },
            {
                'type': 'context',
                'elements': [
                    {
                        'type': 'mrkdwn',
                        'text': f'log stream `{log_stream}`',
                    },
                ],
            },
        ],
    }


def _post(webhook_url: str, body: dict) -> None:
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        if resp.status >= 300:
            log.error('Slack webhook returned HTTP %d', resp.status)


def handler(event, _context=None):
    webhook_url = os.environ.get('SLACK_WEBHOOK_URL')
    if not webhook_url:
        # Don't loudly log "ERROR" — the notifier is its own potential
        # log source. Keep this at WARNING so a missing webhook doesn't
        # accidentally re-trigger ourselves through some other path.
        log.warning('SLACK_WEBHOOK_URL is not set; dropping notification')
        return {'statusCode': 200}

    try:
        compressed = base64.b64decode(event['awslogs']['data'])
        log_data = json.loads(gzip.decompress(compressed))
    except Exception:
        log.warning('Failed to decode CloudWatch Logs payload')
        return {'statusCode': 200}

    log_group = log_data.get('logGroup') or ''
    log_stream = log_data.get('logStream') or '<unknown>'
    function_name = log_group.replace('/aws/lambda/', '') or '<unknown>'

    for log_event in log_data.get('logEvents') or []:
        try:
            _post(webhook_url, _slack_blocks(function_name, log_event, log_stream))
        except Exception:
            log.warning('Slack post failed for %s', function_name)

    return {'statusCode': 200}
