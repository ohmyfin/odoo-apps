"""The payment guard (§4.1 item 4) and AC 4's payment half: a listed payee is refused, a potential
match waits for a reviewer's reason, a stale payee is screened once, a screen that cannot run lets
the payment through with "not screened" unless the switch says otherwise; the Register Payment
wizard creates nothing for a held payee."""
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import LISTED, POTENTIAL, OhmyfinCase


@tagged('post_install', '-at_install')
class TestPaymentGuard(OhmyfinCase):

    def _vendor(self, name):
        return self.env['res.partner'].with_context(ohmyfin_no_screen=True).create(
            {'name': name, 'is_company': True, 'country_id': self.env.ref('base.de').id})

    def test_payee_with_no_match_posts_after_one_screen(self):
        payment = self.make_payment(self.vendor)
        payment.action_post()
        self.assertNotEqual(payment.state, 'draft')
        self.assertEqual(payment.ohmyfin_guard_state, 'no_match')
        self.assertEqual(len(self.calls_to('/api/v5/sanctions/screen')), 1)
        # Screened minutes ago: the next payment to the same payee makes no call.
        second = self.make_payment(self.vendor)
        second.action_post()
        self.assertNotEqual(second.state, 'draft')
        self.assertEqual(len(self.calls), 1)

    def test_listed_payee_is_refused(self):
        vendor = self._vendor(LISTED)
        payment = self.make_payment(vendor)
        action = payment.action_post()
        self.assertEqual(payment.state, 'draft')
        self.assertEqual(payment.ohmyfin_guard_state, 'refused')
        self.assertEqual(action['params']['type'], 'danger')
        self.assertIn('is listed', self.last_message(payment))
        self.assertIn('was not posted', self.last_message(payment))
        self.assertEqual(vendor.ohmyfin_screen_state, 'listed', "the payee's screen is kept")

    def test_potential_match_waits_then_a_reviewer_releases(self):
        vendor = self._vendor(POTENTIAL)
        payment = self.make_payment(vendor)
        payment.action_post()
        self.assertEqual(payment.state, 'draft')
        self.assertEqual(payment.ohmyfin_guard_state, 'review')
        self.assertIn("waits for a compliance reviewer's reason", self.last_message(payment))

        with self.assertRaises(AccessError):
            payment.with_user(self.salesman).action_ohmyfin_release()
        self.assertEqual(payment.state, 'draft')

        as_reviewer = payment.with_user(self.reviewer)
        as_reviewer.write({'ohmyfin_review_input': 'Not this supplier: the match is a shipping company in Malta.'})
        as_reviewer.action_ohmyfin_release()
        self.assertNotEqual(payment.state, 'draft')
        self.assertEqual(payment.ohmyfin_guard_state, 'released')
        self.assertIn('Malta', payment.ohmyfin_review_reason)
        self.assertEqual(payment.ohmyfin_reviewed_by, self.reviewer)
        self.assertIn('Released by', '\n'.join(self.messages(payment)))
        # The reason sits on the payee for this screen: the next payment goes through with it.
        later = self.make_payment(vendor)
        later.action_post()
        self.assertNotEqual(later.state, 'draft')
        self.assertEqual(later.ohmyfin_guard_state, 'released')

    def test_service_down_posts_with_not_screened(self):
        type(self).fail_with = (502, {})
        payment = self.make_payment(self._vendor('DEMO Unreachable Supplier'))
        payment.action_post()
        self.assertNotEqual(payment.state, 'draft')
        self.assertEqual(payment.ohmyfin_guard_state, 'not_screened')
        self.assertIn('Payee not screened: the service is unavailable (HTTP 502)', self.last_message(payment))
        self.assertIn('posted without the screen', self.last_message(payment))

    def test_service_down_with_refuse_switch_holds(self):
        self.activate(refuse=True)
        type(self).fail_with = (502, {})
        payment = self.make_payment(self._vendor('DEMO Unreachable Supplier'))
        payment.action_post()
        self.assertEqual(payment.state, 'draft')
        self.assertIn('was not posted', self.last_message(payment))

    def test_incoming_payment_is_not_guarded(self):
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': self._vendor(LISTED).id,
            'amount': 50.0, 'journal_id': self.company_data['default_journal_bank'].id})
        payment.action_post()
        self.assertEqual(self.calls, [])
        self.assertNotEqual(payment.state, 'draft')

    def test_register_payment_wizard_creates_nothing_for_a_listed_payee(self):
        vendor = self._vendor(LISTED)
        bill = self.init_invoice('in_invoice', partner=vendor, amounts=[100.0], post=True)
        before = self.env['account.payment'].search_count([])
        wizard = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=bill.ids).create({})
        action = wizard.action_create_payments()
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(self.env['account.payment'].search_count([]), before)
        self.assertEqual(vendor.ohmyfin_screen_state, 'listed')

    def test_register_payment_wizard_pays_a_payee_with_no_match(self):
        bill = self.init_invoice('in_invoice', partner=self.vendor, amounts=[100.0], post=True)
        wizard = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=bill.ids).create({})
        wizard.action_create_payments()
        self.assertEqual(len(self.calls_to('/api/v5/sanctions/screen')), 1)
        self.assertIn(bill.payment_state, ('paid', 'in_payment', 'partial'))
