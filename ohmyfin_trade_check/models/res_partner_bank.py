"""Vendor bank account (PRD 0022 §4.1 item 5): when the BIC is saved, look it up in Ohmyfin's
bank directory and show the bank's name and country; warn on a BIC the directory does not know,
and on a BIC whose country differs from the IBAN's. Free on every Ohmyfin plan.

Odoo 18 and 19 keep the BIC on the bank (`bank_id.bic`, read here through the related
`bank_bic`) and the number in `acc_number`; Odoo 20 stores `bank_bic` on the account itself and
calls the number `account_number`. Both are read through `bank_bic` and _ohmyfin_number().
"""
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .ohmyfin_client import OhmyfinClient, OhmyfinUnavailable

BIC_SHAPE = re.compile(r'^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$')
IBAN_SHAPE = re.compile(r'^[A-Z]{2}\d{2}[A-Z0-9]{8,30}$')
TRIGGERS = ('bank_bic', 'bank_id', 'acc_number', 'account_number')


class ResPartnerBank(models.Model):
    _inherit = 'res.partner.bank'

    ohmyfin_bic_status = fields.Selection([
        ('not_checked', 'Not looked up'),
        ('found', 'Found in the bank directory'),
        ('unknown', 'Not in the bank directory'),
        ('not_screened', 'Not looked up: the service did not answer'),
    ], string='BIC lookup', default='not_checked', readonly=True, copy=False)
    ohmyfin_bic_checked = fields.Char('BIC looked up', readonly=True, copy=False)
    ohmyfin_bic_bank_name = fields.Char('Bank (directory)', readonly=True, copy=False)
    ohmyfin_bic_country = fields.Char('Bank country (directory)', readonly=True, copy=False)
    ohmyfin_bic_warning = fields.Char('BIC warning', readonly=True, copy=False)

    def _ohmyfin_number(self):
        self.ensure_one()
        field = 'account_number' if 'account_number' in self._fields else 'acc_number'
        return re.sub(r'\s', '', (self[field] or '')).upper()

    def _ohmyfin_bic(self):
        self.ensure_one()
        return re.sub(r'\s', '', (self.bank_bic or '')).upper()

    @api.model_create_multi
    def create(self, vals_list):
        accounts = super().create(vals_list)
        if not self.env.context.get('ohmyfin_no_lookup'):
            accounts._ohmyfin_auto_lookup()
        return accounts

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('ohmyfin_no_lookup') and any(f in vals for f in TRIGGERS):
            self._ohmyfin_auto_lookup()
        return res

    def _ohmyfin_auto_lookup(self):
        client = OhmyfinClient(self.env)
        if not client.is_active():
            return
        todo = self.filtered(lambda a: a._ohmyfin_bic() and a._ohmyfin_bic() != a.ohmyfin_bic_checked)
        if 0 < len(todo) <= 5 and not self.env.context.get('import_file'):
            todo._ohmyfin_lookup(client)

    def action_ohmyfin_lookup_bic(self):
        client = OhmyfinClient(self.env)
        if not client.is_active():
            raise UserError(_('Save an Ohmyfin API key and tick the consent box in Settings first.'))
        self._ohmyfin_lookup(client)
        return True

    def _ohmyfin_lookup(self, client):
        for account in self:
            bic = account._ohmyfin_bic()
            if not bic:
                continue
            vals = {'ohmyfin_bic_checked': bic, 'ohmyfin_bic_bank_name': False, 'ohmyfin_bic_country': False,
                    'ohmyfin_bic_warning': False}
            if not BIC_SHAPE.match(bic):
                vals.update({'ohmyfin_bic_status': 'unknown',
                             'ohmyfin_bic_warning': 'This is not the shape of a BIC (8 or 11 letters and digits).'})
                account.with_context(ohmyfin_no_lookup=True).write(vals)
                continue
            try:
                answer = client.bank_lookup(bic)
            except OhmyfinUnavailable as exc:
                vals.update({'ohmyfin_bic_status': 'not_screened',
                             'ohmyfin_bic_warning': 'The BIC was not looked up: %s.' % exc.reason})
                account.with_context(ohmyfin_no_lookup=True).write(vals)
                continue

            bank = self._ohmyfin_pick(bic, answer.get('banks') or [])
            if not bank:
                vals.update({'ohmyfin_bic_status': 'unknown',
                             'ohmyfin_bic_warning': 'Ohmyfin\'s bank directory does not know the BIC %s: check it with the vendor.' % bic})
            else:
                country = (bank.get('country') or '').upper()
                vals.update({'ohmyfin_bic_status': 'found', 'ohmyfin_bic_bank_name': bank.get('name') or False,
                             'ohmyfin_bic_country': country or False})
                number = account._ohmyfin_number()
                if IBAN_SHAPE.match(number) and country and number[:2] != country:
                    vals['ohmyfin_bic_warning'] = (
                        'The BIC belongs to a bank in %s, the IBAN to an account in %s: check the bank details '
                        'with the vendor.' % (country, number[:2]))
            account.with_context(ohmyfin_no_lookup=True).write(vals)

    @staticmethod
    def _ohmyfin_pick(bic, banks):
        """The directory row for exactly this BIC: the 11-character branch, or its 8-character
        institution (a head office's "XXX" is the 8-character code)."""
        wanted = {bic, bic[:8]} if len(bic) == 11 and bic.endswith('XXX') else {bic}
        for bank in banks:
            if isinstance(bank, dict) and (bank.get('swift') or '').upper() in wanted:
                return bank
        if len(bic) == 11:
            for bank in banks:
                if isinstance(bank, dict) and (bank.get('swift') or '').upper() == bic[:8]:
                    return bank
        return None
