"""The payment guard (§4.1 item 4) and AC 4's payment half: a listed payee is refused, a potential
match waits for a reviewer's reason, a stale payee is screened once, a screen that cannot run lets
the payment through with "not screened" unless the switch says otherwise; the Register Payment
wizard creates nothing for a held payee.

changes/0053 (1.1.1): a plain accountant, the user the guard is for, cannot skip it: not by
writing the payee's screen state or a release reference on the contact, not by writing the
payment's guard state, not with a context key on the payment or on the wizard."""
from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from ..models.account_payment import PROTECTED
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

    # ── changes/0053: the guard cannot be skipped by the users it holds ────────────────────

    def test_every_stored_guard_field_is_module_owned(self):
        self.assert_module_owned('account.payment', PROTECTED)

    def test_skip_check_context_does_not_skip_the_post(self):
        """AC 2, the payment."""
        vendor = self._vendor(LISTED)
        payment = self.make_payment(vendor, user=self.plain_accountant)
        payment.with_context(ohmyfin_skip_check=True).action_post()
        self.assertEqual(payment.state, 'draft')
        self.assertEqual(payment.ohmyfin_guard_state, 'refused')
        self.assertEqual(len(self.calls_to('/api/v5/sanctions/screen')), 1)

    def test_skip_check_context_does_not_skip_the_wizard(self):
        """AC 2, the Register Payment wizard."""
        vendor = self._vendor(LISTED)
        bill = self.init_invoice('in_invoice', partner=vendor, amounts=[100.0], post=True)
        before = self.env['account.payment'].search_count([])
        wizard = self.env['account.payment.register'].with_user(self.plain_accountant).with_context(
            active_model='account.move', active_ids=bill.ids, ohmyfin_skip_check=True).create({})
        action = wizard.action_create_payments()
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(self.env['account.payment'].search_count([]), before)
        self.assertEqual(bill.payment_state, 'not_paid')

    def test_plain_user_writing_no_match_on_a_listed_payee_does_not_post(self):
        """AC 3: the state, a fresh date and the matching screen key in one write, so the payee
        would not even read as stale."""
        vendor = self._vendor(LISTED)
        payment = self.make_payment(vendor, user=self.plain_accountant)
        payment.action_post()
        self.assertEqual(vendor.ohmyfin_screen_state, 'listed')
        vendor.with_user(self.plain_accountant).write({
            'ohmyfin_screen_state': 'no_match', 'ohmyfin_screened_at': fields.Datetime.now(),
            'ohmyfin_screen_key': vendor._ohmyfin_key(), 'ohmyfin_matches_json': '{"matches": []}'})
        self.assertEqual(vendor.ohmyfin_screen_state, 'listed')
        self.assertIn('Specially Designated Nationals', vendor.ohmyfin_matches_json)
        payment.write({'ohmyfin_guard_state': 'no_match'})
        self.assertEqual(payment.ohmyfin_guard_state, 'refused')
        payment.action_post()
        self.assertEqual(payment.state, 'draft')
        self.assertEqual(payment.ohmyfin_guard_state, 'refused')

    def test_plain_user_writing_no_match_before_any_screen_does_not_post(self):
        """AC 3, on a payee never screened: the write does not make it fresh, so the guard
        screens it and refuses."""
        vendor = self._vendor(LISTED)
        vendor.with_user(self.plain_accountant).write({
            'ohmyfin_screen_state': 'no_match', 'ohmyfin_screened_at': fields.Datetime.now(),
            'ohmyfin_screen_key': vendor._ohmyfin_key()})
        self.assertEqual(vendor.ohmyfin_screen_state, 'not_screened')
        payment = self.make_payment(vendor, user=self.plain_accountant)
        payment.action_post()
        self.assertEqual(payment.state, 'draft')
        self.assertEqual(vendor.ohmyfin_screen_state, 'listed')

    def test_plain_user_writing_a_release_reference_does_not_post(self):
        """AC 3, separately: ``ohmyfin_released_for`` (with a reviewer and a reason) written on a
        potential match by a non-reviewer."""
        vendor = self._vendor(POTENTIAL)
        payment = self.make_payment(vendor, user=self.plain_accountant)
        payment.action_post()
        self.assertEqual(payment.ohmyfin_guard_state, 'review')
        vendor.with_user(self.plain_accountant).write({
            'ohmyfin_released_for': vendor._ohmyfin_screen_ref(), 'ohmyfin_reviewed_by': self.reviewer.id,
            'ohmyfin_reviewed_at': fields.Datetime.now(), 'ohmyfin_review_reason': 'Looks fine to me, honestly.'})
        self.assertFalse(vendor.ohmyfin_released_for)
        self.assertFalse(vendor.ohmyfin_reviewed_by)
        self.assertFalse(vendor.ohmyfin_review_current)
        payment.write({'ohmyfin_guard_state': 'released', 'ohmyfin_reviewed_by': self.reviewer.id})
        self.assertEqual(payment.ohmyfin_guard_state, 'review')
        self.assertFalse(payment.ohmyfin_reviewed_by)
        payment.action_post()
        self.assertEqual(payment.state, 'draft')
        self.assertEqual(payment.ohmyfin_guard_state, 'review')
        # The wizard decides from the same contact, so it holds too.
        bill = self.init_invoice('in_invoice', partner=vendor, amounts=[100.0], post=True)
        wizard = self.env['account.payment.register'].with_user(self.plain_accountant).with_context(
            active_model='account.move', active_ids=bill.ids).create({})
        self.assertEqual(wizard.action_create_payments()['tag'], 'display_notification')
        self.assertEqual(bill.payment_state, 'not_paid')

    def test_create_carrying_a_guard_state_is_created_unchecked(self):
        payment = self.env['account.payment'].with_user(self.plain_accountant).with_context(
            default_ohmyfin_guard_state='released').create({
                'payment_type': 'outbound', 'partner_type': 'supplier', 'partner_id': self._vendor(LISTED).id,
                'amount': 10.0, 'journal_id': self.company_data['default_journal_bank'].id,
                'ohmyfin_guard_state': 'no_match', 'ohmyfin_reviewed_by': self.reviewer.id})
        self.assertEqual(payment.ohmyfin_guard_state, 'none')
        self.assertFalse(payment.ohmyfin_reviewed_by)
        payment.action_post()
        self.assertEqual(payment.state, 'draft')

    def test_plain_user_pays_a_payee_with_no_match_as_the_author(self):
        """AC 5: the guard's writes go through sudo(), so a plain accountant's payment to a payee
        with no match posts and its state is stored; the payee's screen line is the accountant's."""
        payment = self.make_payment(self.vendor, user=self.plain_accountant)
        payment.action_post()
        self.assertNotEqual(payment.state, 'draft')
        self.assertEqual(payment.ohmyfin_guard_state, 'no_match')
        self.assertEqual(self.vendor.ohmyfin_screen_state, 'no_match')
        screen = self.module_messages(self.vendor, 'Sanctions screen:')
        self.assertEqual(len(screen), 1)
        self.assertEqual(screen.author_id, self.plain_accountant.partner_id)

    def test_held_payment_line_names_the_plain_user(self):
        vendor = self._vendor(LISTED)
        payment = self.make_payment(vendor, user=self.plain_accountant)
        payment.action_post()
        self.assertEqual(self.module_messages(payment, 'is listed').author_id, self.plain_accountant.partner_id)

    def test_reviewer_release_on_the_contact_lets_a_plain_users_payment_post(self):
        """AC 5: a reviewer's release recorded on the potential-match contact; the plain
        accountant's payment then posts, carrying the reviewer, and each line names who acted."""
        vendor = self._vendor(POTENTIAL)
        vendor._ohmyfin_screen()
        self.assertEqual(vendor.ohmyfin_screen_state, 'potential_match')
        as_reviewer = vendor.with_user(self.reviewer)
        as_reviewer.write({'ohmyfin_review_input': 'Different entity: other registration number.'})
        as_reviewer.action_ohmyfin_release()
        self.assertTrue(vendor.ohmyfin_review_current)
        self.assertEqual(self.module_messages(vendor, 'Released by').author_id, self.reviewer.partner_id)
        payment = self.make_payment(vendor, user=self.plain_accountant)
        payment.action_post()
        self.assertNotEqual(payment.state, 'draft')
        self.assertEqual(payment.ohmyfin_guard_state, 'released')
        self.assertEqual(payment.ohmyfin_reviewed_by, self.reviewer)
        self.assertIn('registration number', payment.ohmyfin_review_reason)
        released = self.module_messages(payment, 'Released by')
        self.assertEqual(len(released), 1)
        self.assertEqual(released.author_id, self.plain_accountant.partner_id)
