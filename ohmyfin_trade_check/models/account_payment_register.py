"""The Register Payment wizard creates and posts payments in one step and reconciles them with the
bills straight after; a payment held in draft there would fail that reconciliation. So the payee
guard runs here first, before anything is created: when any payee is held, no payment is created,
the payee's screen is kept, and the wizard answers with the same warning as the payment form.
"""
from odoo import models

from .ohmyfin_client import OhmyfinClient
from . import ohmyfin_words as words


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    def action_create_payments(self):
        client = OhmyfinClient(self.env)
        if not client.is_active() or self.env.context.get('ohmyfin_skip_check') or self.payment_type != 'outbound':
            return super().action_create_payments()

        held = []
        for payee in self.line_ids.move_id.partner_id.commercial_partner_id:
            if payee._ohmyfin_is_stale():
                payee._ohmyfin_screen(client)
            state = payee.ohmyfin_screen_state
            if state == 'listed' or (state == 'potential_match' and not payee._ohmyfin_released()) \
                    or (state == 'not_screened' and client.refuse_when_unavailable):
                held.append((payee, state))
        if not held:
            return super().action_create_payments()

        lines = []
        for payee, state in held:
            text = words.payment_note(state, payee.name, payee.ohmyfin_report_id, reason=payee.ohmyfin_screen_reason,
                                      posted=False)
            payee.message_post(body=text)
            lines.append(text)
        lines.append('A compliance reviewer can record a reason on a potential match from the contact, '
                     'under "Sanctions screening"; then register the payment again.')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Not paid',
                'message': '\n'.join(lines),
                'type': 'danger' if any(state == 'listed' for _payee, state in held) else 'warning',
                'sticky': True,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
