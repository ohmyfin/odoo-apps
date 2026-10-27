"""AC 2: consent unticked or no key: 0 HTTP calls on contact create, order confirm, payment post."""
from odoo.tests import tagged

from .common import OhmyfinCase


@tagged('post_install', '-at_install')
class TestConsent(OhmyfinCase):

    def _everything(self):
        partner = self.env['res.partner'].create({'name': 'DEMO New Customer SA', 'is_company': True,
                                                  'country_id': self.kz.id})
        order = self.make_order(customer=partner)
        order.action_confirm()
        payment = self.make_payment(self.vendor)
        payment.action_post()
        self.env['res.partner.bank'].create(self._bank_vals(partner))
        return partner, order, payment

    def _bank_vals(self, partner):
        bank = self.env['res.partner.bank']
        number = 'account_number' if 'account_number' in bank._fields else 'acc_number'
        vals = {'partner_id': partner.id, number: 'DE89370400440532013000'}
        if 'bank_id' in bank._fields and bank._fields['bank_bic'].related:
            vals['bank_id'] = self.env['res.bank'].create({'name': 'DEMO Bank', 'bic': 'COBADEFF'}).id
        else:
            vals['bank_bic'] = 'COBADEFF'
        return vals

    def test_no_consent_sends_nothing(self):
        self.activate(consent=False)
        partner, order, payment = self._everything()
        self.assertEqual(self.calls, [])
        self.assertEqual(order.state, 'sale')
        self.assertEqual(order.ohmyfin_check_state, 'none')
        self.assertNotEqual(payment.state, 'draft')
        self.assertEqual(partner.ohmyfin_screen_state, 'not_screened')

    def test_no_key_sends_nothing(self):
        self.activate(key=False)
        _partner, order, payment = self._everything()
        self.assertEqual(self.calls, [])
        self.assertEqual(order.state, 'sale')
        self.assertNotEqual(payment.state, 'draft')

    def test_with_both_it_sends(self):
        self._everything()
        self.assertTrue(self.calls)
