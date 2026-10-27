"""AC 3 (order check fixtures: stored state, chatter line, confirm behaviour; a line with no HS
code renders "not goods-screened"), AC 4 (a reviewer's reason releases a waiting order; a
non-reviewer cannot record one), and AC 1's two shapes (with and without stock_delivery)."""
import requests

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import LISTED, OhmyfinCase


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

    def test_malformed_hs_code_is_not_sent_and_says_why(self):
        if not self.has_hs:
            self.skipTest('needs stock_delivery')
        odd = self._product('DEMO Odd Part', 'n/a', 'base.de')
        order = self.make_order(products=[odd])
        self._confirm(order, 'no_findings')
        self.assertEqual(self.body(self._trade_calls()[0])['lines'], [{'country_of_origin': 'DE'}])
        self.assertIn('the HS code is not 4 to 10 digits', order.ohmyfin_check_html)

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
