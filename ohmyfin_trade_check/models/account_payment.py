"""The payment guard (PRD 0022 §4.1 item 4): when an outgoing payment is posted, the payee is
screened if its screen is stale (never screened, older than 30 days, or its name, type or country
changed since). A listed payee is refused; a potential match waits for a compliance reviewer's
reason; a screen that cannot run lets the payment through with "not screened: <reason>" in the
chatter, unless "refuse when the check cannot run" is on.

As on the order, a held payment is left in draft and a warning is returned rather than raised,
so the payee's fresh screen and the chatter line are kept.
"""
from odoo import _, fields, models
from odoo.exceptions import UserError

from . import ohmyfin_words as words
from .ohmyfin_client import OhmyfinClient
from .ohmyfin_review import REVIEWER_GROUP, take_reason


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    ohmyfin_guard_state = fields.Selection([
        ('none', 'Not checked'),
        ('no_match', 'No match'),
        ('review', 'Waiting for a compliance reviewer'),
        ('released', 'Released by a compliance reviewer'),
        ('refused', 'Refused: payee listed'),
        ('not_screened', 'Not screened'),
    ], string='Payee screen', default='none', readonly=True, copy=False)
    ohmyfin_guard_reason = fields.Char('Why not screened', readonly=True, copy=False)
    ohmyfin_review_input = fields.Text('Reason to release', groups=REVIEWER_GROUP, copy=False)
    ohmyfin_review_reason = fields.Text('Reviewer reason', readonly=True, copy=False)
    ohmyfin_reviewed_by = fields.Many2one('res.users', 'Reviewed by', readonly=True, copy=False)
    ohmyfin_reviewed_at = fields.Datetime('Reviewed on', readonly=True, copy=False)
    ohmyfin_payee_badge = fields.Char('Payee sanctions status', related='partner_id.commercial_partner_id.ohmyfin_badge')

    def _ohmyfin_payee(self):
        self.ensure_one()
        return self.partner_id.commercial_partner_id

    def _ohmyfin_guard(self, client):
        """Decide one payment. True when it may be posted. Screens the payee first if stale."""
        self.ensure_one()
        payee = self._ohmyfin_payee()
        if self.payment_type != 'outbound' or not payee:
            return True

        if payee._ohmyfin_is_stale():
            payee._ohmyfin_screen(client)
        state = payee.ohmyfin_screen_state
        lists = [m.get('list_name') or m.get('list_source') for m in
                 words.load(payee.ohmyfin_matches_json).get('matches') or [] if isinstance(m, dict)]

        if state == 'listed':
            self.write({'ohmyfin_guard_state': 'refused', 'ohmyfin_guard_reason': False})
            self.message_post(body=words.payment_note('listed', payee.name, payee.ohmyfin_report_id,
                                                      lists=[x for x in lists if x][:3]))
            return False
        if state == 'potential_match':
            if payee._ohmyfin_released():
                # Released on the contact, for this screen: the reason travels with the payment.
                self.write({'ohmyfin_guard_state': 'released', 'ohmyfin_guard_reason': False,
                            'ohmyfin_review_reason': payee.ohmyfin_review_reason,
                            'ohmyfin_reviewed_by': payee.ohmyfin_reviewed_by.id,
                            'ohmyfin_reviewed_at': payee.ohmyfin_reviewed_at})
                self.message_post(body=words.release_note(payee.ohmyfin_reviewed_by.name, payee.ohmyfin_review_reason,
                                                          payee.ohmyfin_report_id))
                return True
            self.write({'ohmyfin_guard_state': 'review', 'ohmyfin_guard_reason': False})
            self.message_post(body=words.payment_note('potential_match', payee.name, payee.ohmyfin_report_id))
            return False
        if state == 'not_screened':
            go = not client.refuse_when_unavailable
            self.write({'ohmyfin_guard_state': 'not_screened', 'ohmyfin_guard_reason': payee.ohmyfin_screen_reason})
            self.message_post(body=words.payment_note('not_screened', payee.name, reason=payee.ohmyfin_screen_reason,
                                                      posted=go))
            return go
        self.write({'ohmyfin_guard_state': 'no_match', 'ohmyfin_guard_reason': False})
        return True

    def action_post(self):
        client = OhmyfinClient(self.env)
        if not client.is_active() or self.env.context.get('ohmyfin_skip_check'):
            return super().action_post()
        go = self.browse()
        held = self.browse()
        for payment in self:
            if payment._ohmyfin_guard(client):
                go |= payment
            else:
                held |= payment
        res = super(AccountPayment, go).action_post() if go else True
        if held:
            return held._ohmyfin_notification()
        return res

    def _ohmyfin_notification(self):
        lines = ['%s: %s' % (p.name or p.partner_id.display_name, dict(self._fields['ohmyfin_guard_state'].selection).get(
            p.ohmyfin_guard_state)) for p in self]
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Not posted'),
                'message': '\n'.join(lines) + '\n' + _("The payee's screen is in the chatter."),
                'type': 'danger' if any(p.ohmyfin_guard_state == 'refused' for p in self) else 'warning',
                'sticky': True,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_ohmyfin_release(self):
        """A compliance reviewer releases a waiting payment with the reason typed in "Reason to
        release", and the payment is posted."""
        self.ensure_one()
        return self._ohmyfin_release(take_reason(self))

    def _ohmyfin_release(self, reason):
        """Record a compliance reviewer's reason for a waiting payment, then post it. The reason
        is recorded on the payee for its current screen (so the Register Payment wizard honours it
        too), and copied to the payment when it posts."""
        self.ensure_one()
        if self.ohmyfin_guard_state != 'review':
            raise UserError(_('Only a payment waiting for a compliance reviewer can be released.'))
        self._ohmyfin_payee()._ohmyfin_release(reason)
        return self.action_post()
