"""Settings (PRD 0022 §4.1 item 1): the API key (administrators only), the base URL, the consent
box naming what is sent, and the two switches. Nothing is sent until the box is ticked and a key
is saved (ohmyfin_client.OhmyfinClient.is_active)."""
from odoo import api, fields, models

from .ohmyfin_client import (DEFAULT_BASE_URL, PARAM_AUTO_SCREEN, PARAM_BASE_URL, PARAM_CONSENT,
                             PARAM_KEY, PARAM_REFUSE_UNAVAILABLE, get_param, set_param)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ohmyfin_api_key = fields.Char('Ohmyfin API key', config_parameter=PARAM_KEY, groups='base.group_system')
    ohmyfin_base_url = fields.Char('Ohmyfin API address', config_parameter=PARAM_BASE_URL, default=DEFAULT_BASE_URL)
    ohmyfin_consent = fields.Boolean(
        'Send data to Ohmyfin for the checks', config_parameter=PARAM_CONSENT,
        help='Sent: contact names and countries, the delivery country, and each product HS code and '
             'country of origin. No prices, quantities or documents.')
    # Default ON, so it is stored as "True"/"False" by hand: a Boolean config_parameter deletes
    # itself when unticked and would read back as the default.
    ohmyfin_auto_screen = fields.Boolean(
        'Screen contacts automatically',
        help='Screen a company or person when it is created, and again when its name, type or country changes.')
    ohmyfin_refuse_when_unavailable = fields.Boolean(
        'Refuse when the check cannot run', config_parameter=PARAM_REFUSE_UNAVAILABLE,
        help='Off: an order or payment goes through with "not screened" and the reason in the chatter '
             'when the service cannot answer. On: it waits until the check runs.')

    @api.model
    def get_values(self):
        res = super().get_values()
        value = get_param(self.env, PARAM_AUTO_SCREEN)
        res['ohmyfin_auto_screen'] = value in (None, False, '') or str(value).lower() == 'true'
        return res

    def set_values(self):
        super().set_values()
        set_param(self.env, PARAM_AUTO_SCREEN, 'True' if self.ohmyfin_auto_screen else 'False')
