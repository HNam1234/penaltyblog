"""Fixed-channel Bot API publisher. Never consumes bot updates or executes text."""
import json
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from adapters import _NoRedirect
from telegram_format import messages
from football import timestamp


class DeliveryError(RuntimeError):
    def __init__(self, code, uncertain=False):
        self.code, self.uncertain = code, uncertain
        super().__init__(code)


def send(token, channel_id, source):
    if not re.fullmatch(r'[0-9]{6,16}:[A-Za-z0-9_-]{20,150}', token):
        raise ValueError('invalid_bot_token')
    if not re.fullmatch(r'-100[0-9]{6,16}', str(channel_id)):
        raise ValueError('private_channel_required')
    parts = list(messages(source))
    if len(parts) != 1:
        raise ValueError('one_card_required')
    payload = json.dumps({'chat_id': channel_id, **parts[0],
                          'link_preview_options': {'is_disabled': True}}).encode()
    request = Request('https://api.telegram.org/bot' + token + '/sendMessage',
                      data=payload, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with build_opener(_NoRedirect()).open(request, timeout=15) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise DeliveryError('telegram_response_large', uncertain=True)
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get('result'), dict):
            raise DeliveryError('telegram_result_unknown', uncertain=True)
        message_id = data['result'].get('message_id')
        if data.get('ok') is not True or type(message_id) is not int or message_id < 1:
            raise DeliveryError('telegram_result_unknown', uncertain=True)
        return message_id
    except HTTPError as error:
        status = error.code
        error.close()
        # A known rejection is terminal; a server error may follow acceptance.
        raise DeliveryError('telegram_http_' + str(status), uncertain=status >= 500) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise DeliveryError('telegram_delivery_unknown', uncertain=True) from None


def flush(store, token, channel_id, now, sender=send, clock=None):
    """No blind retry after ambiguity: Telegram has no idempotency key."""
    count = 0
    clock = clock or (lambda: datetime.now(timezone.utc))
    initial = timestamp(now)
    while True:
        current = max(initial, timestamp(clock()))
        message = store.next_message(current)
        if not message:
            return count
        key = message['key']
        if not store.claim(key):
            continue
        if message['expires_at'] and timestamp(message['expires_at']) <= max(initial, timestamp(clock())):
            store.mark_failed(key, 'expired_before_send')
            continue
        try:
            message_id = sender(token, channel_id, message['text'])
        except DeliveryError as error:
            if error.uncertain:
                store.mark_uncertain(key)
            else:
                store.mark_failed(key, error.code)
        except Exception:
            store.mark_uncertain(key)
            raise
        else:
            store.mark_sent(key, message_id)
            count += 1
