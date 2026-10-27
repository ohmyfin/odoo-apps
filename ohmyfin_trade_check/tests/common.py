"""Shared fixtures. HTTP is mocked at the one place Odoo's test framework routes every outgoing
request through (``BaseCase._request_handler``), so the module's real client, headers and error
handling run, and nothing ever leaves the box.

Every answer below is shaped as the Ohmyfin API answers it (POST /api/v5/sanctions/screen,
POST /api/v5/trade/check, GET /api/v5/banks): see docs/api-trade-check.md in the Ohmyfin repo.
"""
import json
import logging

import requests

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import new_test_user

from ..models.ohmyfin_client import set_param

API = 'https://api.ohmyfin.test'
KEY = 'prod-odootest-K3Y-7f1c9b2e4d6a8f0e'
LISTED = 'DEMO LISTED STATE EXPORTER JSC'
POTENTIAL = 'DEMO POSSIBLE MATCH TRADING'


def screen_answer(name):
    if name == LISTED:
        matches = [{'name': name, 'list_source': 'OFAC-SDN', 'list_name': 'Specially Designated Nationals',
                    'match_score': 0.98, 'action': 'BLOCK'}]
        action = 'BLOCK'
    elif name == POTENTIAL:
        matches = [{'name': name + ' LTD', 'list_source': 'EU-SANCTIONS', 'list_name': 'EU Consolidated List',
                    'match_score': 0.84, 'action': 'REVIEW'}]
        action = 'REVIEW'
    else:
        matches, action = [], 'CLEAR'
    return {
        'success': True, 'query': name, 'matches': matches, 'total_matches': len(matches),
        'recommended_action': action, 'report_id': 'SCR-00004242', 'screened_at': '2026-10-23T08:00:00+00:00',
        'lists_searched': {'count': 312, 'total_available': 312, 'full_coverage': True},
        'limits': {'daily': 9},
    }


def party(name, country, state='no_match', same=False):
    verdict = {'no_match': 'CLEAR', 'potential_match': 'REVIEW', 'listed': 'BLOCK'}[state]
    matches = [] if state == 'no_match' else [{'name': name, 'list_name': 'EU Consolidated List', 'match_score': 0.9}]
    return {'name': name, 'country': country, 'verdict': verdict, 'total_matches': len(matches), 'lists': [],
            'matches': matches, 'state': state, 'same_as_customer': same}


def trade_answer(payload, case='no_findings'):
    """The /api/v5/trade/check answer for one fixture case, line by line from the request."""
    customer = payload['customer']
    ship = payload['ship_to']
    lines = []
    refusals, reviews = [], []
    for n, line in enumerate(payload['lines'], start=1):
        hs = line.get('hs_code')
        if not hs:
            lines.append({'line': n, 'hs_code': None, 'country_of_origin': line.get('country_of_origin'),
                          'state': 'not_goods_screened', 'goods': {'status': 'not_goods_screened'},
                          'chp': {'status': 'no_hs_code'}})
            continue
        goods = {'status': 'no_flag', 'risk_level': 'low', 'jurisdictions_requiring_license': []}
        chp = {'status': 'no_rule', 'list_version': 'Version of February 2024'}
        state = 'no_flag'
        if case == 'prohibited_goods' and n == 1:
            goods['status'], state = 'prohibited', 'refused'
            refusals.append('Line 1 (HS %s): export to %s is prohibited under at least one jurisdiction.' % (hs, ship['country']))
        if case == 'licence' and n == 1:
            goods.update(status='licence_may_be_required', jurisdictions_requiring_license=['EU', 'US'])
            state = 'review'
            reviews.append("Line 1 (HS %s): an export licence may be required for %s (EU, US); check the item's classification." % (hs, ship['country']))
        if case == 'chp_r2' and n == 1:
            chp = {'status': 'enhanced_due_diligence', 'rule': 'chp_to_third_country', 'hs6': hs[:6], 'tier': '1',
                   'list_version': 'Version of February 2024',
                   'required_actions': ['Art. 12gb Reg. (EU) No 833/2014: carry out and document due diligence',
                                        'Art. 12g Reg. (EU) No 833/2014: include a no-re-export-to-Russia clause']}
            state = 'review'
            reviews.append('Line 1 (HS %s): an EU common high priority (CHP) item to %s.' % (hs, ship['country']))
        lines.append({'line': n, 'hs_code': hs, 'country_of_origin': line.get('country_of_origin'), 'state': state,
                      'goods': goods, 'chp': chp})

    cust_state = 'listed' if case == 'listed' else ('potential_match' if case == 'potential' else 'no_match')
    if case == 'listed':
        refusals.append('The customer matches a sanctions list entry at BLOCK level (OFAC SDN).')
    if case == 'potential':
        reviews.append('The customer has a potential match to review (EU Consolidated List).')
    embargo = case == 'embargoed'
    if embargo:
        refusals.append('The destination, %s, is under a comprehensive embargo (US, EU).' % ship['country'])
    decision = 'refused' if refusals else ('review_required' if reviews else 'no_findings')
    same = ship['name'] == customer['name'] and ship['country'] == customer['country']
    return {
        'check_id': 'TC-01JTESTCHECK0000000000000%s' % len(payload['lines']),
        'checked_at': '2026-10-23T08:00:00+00:00',
        'decision': decision,
        'refusal_reasons': refusals,
        'review_reasons': reviews,
        'notes': [],
        'lists_searched': 312,
        'customer': party(customer['name'], customer['country'], cust_state),
        'ship_to': party(ship['name'], ship['country'], 'no_match', same),
        'origin_country': payload.get('origin_country'),
        'destination': {'country': ship['country'], 'data_on_file': True, 'comprehensive_embargo': embargo,
                        'embargoed_by': ['US', 'EU'] if embargo else []},
        'country_controls': [],
        'goods_jurisdiction': 'ALL',
        'lines': lines,
        'limits': {'daily': 8},
    }


BANKS = {
    'COBADEFF': {'name': 'COMMERZBANK AG', 'swift': 'COBADEFF', 'country': 'DE', 'city': 'FRANKFURT AM MAIN'},
    'HALKKZKA': {'name': 'HALYK BANK OF KAZAKHSTAN', 'swift': 'HALKKZKA', 'country': 'KZ', 'city': 'ALMATY'},
}


class OhmyfinCase(AccountTestInvoicingCommon):
    """One company in Germany, an API key and the consent box set, HTTP mocked."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The accounting base runs as an accountant; the order tests need Sales rights too.
        group_field = 'group_ids' if 'group_ids' in cls.env['res.users']._fields else 'groups_id'
        cls.env.user.write({group_field: [Command.link(cls.env.ref('sales_team.group_sale_manager').id)]})
        cls.env.company.country_id = cls.env.ref('base.de')
        cls.calls = []
        cls.trade_case = 'no_findings'
        cls.fail_with = None          # (status, body) or an exception, for every request
        cls.has_hs = 'hs_code' in cls.env['product.product']._fields
        cls.has_origin = 'country_of_origin' in cls.env['product.product']._fields

        quiet = cls.env['res.partner'].with_context(ohmyfin_no_screen=True)
        cls.kz = cls.env.ref('base.kz')
        cls.customer = quiet.create({'name': 'DEMO Almaty Distribution LLP', 'is_company': True, 'country_id': cls.kz.id})
        cls.vendor = quiet.create({'name': 'DEMO Supplier Werkzeuge GmbH', 'is_company': True,
                                   'country_id': cls.env.ref('base.de').id})
        cls.ic = cls._product('DEMO Microcontroller', '8542.31', 'base.tw')
        cls.laptop = cls._product('DEMO Laptop', '847130', 'base.cn')
        cls.uncoded = cls._product('DEMO Cable', '', 'base.de')
        cls.service = cls.env['product.product'].create({'name': 'DEMO Installation', 'type': 'service'})

        cls.reviewer = new_test_user(
            cls.env, login='ohmyfin_reviewer',
            groups='base.group_user,sales_team.group_sale_manager,account.group_account_manager,'
                   'ohmyfin_trade_check.group_compliance_reviewer',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])
        cls.salesman = new_test_user(
            cls.env, login='ohmyfin_salesman',
            groups='base.group_user,sales_team.group_sale_manager,account.group_account_manager',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])

    @classmethod
    def _product(cls, name, hs, origin):
        vals = {'name': name, 'type': 'consu', 'list_price': 10.0}
        if 'hs_code' in cls.env['product.product']._fields:
            vals['hs_code'] = hs
        if 'country_of_origin' in cls.env['product.product']._fields:
            vals['country_of_origin'] = cls.env.ref(origin).id
        return cls.env['product.product'].create(vals)

    def setUp(self):
        super().setUp()
        type(self).calls = []
        type(self).trade_case = 'no_findings'
        type(self).fail_with = None
        self.activate()

    def activate(self, key=KEY, consent=True, refuse=False, auto=True):
        set_param(self.env, 'ohmyfin_trade_check.base_url', API)
        set_param(self.env, 'ohmyfin_trade_check.api_key', key or False)
        set_param(self.env, 'ohmyfin_trade_check.consent', 'True' if consent else False)
        set_param(self.env, 'ohmyfin_trade_check.refuse_when_unavailable', 'True' if refuse else False)
        set_param(self.env, 'ohmyfin_trade_check.auto_screen', 'True' if auto else 'False')

    # ── the fake service ──────────────────────────────────────────────────────────────────

    @classmethod
    def _request_handler(cls, s, r, /, **kw):
        if not r.url.startswith(API):
            return super()._request_handler(s, r, **kw)
        return fake_response(cls, r)

    def calls_to(self, path):
        return [c for c in self.calls if c.path_url.split('?')[0] == path]

    def body(self, call):
        return json.loads(call.body)

    # ── helpers ───────────────────────────────────────────────────────────────────────────

    def make_order(self, products=None, customer=None, ship_to=None):
        customer = customer or self.customer
        products = products if products is not None else [self.ic, self.laptop, self.uncoded]
        vals = {
            'partner_id': customer.id,
            'order_line': [Command.create({'product_id': p.id, 'product_uom_qty': 1, 'price_unit': 10.0}) for p in products],
        }
        if ship_to:
            vals['partner_shipping_id'] = ship_to.id
        return self.env['sale.order'].create(vals)

    def messages(self, record):
        return record.message_ids.mapped('body')

    def last_message(self, record):
        return record.message_ids.sorted('id')[-1:].body or ''

    def make_payment(self, partner, amount=100.0):
        return self.env['account.payment'].create({
            'payment_type': 'outbound',
            'partner_type': 'supplier',
            'partner_id': partner.id,
            'amount': amount,
            'journal_id': self.company_data['default_journal_bank'].id,
        })


def fake_response(cls, r):
    """Answer one request to the fake Ohmyfin API from the test class's fixture state."""
    cls.calls.append(r)
    if isinstance(cls.fail_with, Exception):
        raise cls.fail_with
    if cls.fail_with:
        status, body = cls.fail_with
    else:
        status, body = 200, answer(cls, r)
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    response.headers['Content-Type'] = 'application/json'
    response.encoding = 'utf-8'
    response.url = r.url
    response.request = r
    return response


def answer(cls, r):
    path = r.path_url.split('?')[0]
    if path == '/api/v5/sanctions/screen':
        return screen_answer(json.loads(r.body)['name'])
    if path == '/api/v5/trade/check':
        return trade_answer(json.loads(r.body), cls.trade_case)
    if path == '/api/v5/banks':
        query = requests.utils.unquote(r.url.split('query=')[1].split('&')[0])
        bank = BANKS.get(query) or BANKS.get(query[:8])
        return {'banks': [bank] if bank else [], 'count': 1 if bank else 0}
    return {}
