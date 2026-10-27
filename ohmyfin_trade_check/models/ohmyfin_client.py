"""The one HTTP client of the module. Every request to Ohmyfin goes through OhmyfinClient._send().

- Nothing is sent unless an administrator has saved an API key AND ticked the consent box
  (Settings). is_active() is the only test; every caller asks it before building a request.
- Every request carries the User-Agent ``Ohmyfin-Odoo/<module version> Odoo/<server version>``.
- The key travels in the ``KEY`` header only. It never reaches a log line, an exception
  message or the chatter: the log lines below name the method, the path and the status.
- Any answer the module cannot use (timeout, connection error, 5xx, 429, a refused key) is
  raised as OhmyfinUnavailable with a reason a user can read; the callers decide what that
  means for the order or the payment (PRD 0022 §4.1 item 6).
"""
import logging

import requests

from odoo import release

_logger = logging.getLogger(__name__)

PARAM_KEY = 'ohmyfin_trade_check.api_key'
PARAM_BASE_URL = 'ohmyfin_trade_check.base_url'
PARAM_CONSENT = 'ohmyfin_trade_check.consent'
PARAM_AUTO_SCREEN = 'ohmyfin_trade_check.auto_screen'
PARAM_REFUSE_UNAVAILABLE = 'ohmyfin_trade_check.refuse_when_unavailable'

DEFAULT_BASE_URL = 'https://api.ohmyfin.ai'
SIGNUP_URL = 'https://app.ohmyfin.ai/profile/api'

# The API's own ceiling is 60 s for the order check; a screen or a bank lookup is short.
TIMEOUT_SHORT = 20
TIMEOUT_ORDER = 60


class OhmyfinUnavailable(Exception):
    """The check could not run. ``reason`` is shown to the user as "not screened: <reason>"."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _truthy(value):
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def get_param(env, key):
    """A system parameter as a string, or None. Odoo 18 and 19 read it with get_param(); Odoo 20
    replaced that with typed getters (get_str, get_bool, ...)."""
    params = env['ir.config_parameter'].sudo()
    if hasattr(params, 'get_param'):
        return params.get_param(key)
    return params.get_str(key, None)


def set_param(env, key, value):
    """Store a system parameter as a string; False or None removes the value."""
    params = env['ir.config_parameter'].sudo()
    if hasattr(params, 'set_param'):
        return params.set_param(key, value)
    return params.set_str(key, value)


class OhmyfinClient:

    def __init__(self, env):
        self.env = env
        self._key = (get_param(env, PARAM_KEY) or '').strip()
        self._consent = _truthy(get_param(env, PARAM_CONSENT))
        self.base_url = (get_param(env, PARAM_BASE_URL) or DEFAULT_BASE_URL).strip().rstrip('/')
        self.refuse_when_unavailable = _truthy(get_param(env, PARAM_REFUSE_UNAVAILABLE))
        auto = get_param(env, PARAM_AUTO_SCREEN)
        self.auto_screen = True if auto in (None, False, '') else _truthy(auto)

    def is_active(self):
        """A key is saved and the consent box is ticked: the only condition for sending anything."""
        return bool(self._key) and self._consent

    # ── the three calls ───────────────────────────────────────────────────────────────────

    def screen_party(self, name, entity_type=None, country=None):
        """POST /api/v5/sanctions/screen for one contact."""
        body = {'name': name}
        if entity_type:
            body['entity_type'] = entity_type
        if country:
            # The country is ours to give, so it may annotate a match, never talk one down.
            body['address'] = {'country': country}
            body['secondary_match_mode'] = 'annotate'
        return self._send('POST', '/api/v5/sanctions/screen', json=body, timeout=TIMEOUT_SHORT)

    def trade_check(self, payload):
        """POST /api/v5/trade/check for one sales order."""
        return self._send('POST', '/api/v5/trade/check', json=payload, timeout=TIMEOUT_ORDER)

    def bank_lookup(self, bic):
        """GET /api/v5/banks for one BIC."""
        return self._send('GET', '/api/v5/banks', params={'query': bic, 'limit': 10}, timeout=TIMEOUT_SHORT)

    # ── transport ─────────────────────────────────────────────────────────────────────────

    def user_agent(self):
        module = self.env['ir.module.module'].sudo().search([('name', '=', 'ohmyfin_trade_check')], limit=1)
        version = module.latest_version or module.installed_version or '1.0.0'
        return 'Ohmyfin-Odoo/%s Odoo/%s' % (version, release.version)

    def _send(self, method, path, json=None, params=None, timeout=TIMEOUT_SHORT):
        if not self.is_active():
            # Callers check first; this is the second lock on the same door.
            raise OhmyfinUnavailable('no API key saved, or the consent box is not ticked')

        headers = {
            'KEY': self._key,
            'User-Agent': self.user_agent(),
            'Accept': 'application/json',
        }
        try:
            response = requests.request(method, self.base_url + path, headers=headers, json=json,
                                        params=params, timeout=timeout)
        except requests.exceptions.Timeout:
            _logger.warning('Ohmyfin %s %s: timeout', method, path)
            raise OhmyfinUnavailable('the service did not answer in time') from None
        except requests.exceptions.RequestException as exc:
            _logger.warning('Ohmyfin %s %s: %s', method, path, exc.__class__.__name__)
            raise OhmyfinUnavailable('the service could not be reached') from None

        status = response.status_code
        _logger.info('Ohmyfin %s %s: HTTP %s', method, path, status)

        if status == 429:
            raise OhmyfinUnavailable("the day's allowance of checks is used up (more with an Ohmyfin plan)")
        if status in (401, 403):
            raise OhmyfinUnavailable('the API key was refused')
        if status == 404:
            raise OhmyfinUnavailable('this check is not available on the service yet')
        if status >= 500:
            raise OhmyfinUnavailable('the service is unavailable (HTTP %s)' % status)
        if status >= 400:
            raise OhmyfinUnavailable('the service refused the request (HTTP %s)' % status)

        try:
            data = response.json()
        except ValueError:
            raise OhmyfinUnavailable('the service sent an answer that could not be read') from None
        if not isinstance(data, dict):
            raise OhmyfinUnavailable('the service sent an answer that could not be read')
        return data
