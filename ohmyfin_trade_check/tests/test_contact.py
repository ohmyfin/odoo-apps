"""Contact screening (§4.1 item 2): a screen per created company or person, stored state, badge,
top matches, report id and chatter note; re-screen only when name, type or country changes; bulk
imports screened by the job; a reviewer's reason on a potential match.

changes/0053 (1.1.1): the screen and a reviewer's release are written by the module only; a plain
user's create() or write() of them is left out, and the module's own screen still stores."""
from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from ..models.res_partner import PROTECTED
from .common import LISTED, POTENTIAL, OhmyfinCase


@tagged('post_install', '-at_install')
class TestContactScreen(OhmyfinCase):

    def _create(self, name, **vals):
        # A VAT too: Odoo 20 computes "is a company" from it.
        vals = dict({'name': name, 'is_company': True, 'vat': 'KZ%012d' % len(name), 'country_id': self.kz.id}, **vals)
        if not vals['is_company']:
            vals.pop('vat')
        return self.env['res.partner'].create(vals)

    def test_create_screens_once_with_name_type_and_country(self):
        partner = self._create('DEMO Fresh Customer LLP')
        calls = self.calls_to('/api/v5/sanctions/screen')
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.body(calls[0]), {
            'name': 'DEMO Fresh Customer LLP', 'entity_type': 'organization',
            'address': {'country': 'KZ'}, 'secondary_match_mode': 'annotate'})
        self.assertEqual(partner.ohmyfin_screen_state, 'no_match')
        self.assertEqual(partner.ohmyfin_report_id, 'SCR-00004242')
        self.assertEqual(partner.ohmyfin_lists_count, 312)
        self.assertTrue(partner.ohmyfin_badge.startswith('No match on 312 lists, '))
        self.assertIn('Sanctions screen: No match on 312 lists', self.last_message(partner))
        self.assertIn('SCR-00004242', self.last_message(partner))

    def test_person_is_screened_as_individual_where_odoo_knows(self):
        self._create('DEMO Jane Example', is_company=False)
        body = self.body(self.calls_to('/api/v5/sanctions/screen')[0])
        if self.env['res.partner']._fields['is_company'].compute:
            # Odoo 20: "not a company" only means "no VAT"; no type is better than a wrong one.
            self.assertNotIn('entity_type', body)
        else:
            self.assertEqual(body['entity_type'], 'individual')

    def test_listed_potential_and_failures_give_distinct_states(self):
        listed = self._create(LISTED)
        potential = self._create(POTENTIAL)
        type(self).fail_with = (429, {'error': 'Rate limit exceeded'})
        quota = self._create('DEMO Quota Customer')
        type(self).fail_with = (500, {})
        down = self._create('DEMO Outage Customer')

        self.assertEqual(listed.ohmyfin_screen_state, 'listed')
        self.assertTrue(listed.ohmyfin_badge.startswith('Listed, '))
        self.assertIn('Specially Designated Nationals', listed.ohmyfin_matches_html)
        self.assertEqual(potential.ohmyfin_screen_state, 'potential_match')
        self.assertTrue(potential.ohmyfin_badge.startswith('Potential match, '))
        self.assertIn('score 0.84', potential.ohmyfin_matches_html)
        self.assertEqual(quota.ohmyfin_screen_state, 'not_screened')
        self.assertIn('allowance', quota.ohmyfin_badge)
        self.assertEqual(down.ohmyfin_screen_state, 'not_screened')
        self.assertIn('HTTP 500', down.ohmyfin_badge)
        self.assertIn('Sanctions screen not run: not screened', self.last_message(down))

    def test_edit_that_changes_nothing_screened_makes_no_call(self):
        partner = self._create('DEMO Steady Customer')
        before = len(self.calls)
        partner.write({'phone': '+7 700 000 0000', 'email': 'orders@example.com', 'comment': 'note'})
        self.assertEqual(len(self.calls), before)
        partner.write({'name': 'DEMO Steady Customer'})
        self.assertEqual(len(self.calls), before, 'the same name is the same screen')
        partner.write({'name': 'DEMO Steady Customer Renamed'})
        self.assertEqual(len(self.calls), before + 1)
        partner.write({'country_id': self.env.ref('base.ae').id})
        self.assertEqual(len(self.calls), before + 2)

    def test_auto_screen_switch_off(self):
        self.activate(auto=False)
        partner = self._create('DEMO Quiet Customer')
        self.assertEqual(self.calls, [])
        partner.action_ohmyfin_screen()
        self.assertEqual(len(self.calls), 1)

    def test_delivery_address_and_own_users_are_not_screened(self):
        parent = self._create('DEMO Parent Company')
        before = len(self.calls)
        self.env['res.partner'].create({'name': 'Warehouse', 'type': 'delivery', 'parent_id': parent.id})
        self.assertEqual(len(self.calls), before)

    def test_bulk_create_is_left_to_the_job(self):
        partners = self.env['res.partner'].create([
            {'name': 'DEMO Bulk %s' % i, 'is_company': True, 'country_id': self.kz.id} for i in range(7)])
        self.assertEqual(self.calls, [])
        self.assertTrue(all(partners.mapped('ohmyfin_screen_pending')))
        self.env['res.partner']._cron_ohmyfin_screen_pending()
        self.assertEqual(len(self.calls_to('/api/v5/sanctions/screen')), 7)
        self.assertFalse(any(partners.mapped('ohmyfin_screen_pending')))

    def test_job_stops_when_the_allowance_is_spent(self):
        partners = self.env['res.partner'].create([
            {'name': 'DEMO Spent %s' % i, 'is_company': True, 'country_id': self.kz.id} for i in range(6)])
        type(self).fail_with = (429, {'error': 'Rate limit exceeded'})
        self.env['res.partner']._cron_ohmyfin_screen_pending()
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(all(partners.mapped('ohmyfin_screen_pending')))

    def test_reviewer_reason_on_a_potential_match_stays_a_potential_match(self):
        partner = self._create(POTENTIAL)
        as_reviewer = partner.with_user(self.reviewer)
        as_reviewer.write({'ohmyfin_review_input': 'Different entity: other registration number.'})
        as_reviewer.action_ohmyfin_release()
        self.assertTrue(partner.ohmyfin_review_current)
        self.assertEqual(partner.ohmyfin_screen_state, 'potential_match')
        self.assertIn('reviewed by', partner.ohmyfin_badge)
        with self.assertRaises(AccessError):
            partner.with_user(self.salesman).action_ohmyfin_release()

    # ── changes/0053: the screen is written by the module only ─────────────────────────────

    def test_every_stored_screen_field_is_module_owned(self):
        self.assert_module_owned('res.partner', PROTECTED)

    def test_plain_user_creates_a_listed_contact_as_not_screened(self):
        """A contact created with a screen in its values (and the screen at create turned off by
        context) is created not screened, and the payment guard screens it."""
        contacts = self.env['res.partner'].with_user(self.plain_accountant).with_context(
            ohmyfin_no_screen=True, default_ohmyfin_released_for='SCR-00004242@x')
        partner = contacts.create({
            'name': LISTED, 'is_company': True, 'country_id': self.kz.id,
            'ohmyfin_screen_state': 'no_match', 'ohmyfin_screened_at': fields.Datetime.now(),
            'ohmyfin_screen_key': 'k', 'ohmyfin_lists_count': 312, 'ohmyfin_report_id': 'SCR-1'})
        self.assertEqual(self.calls, [])
        self.assertEqual(partner.ohmyfin_screen_state, 'not_screened')
        self.assertFalse(partner.ohmyfin_screened_at)
        self.assertFalse(partner.ohmyfin_screen_key)
        self.assertFalse(partner.ohmyfin_released_for)
        self.assertEqual(partner.ohmyfin_lists_count, 0)
        payment = self.make_payment(partner, user=self.plain_accountant)
        payment.action_post()
        self.assertEqual(payment.state, 'draft')
        self.assertEqual(partner.ohmyfin_screen_state, 'listed')

    def test_plain_user_create_is_screened_by_the_module(self):
        """The module's own screen of a contact a plain user creates still stores (through
        sudo()), and its chatter line names that user."""
        partner = self.env['res.partner'].with_user(self.plain_salesman).create(
            {'name': POTENTIAL, 'is_company': True, 'vat': 'KZ000000000027', 'country_id': self.kz.id})
        self.assertEqual(partner.ohmyfin_screen_state, 'potential_match')
        self.assertEqual(partner.ohmyfin_report_id, 'SCR-00004242')
        self.assertTrue(partner.ohmyfin_screen_key)
        line = self.module_messages(partner, 'Sanctions screen:')
        self.assertEqual(len(line), 1)
        self.assertEqual(line.author_id, self.plain_salesman.partner_id)

    def test_plain_user_cannot_release_a_potential_match_by_writing_it(self):
        partner = self._create(POTENTIAL)
        partner.with_user(self.plain_salesman).write({
            'ohmyfin_released_for': partner._ohmyfin_screen_ref(), 'ohmyfin_reviewed_by': self.plain_salesman.id,
            'ohmyfin_review_reason': 'Reviewed by me, not a reviewer.', 'ohmyfin_screen_pending': True,
            'comment': 'an ordinary field still saves'})
        self.assertFalse(partner.ohmyfin_review_current)
        self.assertFalse(partner.ohmyfin_released_for)
        self.assertFalse(partner.ohmyfin_reviewed_by)
        self.assertFalse(partner.ohmyfin_screen_pending)
        self.assertIn('an ordinary field still saves', str(partner.comment))
        self.assertTrue(partner.ohmyfin_badge.startswith('Potential match, '))
        self.assertNotIn('reviewed by', partner.ohmyfin_badge)

    def test_ordinary_creates_log_no_warning(self):
        """A new user hands its partner the default screen state through _inherits; that, and a
        contact created the ordinary way, are not reported as left-out values."""
        with self.assertNoLogs('odoo.addons.ohmyfin_trade_check.models.ohmyfin_protected', level='WARNING'):
            new_test_user(self.env, login='ohmyfin_new_colleague', groups='base.group_user')
            self.env['res.partner'].with_user(self.plain_salesman).create({'name': 'DEMO Ordinary Contact'})
        with self.assertLogs('odoo.addons.ohmyfin_trade_check.models.ohmyfin_protected', level='WARNING'):
            self.env['res.partner'].with_user(self.plain_salesman).create(
                {'name': 'DEMO Claimed Contact', 'ohmyfin_screen_state': 'no_match'})

    def test_module_writes_from_the_superuser_still_store(self):
        """sudo() is the module's way in; nothing else changed for it."""
        partner = self._create('DEMO Sudo Customer')
        partner.sudo().write({'ohmyfin_screen_state': 'potential_match', 'ohmyfin_released_for': 'x@y'})
        self.assertEqual(partner.ohmyfin_screen_state, 'potential_match')
        self.assertEqual(partner.ohmyfin_released_for, 'x@y')
