"""AC 3 (order check fixtures: stored state, chatter line, confirm behaviour; a line with no HS
code renders "not goods-screened"), AC 4 (a reviewer's reason releases a waiting order; a
non-reviewer cannot record one), and AC 1's two shapes (with and without stock_delivery).

changes/0053 (1.1.1): a plain salesperson, the user the hold is for, cannot skip it: not by
writing the check's state or fingerprint, not by creating or copying an order that carries them,
not through a context default or a personal default, not with a context key."""
import requests

from odoo.exceptions import AccessError
from odoo.tests import tagged

from ..models.sale_order import PROTECTED
from .common import LISTED, OhmyfinCase

PROTECTED_LOGGER = 'odoo.addons.ohmyfin_trade_check.models.ohmyfin_protected'


@tagged('post_install', '-at_install')
class TestOrderCheck(OhmyfinCase):

    def _confirm(self, order, case):
        type(self).trade_case = case
        return order.action_confirm()

    def _trade_calls(self):
        return self.calls_to('/api/v5/trade/check')

    # ── what is sent ──────────────────────────────────────────────────────────────────────

    def test_payload_parties_route_and_lines(self):
        order = self.make_order(products=[self.ic, self.laptop, self.uncoded, self.service])
        self._confirm(order, 'no_findings')
        calls = self._trade_calls()
        self.assertEqual(len(calls), 1)
        body = self.body(calls[0])
        self.assertEqual(body['customer'], {'name': 'DEMO Almaty Distribution LLP', 'country': 'KZ'})
        self.assertEqual(body['ship_to'], {'name': 'DEMO Almaty Distribution LLP', 'country': 'KZ'})
        self.assertEqual(body['origin_country'], 'DE')
        # The service line is not goods: three goods lines go out, nothing else from the order.
        self.assertEqual(len(body['lines']), 3)
        self.assertEqual(set(body), {'customer', 'ship_to', 'origin_country', 'lines'})
        if self.has_hs:
            self.assertEqual([line.get('hs_code') for line in body['lines']], ['854231', '847130', None])
            self.assertEqual(body['lines'][0]['country_of_origin'], 'TW')
        else:
            # Without stock_delivery there is no HS code field: every line goes without one.
            self.assertEqual([line.get('hs_code') for line in body['lines']], [None, None, None])
        for line in body['lines']:
            self.assertLessEqual(set(line), {'hs_code', 'country_of_origin'})

    # ── changes/0051: services by their service type ───────────────────────────────────────

    def test_service_type_line_sends_service_type_and_no_hs_code(self):
        order = self.make_order(products=[self.ic, self.engineering, self.service])
        self._confirm(order, 'no_findings')
        lines = self.body(self._trade_calls()[0])['lines']
        # The typed service goes out by its type; the untyped one is not sent, as before.
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1], {'service_type': 'engineering'})
        for line in lines:
            self.assertFalse('hs_code' in line and 'service_type' in line, 'never both on one line')
        self.assertEqual(order.state, 'sale')
        html = order.ohmyfin_check_html
        self.assertIn('Service: Engineering', html)
        self.assertIn('Service screened: no flag', html)
        self.assertIn('1 service line(s) not screened: the product has no service type.', html)
        self.assertIn('1 service line(s) screened', self.last_message(order))
        # A service with a type is not a product "without an HS code" to fix.
        self.assertNotIn(self.engineering.id, order.action_ohmyfin_products_missing_hs()['domain'][0][2])

    def test_coded_line_never_sends_service_type(self):
        if not self.has_hs:
            self.skipTest('needs stock_delivery: without it no product carries a code')
        coded = self._product('DEMO Coded Part', '8542.31', 'base.tw')
        coded.ohmyfin_service_type = 'engineering'   # hidden on the form for a coded product
        uncoded_typed = self._product('DEMO Design Pack', '', 'base.de')
        uncoded_typed.ohmyfin_service_type = 'technology'
        odd = self._product('DEMO Odd Coded Part', 'n/a', 'base.de')
        odd.ohmyfin_service_type = 'engineering'
        order = self.make_order(products=[coded, uncoded_typed, odd])
        self._confirm(order, 'no_findings')
        lines = self.body(self._trade_calls()[0])['lines']
        self.assertEqual(lines[0], {'hs_code': '854231', 'country_of_origin': 'TW'})
        self.assertEqual(lines[1], {'service_type': 'technology'})
        # A malformed code is still a code: it is sent as one, never read as a service.
        self.assertEqual(lines[2], {'hs_code': 'n/a', 'country_of_origin': 'DE'})
        self.assertEqual(order.ohmyfin_check_state, 'review')
        self.assertIn('the HS code is not 4 to 10 digits', order.ohmyfin_check_html)

    def test_has_hs_code_flag_follows_the_code(self):
        if not self.has_hs:
            self.assertFalse(self.ic.product_tmpl_id.ohmyfin_has_hs_code)
            return
        self.assertTrue(self.ic.product_tmpl_id.ohmyfin_has_hs_code)
        self.assertFalse(self.uncoded.product_tmpl_id.ohmyfin_has_hs_code)
        self.assertFalse(self.engineering.product_tmpl_id.ohmyfin_has_hs_code)

    def test_other_service_reads_not_screened(self):
        order = self.make_order(products=[self.other_service])
        self._confirm(order, 'no_findings')
        self.assertEqual(self.body(self._trade_calls()[0])['lines'], [{'service_type': 'other_service'}])
        html = order.ohmyfin_check_html.replace('&#39;', "'")
        self.assertIn("Not screened: service type 'other service' is not screened", html)
        self.assertIn('1 not screened', self.last_message(order))

    def test_possible_controls_are_information_not_a_hold(self):
        self._needs_goods()
        order = self.make_order()
        self._confirm(order, 'possible_controls')
        self.assertEqual(order.state, 'sale', 'a possible control is shown, never a hold')
        html = order.ohmyfin_check_html.replace('&#39;', "'")
        self.assertIn('For information (line 1)', html)
        self.assertIn('May be controlled if the item is a digital oscilloscope with a sampling rate above '
                      '10 GS/s (EU dual-use 3A002.a): classify.', html)
        self.assertIn("Possibly controlled: it depends on the item's technical parameters", html)

    def test_destination_regimes_block(self):
        order = self.make_order()
        self._confirm(order, 'no_findings')
        self.assertNotIn('Destination regimes', order.ohmyfin_check_html)
        order = self.make_order()
        self._confirm(order, 'regimes')
        html = order.ohmyfin_check_html
        self.assertIn('Destination regimes', html)
        self.assertIn('Arms embargo (UN and EU): countrywide', html)
        self.assertIn('Arms embargo (UK)', html)
        self.assertIn('EAR country groups: D:1, E:1', html)
        self.assertIn('US: ITAR §126.1(d)(1), policy of denial', html)
        self.assertIn('EU001: this destination is not listed', html)

    def test_ship_to_is_the_delivery_address(self):
        ship = self.env['res.partner'].with_context(ohmyfin_no_screen=True).create({
            'name': 'DEMO Steppe Logistics LLP', 'is_company': True, 'country_id': self.env.ref('base.uz').id})
        order = self.make_order(ship_to=ship)
        self._confirm(order, 'no_findings')
        body = self.body(self._trade_calls()[0])
        self.assertEqual(body['ship_to'], {'name': 'DEMO Steppe Logistics LLP', 'country': 'UZ'})

    # ── AC 3: one fixture per result ──────────────────────────────────────────────────────

    def _assert_refused(self, case, words):
        order = self.make_order()
        action = self._confirm(order, case)
        self.assertEqual(order.state, 'draft', 'a refused order is not confirmed')
        self.assertEqual(order.ohmyfin_check_state, 'refused')
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'danger')
        note = self.last_message(order)
        self.assertIn('confirm refused', note)
        self.assertIn('TC-01JTESTCHECK', note)
        self.assertIn(words, note)
        self.assertIn('Confirm refused', order.ohmyfin_check_html)

    def test_listed_party_refuses(self):
        self._assert_refused('listed', 'BLOCK level')

    def test_embargoed_destination_refuses(self):
        self._assert_refused('embargoed', 'comprehensive embargo')

    def _needs_goods(self):
        if not self.has_hs:
            self.skipTest('goods findings need stock_delivery: without it every line is not goods-screened')

    def test_prohibited_goods_refuses(self):
        self._needs_goods()
        self._assert_refused('prohibited_goods', 'prohibited')

    def _assert_waiting(self, case, words):
        order = self.make_order()
        action = self._confirm(order, case)
        self.assertEqual(order.state, 'draft', 'a waiting order is not confirmed')
        self.assertEqual(order.ohmyfin_check_state, 'review')
        self.assertEqual(action['params']['type'], 'warning')
        note = self.last_message(order)
        self.assertIn('waiting for a compliance reviewer', note)
        self.assertIn(words, note)
        return order

    def test_potential_match_waits(self):
        self._assert_waiting('potential', 'potential match')

    def test_licence_possibly_required_waits(self):
        self._needs_goods()
        order = self._assert_waiting('licence', 'licence may be required')
        html = order.ohmyfin_check_html.replace('&#39;', "'")
        self.assertIn("Licence may be required: check the item's classification (EU, US)", html)

    def test_chp_item_to_diversion_risk_country_waits_with_actions(self):
        self._needs_goods()
        order = self._assert_waiting('chp_r2', 'common high priority')
        html = order.ohmyfin_check_html
        self.assertIn('due diligence required', html)
        self.assertIn('Tier 1; CHP list: Version of February 2024', html)
        self.assertIn('Required actions (line 1)', html)
        self.assertIn('Art. 12gb', html)
        self.assertIn('Art. 12g Reg.', html)

    def test_no_findings_confirms(self):
        order = self.make_order()
        result = self._confirm(order, 'no_findings')
        self.assertEqual(order.state, 'sale')
        self.assertTrue(result)
        self.assertEqual(order.ohmyfin_check_state, 'no_findings')
        self.assertEqual(order.ohmyfin_check_id, 'TC-01JTESTCHECK0000000000000%s' % 3)
        note = self.last_message(order)
        self.assertIn('no findings', note)
        html = order.ohmyfin_check_html
        self.assertIn('No match on 312 lists', html)
        self.assertIn('Report TC-01JTESTCHECK', html)

    def test_line_without_hs_code_reads_not_goods_screened(self):
        order = self.make_order()
        self._confirm(order, 'no_findings')
        html = order.ohmyfin_check_html
        self.assertIn('Not goods-screened: no HS code', html)
        self.assertEqual(order.ohmyfin_missing_hs_count, 1 if self.has_hs else 3)
        action = order.action_ohmyfin_products_missing_hs()
        self.assertEqual(action['res_model'], 'product.product')
        expected = self.uncoded if self.has_hs else (self.ic | self.laptop | self.uncoded)
        self.assertEqual(set(action['domain'][0][2]), set(expected.ids))
        if not self.has_hs:
            # AC 1, without stock_delivery: every line reads "not goods-screened", and the server
            # was sent no code at all.
            self.assertEqual(html.count('Not goods-screened: no HS code'), 3)
            self.assertNotIn('hs_code', self.calls_to('/api/v5/trade/check')[0].body.decode())

    def test_malformed_hs_code_is_sent_and_holds(self):
        if not self.has_hs:
            self.skipTest('needs stock_delivery')
        odd = self._product('DEMO Odd Part', 'n/a', 'base.de')
        order = self.make_order(products=[odd])
        self._confirm(order, 'no_findings')
        # Sent as typed (changes/0051): the server answers "not accepted" and the order waits.
        self.assertEqual(self.body(self._trade_calls()[0])['lines'], [{'hs_code': 'n/a', 'country_of_origin': 'DE'}])
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.ohmyfin_check_state, 'review')
        self.assertIn('the HS code is not 4 to 10 digits', order.ohmyfin_check_html)
        self.assertIn('Line 1 (HS n/a): the goods could not be screened', self.last_message(order))
        # A code that is there but malformed is not a product "without an HS code".
        self.assertEqual(order.ohmyfin_missing_hs_count, 0)

    def test_service_down_confirms_with_not_screened(self):
        type(self).fail_with = (503, {'error': 'screening_unavailable'})
        order = self.make_order()
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
        self.assertEqual(order.ohmyfin_check_state, 'not_screened')
        note = self.last_message(order)
        self.assertIn('not screened: the service is unavailable (HTTP 503)', note)
        self.assertIn('confirmed without the check', note)

    def test_timeout_and_quota_are_not_screened(self):
        for failure, words in ((requests.exceptions.Timeout(), 'did not answer in time'),
                               ((429, {'error': 'Rate limit exceeded'}), 'allowance of checks is used up')):
            type(self).fail_with = failure
            order = self.make_order()
            order.action_confirm()
            self.assertEqual(order.state, 'sale')
            self.assertIn(words, self.last_message(order))

    def test_service_down_with_refuse_switch_holds(self):
        self.activate(refuse=True)
        type(self).fail_with = (500, {})
        order = self.make_order()
        action = order.action_confirm()
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.ohmyfin_check_state, 'not_screened')
        self.assertEqual(action['tag'], 'display_notification')
        self.assertIn('was not confirmed', self.last_message(order))

    def test_customer_without_country_is_not_screened(self):
        nowhere = self.env['res.partner'].with_context(ohmyfin_no_screen=True).create(
            {'name': 'DEMO No Country Ltd', 'is_company': True})
        order = self.make_order(customer=nowhere)
        order.action_confirm()
        self.assertEqual(self._trade_calls(), [])
        self.assertEqual(order.ohmyfin_check_state, 'not_screened')
        self.assertIn('has no country', self.last_message(order))

    def test_check_button_does_not_confirm(self):
        order = self.make_order()
        type(self).trade_case = 'no_findings'
        order.action_ohmyfin_check()
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.ohmyfin_check_state, 'no_findings')

    # ── AC 4: the reviewer ────────────────────────────────────────────────────────────────

    def test_reviewer_reason_releases_and_confirms(self):
        order = self._assert_waiting('potential', 'potential match')
        calls = len(self._trade_calls())
        as_reviewer = order.with_user(self.reviewer)
        as_reviewer.write({'ohmyfin_review_input': 'Different company: the listed entity is registered in Iran.'})
        as_reviewer.action_ohmyfin_release()
        self.assertEqual(order.state, 'sale')
        self.assertEqual(order.ohmyfin_check_state, 'released')
        self.assertEqual(order.ohmyfin_reviewed_by, self.reviewer)
        self.assertIn('registered in Iran', order.ohmyfin_review_reason)
        self.assertEqual(len(self._trade_calls()), calls, 'a release confirms what was checked, with no new call')
        note = '\n'.join(self.messages(order))
        self.assertIn('Released by', note)
        self.assertIn('TC-01JTESTCHECK', note)
        self.assertIn('Released by a compliance reviewer', order.ohmyfin_check_html)

    def test_non_reviewer_cannot_record_a_reason(self):
        order = self._assert_waiting('potential', 'potential match')
        with self.assertRaises(AccessError):
            order.with_user(self.salesman).action_ohmyfin_release()
        with self.assertRaises(AccessError):
            order.with_user(self.salesman).write({'ohmyfin_review_input': 'I would like to ship this anyway.'})
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.ohmyfin_check_state, 'review')

    def test_a_refused_order_cannot_be_released(self):
        order = self.make_order()
        self._confirm(order, 'listed')
        order.with_user(self.reviewer).write({'ohmyfin_review_input': 'Trying to release a listed party anyway.'})
        with self.assertRaises(Exception):
            order.with_user(self.reviewer).action_ohmyfin_release()
        self.assertEqual(order.state, 'draft')

    def test_release_holds_only_for_what_was_checked(self):
        order = self._assert_waiting('potential', 'potential match')
        order.with_user(self.reviewer).write({'ohmyfin_review_input': 'Reviewed: not the listed entity.'})
        order.with_user(self.reviewer).action_ohmyfin_release()
        self.assertEqual(order.state, 'sale')

        # Back to quotation, unchanged: the release still covers it, no new call.
        order._action_cancel()
        order.action_draft()
        calls = len(self._trade_calls())
        self._confirm(order, 'potential')
        self.assertEqual(order.state, 'sale')
        self.assertEqual(len(self._trade_calls()), calls)

        # Back again, and a line is added: the release no longer covers the order.
        order._action_cancel()
        order.action_draft()
        order.write({'order_line': [(0, 0, {'product_id': self.uncoded.id, 'product_uom_qty': 2, 'price_unit': 10})]})
        self._confirm(order, 'potential')
        self.assertEqual(len(self._trade_calls()), calls + 1)
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.ohmyfin_check_state, 'review')

    def test_listed_name_on_the_order_never_reaches_a_log_line(self):
        customer = self.env['res.partner'].with_context(ohmyfin_no_screen=True).create(
            {'name': LISTED, 'is_company': True, 'country_id': self.kz.id})
        order = self.make_order(customer=customer)
        with self.assertLogs(level='DEBUG') as logs:
            self._confirm(order, 'listed')
        self.assertNotIn(LISTED, '\n'.join(logs.output))

    # ── changes/0053: the hold cannot be skipped by the users it holds ─────────────────────

    def _held_by_plain_salesman(self):
        """An order a plain salesperson (no reviewer group) created and tried to confirm, held
        for a reviewer. The record is in that salesperson's environment."""
        order = self.make_order(user=self.plain_salesman)
        self._confirm(order, 'potential')
        self.assertFalse(order.env.su)
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.ohmyfin_check_state, 'review')
        return order

    def test_every_stored_check_field_is_module_owned(self):
        self.assert_module_owned('sale.order', PROTECTED)

    def test_plain_user_writing_released_does_not_release(self):
        """AC 1."""
        order = self._held_by_plain_salesman()
        order.write({'ohmyfin_check_state': 'released'})
        self.assertEqual(order.ohmyfin_check_state, 'review')
        self._confirm(order, 'potential')
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.ohmyfin_check_state, 'review')

    def test_plain_user_writing_the_whole_release_does_not_release(self):
        """AC 1, the strongest form: state, the fingerprint the module would compute and a
        reviewer, in one write, and a warning in the log that carries no value."""
        order = self._held_by_plain_salesman()
        payload = order._ohmyfin_payload()[0]
        fingerprint = order._ohmyfin_fingerprint(payload)
        self.assertEqual(order.ohmyfin_check_fingerprint, fingerprint, 'the attacker can compute it')
        with self.assertLogs(PROTECTED_LOGGER, level='WARNING') as logs:
            order.write({'ohmyfin_check_state': 'released', 'ohmyfin_check_fingerprint': fingerprint,
                         'ohmyfin_reviewed_by': self.reviewer.id, 'ohmyfin_review_reason': 'I say so, honestly.',
                         'note': 'an ordinary field still saves'})
        self.assertIn('ohmyfin_check_state', logs.output[0])
        self.assertNotIn('I say so', '\n'.join(logs.output))
        self.assertIn('an ordinary field still saves', str(order.note))
        self.assertEqual(order.ohmyfin_check_state, 'review')
        self.assertFalse(order.ohmyfin_reviewed_by)
        self.assertFalse(order.ohmyfin_review_reason)
        calls = len(self._trade_calls())
        self._confirm(order, 'potential')
        self.assertEqual(order.state, 'draft')
        self.assertEqual(len(self._trade_calls()), calls + 1, 'checked again, not passed on a stored release')
        self.assertFalse(self.module_messages(order, 'Released by'))

    def test_skip_check_context_does_not_skip(self):
        """AC 2, the order."""
        order = self.make_order(user=self.plain_salesman)
        type(self).trade_case = 'potential'
        order.with_context(ohmyfin_skip_check=True).action_confirm()
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.ohmyfin_check_state, 'review')
        self.assertEqual(len(self._trade_calls()), 1)

    def test_create_carrying_a_release_is_created_unchecked(self):
        """AC 4: create() with the state, the fingerprint the module would compute and a reviewer."""
        held = self._held_by_plain_salesman()
        fingerprint = held.ohmyfin_check_fingerprint
        vals = dict(self.order_vals(), ohmyfin_check_state='released', ohmyfin_check_fingerprint=fingerprint,
                    ohmyfin_reviewed_by=self.reviewer.id, ohmyfin_check_id=held.ohmyfin_check_id)
        order = self.env['sale.order'].with_user(self.plain_salesman).create(vals)
        self.assertEqual(order._ohmyfin_fingerprint(order._ohmyfin_payload()[0]), fingerprint,
                         'the same parties and lines: the release would have matched')
        self.assertEqual(order.ohmyfin_check_state, 'none')
        self.assertFalse(order.ohmyfin_check_fingerprint)
        self.assertFalse(order.ohmyfin_reviewed_by)
        self.assertFalse(order.ohmyfin_check_id)
        self._confirm(order, 'potential')
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.ohmyfin_check_state, 'review')

    def test_copy_with_a_release_is_created_unchecked(self):
        held = self._held_by_plain_salesman()
        copy = held.copy({'ohmyfin_check_state': 'released', 'ohmyfin_check_fingerprint': held.ohmyfin_check_fingerprint})
        self.assertEqual(copy.ohmyfin_check_state, 'none')
        self.assertFalse(copy.ohmyfin_check_fingerprint)
        self._confirm(copy, 'potential')
        self.assertEqual(copy.state, 'draft')

    def test_context_default_cannot_carry_a_release(self):
        """A field left out of create() takes its default from the context, which a JSON-RPC
        caller sends: create() sets every module-owned field itself."""
        held = self._held_by_plain_salesman()
        fingerprint = held.ohmyfin_check_fingerprint
        orders = self.env['sale.order'].with_user(self.plain_salesman)
        by_context = orders.with_context(default_ohmyfin_check_state='released',
                                         default_ohmyfin_check_fingerprint=fingerprint).create(self.order_vals())
        self.assertEqual(by_context.ohmyfin_check_state, 'none')
        self.assertFalse(by_context.ohmyfin_check_fingerprint)
        self._confirm(by_context, 'potential')
        self.assertEqual(by_context.state, 'draft')

    def test_personal_default_cannot_carry_a_release(self):
        """Or from a personal default ("Set Defaults"): an ir.default for this user only, which
        Odoo lets any internal user record for themself."""
        held = self._held_by_plain_salesman()
        fingerprint = held.ohmyfin_check_fingerprint
        orders = self.env['sale.order'].with_user(self.plain_salesman)
        defaults = self.env['ir.default'].with_user(self.plain_salesman)
        defaults.set('sale.order', 'ohmyfin_check_state', 'released', user_id=True)
        defaults.set('sale.order', 'ohmyfin_check_fingerprint', fingerprint, user_id=True)
        self.assertEqual(orders.default_get(['ohmyfin_check_state'])['ohmyfin_check_state'], 'released')
        by_default = orders.create(self.order_vals())
        self.assertEqual(by_default.ohmyfin_check_state, 'none')
        self.assertFalse(by_default.ohmyfin_check_fingerprint)
        self._confirm(by_default, 'potential')
        self.assertEqual(by_default.state, 'draft')

    def test_plain_user_confirms_a_clean_order_as_the_author(self):
        """AC 5: the module's own writes go through sudo(), so a plain salesperson's clean order
        confirms and its check is stored; the chatter line is the salesperson's."""
        order = self.make_order(user=self.plain_salesman)
        self._confirm(order, 'no_findings')
        self.assertEqual(order.state, 'sale')
        self.assertEqual(order.ohmyfin_check_state, 'no_findings')
        self.assertEqual(order.ohmyfin_check_id, 'TC-01JTESTCHECK0000000000000%s' % 3)
        self.assertTrue(order.ohmyfin_check_fingerprint)
        self.assertTrue(order.ohmyfin_checked_at)
        notes = self.module_messages(order, 'no findings')
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes.author_id, self.plain_salesman.partner_id)

    def test_reviewer_releases_a_plain_users_held_order(self):
        """AC 5: the reviewer's release confirms it, the reviewer is recorded, and each chatter
        line names the user who acted."""
        order = self._held_by_plain_salesman()
        self.assertEqual(self.module_messages(order, 'waiting for a compliance reviewer').author_id,
                         self.plain_salesman.partner_id)
        as_reviewer = order.with_user(self.reviewer)
        as_reviewer.write({'ohmyfin_review_input': 'Different company: the listed entity is registered in Iran.'})
        as_reviewer.action_ohmyfin_release()
        self.assertEqual(order.state, 'sale')
        self.assertEqual(order.ohmyfin_check_state, 'released')
        self.assertEqual(order.ohmyfin_reviewed_by, self.reviewer)
        self.assertTrue(order.ohmyfin_reviewed_at)
        self.assertIn('registered in Iran', order.ohmyfin_review_reason)
        released = self.module_messages(order, 'Released by')
        self.assertEqual(len(released), 1)
        self.assertEqual(released.author_id, self.reviewer.partner_id)

    def test_plain_user_cannot_release(self):
        order = self._held_by_plain_salesman()
        with self.assertRaises(AccessError):
            order.write({'ohmyfin_review_input': 'I would like to ship this anyway.'})
        with self.assertRaises(AccessError):
            order.action_ohmyfin_release()
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.ohmyfin_check_state, 'review')
