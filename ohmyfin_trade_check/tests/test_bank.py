"""Vendor bank account (§4.1 item 5): the BIC looked up when it is saved; the bank's name and
country stored; a warning for an unknown BIC and for a BIC country that differs from the IBAN's."""
from odoo.tests import tagged

from .common import OhmyfinCase


@tagged('post_install', '-at_install')
class TestBankBic(OhmyfinCase):

    def _account(self, bic, number):
        bank = self.env['res.partner.bank']
        field = 'account_number' if 'account_number' in bank._fields else 'acc_number'
        vals = {'partner_id': self.vendor.id, field: number}
        if bank._fields['bank_bic'].related:
            # Odoo 18 and 19: the BIC lives on the bank.
            vals['bank_id'] = self.env['res.bank'].create({'name': 'DEMO Bank %s' % bic, 'bic': bic}).id
        else:
            vals['bank_bic'] = bic
        return bank.create(vals)

    def test_known_bic_stores_name_and_country(self):
        account = self._account('COBADEFFXXX', 'DE89370400440532013000')
        calls = self.calls_to('/api/v5/banks')
        self.assertEqual(len(calls), 1)
        self.assertIn('query=COBADEFFXXX', calls[0].url)
        self.assertEqual(account.ohmyfin_bic_status, 'found')
        self.assertEqual(account.ohmyfin_bic_bank_name, 'COMMERZBANK AG')
        self.assertEqual(account.ohmyfin_bic_country, 'DE')
        self.assertFalse(account.ohmyfin_bic_warning)

    def test_bic_and_iban_in_different_countries_warns(self):
        account = self._account('HALKKZKA', 'DE89370400440532013000')
        self.assertEqual(account.ohmyfin_bic_status, 'found')
        self.assertIn('a bank in KZ, the IBAN to an account in DE', account.ohmyfin_bic_warning)

    def test_unknown_bic_warns(self):
        account = self._account('ZZZZDEFF', 'DE89370400440532013000')
        self.assertEqual(account.ohmyfin_bic_status, 'unknown')
        self.assertIn('does not know the BIC ZZZZDEFF', account.ohmyfin_bic_warning)

    def test_malformed_bic_warns_without_a_call(self):
        account = self._account('NOT-A-BIC', 'DE89370400440532013000')
        self.assertEqual(self.calls, [])
        self.assertEqual(account.ohmyfin_bic_status, 'unknown')
        self.assertIn('not the shape of a BIC', account.ohmyfin_bic_warning)

    def test_service_down_says_not_looked_up(self):
        type(self).fail_with = (503, {})
        account = self._account('COBADEFF', 'DE89370400440532013000')
        self.assertEqual(account.ohmyfin_bic_status, 'not_screened')
        self.assertIn('was not looked up', account.ohmyfin_bic_warning)

    def test_same_bic_is_not_looked_up_twice(self):
        account = self._account('COBADEFF', 'DE89370400440532013000')
        account.action_ohmyfin_lookup_bic()
        self.assertEqual(len(self.calls), 2, 'the button always looks up')
        account.write({'allow_out_payment': True})
        self.assertEqual(len(self.calls), 2)
