"""AC 5: every request carries the Ohmyfin-Odoo User-Agent; the key appears 0 times in captured
logs and in the chatter, on the success paths and the failure paths alike."""
import re

import requests

from odoo import release
from odoo.tests import tagged

from .common import KEY, LISTED, POTENTIAL, OhmyfinCase


@tagged('post_install', '-at_install')
class TestPrivacy(OhmyfinCase):

    def _everything(self):
        self.env['res.partner'].create({'name': 'DEMO Logged Customer', 'is_company': True, 'country_id': self.kz.id})
        self.env['res.partner'].create({'name': POTENTIAL, 'is_company': True, 'country_id': self.kz.id})
        order = self.make_order()
        type(self).trade_case = 'licence'
        order.action_confirm()
        payment = self.make_payment(self.env['res.partner'].with_context(ohmyfin_no_screen=True).create(
            {'name': LISTED, 'is_company': True, 'country_id': self.kz.id}))
        payment.action_post()
        bank = self.env['res.partner.bank']
        field = 'account_number' if 'account_number' in bank._fields else 'acc_number'
        vals = {'partner_id': self.vendor.id, field: 'DE89370400440532013000'}
        if bank._fields['bank_bic'].related:
            vals['bank_id'] = self.env['res.bank'].create({'name': 'DEMO Bank', 'bic': 'COBADEFF'}).id
        else:
            vals['bank_bic'] = 'COBADEFF'
        bank.create(vals)
        for failure in ((500, {}), (429, {}), requests.exceptions.Timeout(), requests.exceptions.ConnectionError()):
            type(self).fail_with = failure
            self.env['res.partner'].create({'name': 'DEMO Failing %s' % len(self.calls), 'is_company': True})
            self.make_order().action_confirm()

    def test_user_agent_on_every_request_and_the_key_nowhere_else(self):
        with self.assertLogs(level='DEBUG') as logs:
            self._everything()

        self.assertGreaterEqual(len(self.calls), 10)
        paths = {c.path_url.split('?')[0] for c in self.calls}
        self.assertEqual(paths, {'/api/v5/sanctions/screen', '/api/v5/trade/check', '/api/v5/banks'})
        agent = re.compile(r'^Ohmyfin-Odoo/\d+\.\d+(\.\d+)* Odoo/%s$' % re.escape(release.version))
        for call in self.calls:
            self.assertRegex(call.headers['User-Agent'], agent)
            self.assertEqual(call.headers['KEY'], KEY)
            self.assertNotIn(KEY, call.url)
            self.assertNotIn(KEY.encode(), call.body or b'')

        captured = '\n'.join(logs.output)
        self.assertIn('Ohmyfin POST /api/v5/trade/check', captured, 'the client did log')
        self.assertEqual(captured.count(KEY), 0)

        bodies = self.env['mail.message'].sudo().search([]).mapped('body')
        self.assertTrue(any('Trade check' in (b or '') for b in bodies))
        self.assertEqual(sum((b or '').count(KEY) for b in bodies), 0)
