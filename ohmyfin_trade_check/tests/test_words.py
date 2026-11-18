"""AC 6: a word test over user-visible strings and index.html: 0 hits for the words PRD 0022 §4.7
forbids. Read from four places, so a new string cannot slip past by living somewhere else:

1. every string literal in the module's Python (docstrings and comments are for developers and
   are skipped; so are upper-case API values such as BLOCK);
2. the module's views as installed (text, string, help, placeholder, title);
3. its fields' labels, help texts and selection labels, its group and its job;
4. the static/description/index.html store page, and every panel and chatter line the module
   renders, for every state.
"""
import ast
import os
import re
from datetime import datetime

from lxml import etree

from odoo.tests import tagged

from ..models import ohmyfin_words as words
from .common import OhmyfinCase, trade_answer

FORBIDDEN = re.compile(r'\b(clear|cleared|safe|verified|compliant|GPI|tracking|track)\b', re.I)
# "BIC", never the other name; and a goods result is never a licence determination.
FORBIDDEN_EXACT = re.compile(r'\bSWIFT\b|(?i:no licen[cs]e (is )?(needed|required)|licen[cs]e not required)')
MODULE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def hits(text):
    return sorted({m.group(0) for m in FORBIDDEN.finditer(text or '')} | {m.group(0) for m in FORBIDDEN_EXACT.finditer(text or '')})


@tagged('post_install', '-at_install')
class TestWords(OhmyfinCase):

    def _python_strings(self):
        out = []
        for folder in ('models', ''):
            base = os.path.join(MODULE, folder)
            for name in sorted(os.listdir(base)):
                if not name.endswith('.py'):
                    continue
                path = os.path.join(base, name)
                tree = ast.parse(open(path, encoding='utf-8').read())
                docstrings = set()
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body \
                            and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], 'value', None), ast.Constant):
                        docstrings.add(id(node.body[0].value))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                        if re.fullmatch(r'[A-Z_]+|[a-z0-9_.]+', node.value):
                            continue  # API values (BLOCK, CLEAR) and keys (swift, hs_code), never shown as words
                        out.append(('%s/%s:%s' % (folder or '.', name, node.lineno), node.value))
        return out

    def test_python_strings(self):
        strings = self._python_strings()
        self.assertGreater(len(strings), 100)
        bad = [(where, text, hits(text)) for where, text in strings if hits(text)]
        self.assertEqual(bad, [])

    def test_views_fields_group_and_job(self):
        texts = []
        data = self.env['ir.model.data'].sudo().search([('module', '=', 'ohmyfin_trade_check')])
        for xid in data.filtered(lambda d: d.model == 'ir.ui.view'):
            arch = self.env['ir.ui.view'].sudo().browse(xid.res_id).arch_db
            root = etree.fromstring(arch.encode())
            for el in root.iter():
                if isinstance(el.tag, str):
                    texts.extend(t for t in (el.text, el.tail) if t and t.strip())
                    texts.extend(el.get(a) for a in ('string', 'help', 'placeholder', 'title', 'data-string') if el.get(a))
        self.assertTrue(any('Trade check' in t for t in texts))
        for model in ('res.partner', 'sale.order', 'account.payment', 'res.partner.bank', 'res.config.settings',
                      'product.template'):
            for name, field in self.env[model]._fields.items():
                if not name.startswith('ohmyfin_'):
                    continue
                texts.extend(t for t in (field.string, field.help) if t)
                if field.type == 'selection' and isinstance(field.selection, list):
                    texts.extend(label for _value, label in field.selection)
        group = self.env.ref('ohmyfin_trade_check.group_compliance_reviewer')
        texts.extend([group.name, group.comment or '', self.env.ref('ohmyfin_trade_check.ir_cron_ohmyfin_screen_pending').name])
        bad = [(t, hits(t)) for t in texts if hits(t)]
        self.assertEqual(bad, [])

    def test_store_page(self):
        path = os.path.join(MODULE, 'static', 'description', 'index.html')
        root = etree.HTML(open(path, encoding='utf-8').read())
        text = ' '.join(t for t in root.itertext())
        self.assertIn('Sanctions & Export Check', text)
        self.assertEqual(hits(text), [])

    def test_every_rendered_state(self):
        rendered = []
        now = datetime(2026, 10, 23, 8, 0)
        payload = {'customer': {'name': 'DEMO A', 'country': 'KZ'}, 'ship_to': {'name': 'DEMO A', 'country': 'KZ'},
                   'origin_country': 'DE', 'lines': [{'hs_code': '854231'}, {'hs_code': '847130'}, {},
                                                     {'service_type': 'engineering'}, {'service_type': 'other_service'}]}
        for case in ('no_findings', 'listed', 'potential', 'embargoed', 'prohibited_goods', 'licence', 'chp_r2',
                     'possible_controls', 'regimes', 'information_only', 'country_advisory', 'depends', 'required'):
            result = trade_answer(payload, case)
            for state in ('none', 'no_findings', 'review', 'released', 'refused', 'not_screened'):
                rendered.append(str(words.order_panel(self.env, state, result, {
                    'checked_at': now, 'reason': 'the service did not answer in time', 'review_reason': 'Reviewed.',
                    'reviewer': 'DEMO Reviewer', 'service_lines': 1, 'line_overrides': {'3': 'invalid_code'}})))
            decision = {'refused': 'refused', 'review_required': 'review', 'no_findings': 'no_findings'}[result['decision']]
            rendered.append(words.order_chatter(decision, result))
        for chp in words.CHP_LABELS.values():
            rendered.append(chp)
        rendered.extend(words.GOODS_LABELS.values())
        rendered.extend(words.SERVICE_LABELS.values())
        rendered.extend(words.LICENSE_STATUS_LABELS.values())
        rendered.extend(words.SERVICE_LICENSE_STATUS_LABELS.values())
        for kind in ('goods', 'service'):
            for license_status in LICENSE_STATUSES:
                rendered.append(words.line_result_text({'kind': kind, 'service_type': 'engineering', 'goods': {
                    'status': 'no_flag', 'license_status': license_status,
                    'jurisdictions_requiring_license': ['EU']}}))
        rendered.extend(words.CLASSIFICATION_LABELS.values())
        rendered.extend(label for _key, label in words.SERVICE_TYPES)
        for status in words.SERVICE_LABELS:
            rendered.append(words.line_result_text({'kind': 'service', 'service_type': 'other_service',
                                                    'goods': {'status': status, 'classification': 'service_screened'}}))
        rendered.extend([
            words.order_chatter('not_screened', {}, 'the service did not answer in time', confirmed=True),
            words.order_chatter('not_screened', {}, 'the service did not answer in time', confirmed=False),
            words.release_note('DEMO Reviewer', 'Different entity.', 'TC-1'),
        ])
        for state in ('no_match', 'potential_match', 'listed', 'not_screened', 'information_only'):
            rendered.append(words.badge(self.env, state, now, 312, 'the API key was refused', reviewer='DEMO Reviewer'))
            rendered.append(words.contact_note(self.env, state, 'SCR-1', now, 312,
                                               [{'name': 'X', 'list_name': 'EU Consolidated List', 'match_score': 0.9}],
                                               'the API key was refused'))
            rendered.append(words.payment_note(state, 'DEMO Vendor', 'SCR-1', 'the API key was refused', ['OFAC SDN'], True))
            rendered.append(words.payment_note(state, 'DEMO Vendor', 'SCR-1', 'the API key was refused', ['OFAC SDN'], False))
        bad = [(r, hits(r)) for r in rendered if hits(r)]
        self.assertEqual(bad, [])
        self.assertTrue(any('No match on 312 lists, ' in r for r in rendered))
        self.assertTrue(any(words.GOODS_FOOTNOTE in r.replace('&#39;', "'") for r in rendered))

    # ── changes/0051 ──────────────────────────────────────────────────────────────────────

    def test_service_line_is_worded_as_a_service(self):
        line = {'line': 2, 'service_type': 'engineering', 'kind': 'service', 'state': 'no_flag',
                'goods': {'status': 'no_flag', 'classification': 'service_screened'}}
        self.assertEqual(words.line_result_text(line), 'Service screened: no flag')
        line['goods'] = {'status': 'prohibited', 'classification': 'service_screened'}
        self.assertEqual(words.line_result_text(line), 'Service prohibited for this destination')
        line['goods'] = {'status': 'licence_may_be_required', 'jurisdictions_requiring_license': ['EU']}
        self.assertIn('for this service', words.line_result_text(line))
        line['goods'] = {'status': 'no_classification'}
        self.assertNotIn('HS code', words.line_result_text(line))
        other = {'kind': 'service', 'service_type': 'other_service', 'goods': {'status': 'not_goods_screened'}}
        self.assertEqual(words.line_result_text(other), "Not screened: service type 'other service' is not screened")
        # An answer without `kind` is read from the service type or the classification.
        self.assertEqual(words.line_kind({'service_type': 'software'}), 'service')
        self.assertEqual(words.line_kind({'goods': {'classification': 'service_screened'}}), 'service')
        self.assertEqual(words.line_kind({'hs_code': '854231'}), 'goods')
        self.assertEqual(words.line_kind({'kind': 'none'}), 'none')
        goods = {'goods': {'status': 'no_flag'}, 'kind': 'goods'}
        self.assertEqual(words.line_result_text(goods), 'No flag on the export-control lists')

    def test_possible_controls_wording(self):
        lines = words.possible_control_lines({'possible_controls': [
            {'list': 'EU dual-use', 'entry': '5A002', 'decides': 'designed for cryptography'},
            {'list': 'EU dual-use', 'entry': None, 'decides': 'rated for space use'},
            {'entry': '5A002'},          # nothing to say without `decides`
            'garbage',
        ]})
        self.assertEqual(lines, [
            'May be controlled if the item is designed for cryptography (EU dual-use 5A002): classify.',
            'May be controlled if the item is rated for space use (EU dual-use): classify.',
        ])
        self.assertEqual(words.possible_control_lines({}), [])
        self.assertEqual(words.possible_control_lines(None), [])
        self.assertEqual(hits(' '.join(lines)), [])

    def test_regimes_wording_and_missing_keys(self):
        from .common import REGIMES
        lines = words.regime_lines(REGIMES)
        self.assertEqual(lines, [
            'Arms embargo (UN and EU): countrywide',
            'Arms embargo (UK)',
            'Restrictive measures (UN and EU): arms embargo, asset freeze',
            'EAR country groups: D:1, E:1',
            'US: ITAR §126.1(d)(1), policy of denial',
            'EU general export authorisation EU001: this destination is not listed',
        ])
        self.assertEqual(hits(' '.join(lines)), [])
        # Every key is optional, and nothing odd raises.
        for regimes in (None, {}, 'garbage', [], {'eu_un_regimes': None, 'itar_126_1': None, 'eu001': None},
                        {'eu_un_regimes': ['x', {}], 'ear_country_groups': 'D:1'}):
            self.assertEqual(words.regime_lines(regimes), [], regimes)
        self.assertEqual(words.regime_lines({'eu_un_regimes': [], 'uk_arms_embargo': False}),
                         ['Arms embargo: none on record (UN, EU, UK)'])
        self.assertEqual(words.regime_lines({'eu001': True, 'itar_126_1': True}),
                         ['US: ITAR §126.1 country', 'EU general export authorisation EU001: this destination is listed'])
        self.assertEqual(words.regime_lines({'eu_un_regimes': [{'adopted_by': 'EU', 'arms_embargo': 'partial',
                                                                 'measures': [{'name': 'travel ban'}]}]}),
                         ['Arms embargo (EU): partial', 'Restrictive measures (EU): travel ban'])
        # The panel shows the block only when the answer carries one, and survives a bare destination.
        panel = str(words.order_panel(self.env, 'no_findings', {'destination': {'regimes': REGIMES}, 'lines': []}))
        self.assertIn('Destination regimes', panel)
        panel = str(words.order_panel(self.env, 'no_findings', {'destination': {'country': 'KZ'}, 'lines': []}))
        self.assertNotIn('Destination regimes', panel)

    def test_regimes_arms_embargo_summary_is_the_whole_answer(self):
        # The server's summary {UN|EU|UK|US: scope} names the embargoes; the per-regime and the
        # uk_arms_embargo lines that say the same are not repeated.
        regimes = dict(REGIMES_SUMMARY)
        lines = words.regime_lines(regimes)
        embargo_lines = [line for line in lines if line.startswith('Arms embargo')]
        self.assertEqual(embargo_lines, ['Arms embargo (EU): countrywide', 'Arms embargo (UK): countrywide'])
        self.assertEqual(len(lines), len(set(lines)), 'no duplicate line')
        self.assertNotIn('Arms embargo (UN and EU): countrywide', lines)
        self.assertNotIn('Arms embargo (UK)', lines)
        panel = str(words.order_panel(self.env, 'review', {'destination': {'regimes': regimes}, 'lines': []}))
        self.assertEqual(panel.count('Arms embargo (EU): countrywide'), 1)
        self.assertEqual(panel.count('Arms embargo (UK): countrywide'), 1)
        # An empty summary (as the live server sends it for AE) names nothing.
        self.assertEqual(words.regime_lines({'arms_embargo': [], 'eu_un_regimes': [], 'uk_arms_embargo': False})[0],
                         'Arms embargo: none on record (UN, EU, UK)')
        self.assertEqual(words.regime_lines({'arms_embargo': {}, 'eu_un_regimes': [], 'uk_arms_embargo': False})[0],
                         'Arms embargo: none on record (UN, EU, UK)')
        # As the live server sends SG and FR: uk_arms_embargo null, empty summary: still "none".
        self.assertEqual(words.regime_lines({'arms_embargo': [], 'eu_un_regimes': [], 'uk_arms_embargo': None})[0],
                         'Arms embargo: none on record (UN, EU, UK)')
        # An EU member state says so, and no EU001 line (EU001 is about exports out of the EU).
        fr = words.regime_lines({'arms_embargo': [], 'eu_member': True, 'eu001': False, 'ear_country_groups': ['A:1']})
        self.assertIn('EU member state (EU single market)', fr)
        self.assertFalse([line for line in fr if 'EU001' in line])

    def test_service_not_screened_shows_the_server_reason(self):
        line = {'line': 3, 'service_type': 'engineering', 'kind': 'service', 'state': 'not_goods_screened',
                'goods': {'status': 'not_accepted',
                          'reason': 'The export-control service does not screen this service type yet (HTTP 422).'}}
        text = words.line_result_text(line)
        self.assertEqual(text, 'Not screened: The export-control service does not screen this service type yet (HTTP 422)')
        line['goods']['status'] = 'no_classification'
        self.assertTrue(words.line_result_text(line).startswith('Not screened: The export-control service'))
        # Without a reason, the module's own words.
        del line['goods']['reason']
        self.assertEqual(words.line_result_text(line), 'Not screened: no rule is held for this service type')
        panel = str(words.order_panel(self.env, 'review', {'lines': [dict(line, goods={
            'status': 'not_accepted', 'reason': 'The export-control service does not screen this service type yet (HTTP 422).'})]},
            {'line_names': {'3': 'DEMO Engineering consulting'}}))
        self.assertIn('Not screened: The export-control service does not screen this service type yet (HTTP 422)', panel)
        self.assertEqual(hits(panel), [])

    # ── 1.1.2: goods.license_status and the information-only party ───────────────────────

    def test_goods_wording_by_license_status(self):
        def text(status, license_status, kind='goods', **goods):
            return words.line_result_text({'kind': kind, 'service_type': 'engineering', 'goods': dict(
                goods, status=status, license_status=license_status)})
        # country_advisory now comes with goods.status no_flag (it used to be licence_may_be_required).
        self.assertEqual(text('no_flag', 'country_advisory'),
                         'Destination advice: no control-list entry covers this item; see the reasons above')
        self.assertEqual(text('licence_may_be_required', 'depends_on_classification'),
                         'Classify the item: it may be controlled depending on its specification')
        self.assertEqual(text('licence_may_be_required', 'required', jurisdictions_requiring_license=['EU', 'US']),
                         "Licence may be required: check the item's classification (EU, US)")
        self.assertEqual(text('prohibited', 'prohibited'), 'Prohibited for this destination')
        # not_required keeps the flag wording: never a licence determination.
        self.assertEqual(text('no_flag', 'not_required'), 'No flag on the export-control lists')
        # A line that was not screened words by its status, whatever license_status says.
        self.assertEqual(text('not_goods_screened', 'country_advisory'), 'Not goods-screened: no HS code')
        self.assertEqual(text('prohibited', 'country_advisory'), 'Prohibited for this destination')
        # Services, worded as a service.
        self.assertEqual(text('no_flag', 'country_advisory', 'service'),
                         'Destination advice: no control-list entry covers this service; see the reasons above')
        self.assertEqual(text('licence_may_be_required', 'depends_on_classification', 'service'),
                         'Check what the service covers: it may be controlled depending on its scope')
        self.assertEqual(text('licence_may_be_required', 'required', 'service', jurisdictions_requiring_license=['EU']),
                         'Licence may be required for this service: check what it covers (EU)')
        self.assertEqual(text('prohibited', 'prohibited', 'service'), 'Service prohibited for this destination')
        self.assertEqual(text('no_flag', 'not_required', 'service'), 'Service screened: no flag')
        # An unknown value from a newer server falls back to the status words.
        self.assertEqual(text('no_flag', 'something_new'), 'No flag on the export-control lists')
        for kind in ('goods', 'service'):
            for license_status in LICENSE_STATUSES:
                self.assertEqual(hits(text('no_flag', license_status, kind)), [])

    def test_answer_without_license_status_words_as_1_1_1(self):
        expected = {
            'prohibited': 'Prohibited for this destination',
            'licence_may_be_required': "Licence may be required: check the item's classification (EU, US)",
            'no_flag': 'No flag on the export-control lists',
            'not_goods_screened': 'Not goods-screened: no HS code',
            'not_accepted': 'Not goods-screened: the service did not accept this HS code',
            'no_classification': 'Not goods-screened: no classification is held for this HS code',
        }
        for status, text in expected.items():
            line = {'kind': 'goods', 'goods': {'status': status, 'jurisdictions_requiring_license': ['EU', 'US']}}
            self.assertEqual(words.line_result_text(line), text, status)
        service = {'kind': 'service', 'service_type': 'engineering',
                   'goods': {'status': 'licence_may_be_required', 'jurisdictions_requiring_license': ['EU']}}
        self.assertEqual(words.line_result_text(service), 'Licence may be required for this service: check what it covers (EU)')
        # The fixture's older-server answer: no line carries license_status, the panel reads as before.
        payload = {'customer': {'name': 'DEMO A', 'country': 'KZ'}, 'ship_to': {'name': 'DEMO A', 'country': 'KZ'},
                   'lines': [{'hs_code': '854231'}]}
        result = trade_answer(payload, 'licence')
        self.assertNotIn('license_status', result['lines'][0]['goods'])
        panel = str(words.order_panel(self.env, 'review', result)).replace('&#39;', "'")
        self.assertIn("Licence may be required: check the item's classification (EU, US)", panel)

    def test_information_only_party_label(self):
        now = datetime(2026, 10, 23, 8, 0)
        self.assertEqual(words.PARTY_LABELS['information_only'], 'Information only (no hold)')
        self.assertTrue(words.badge(self.env, 'information_only', now, 312).startswith('Information only (no hold), '))
        # The contact and payee screens keep their three words: a MONITOR verdict is a potential match there.
        self.assertEqual(words.party_state('MONITOR', 1), 'potential_match')
        self.assertEqual(words.party_state('INFORM', 1), 'potential_match')
        payload = {'customer': {'name': 'DEMO Info Co', 'country': 'KZ'}, 'ship_to': {'name': 'DEMO Info Co', 'country': 'KZ'},
                   'lines': [{'hs_code': '854231'}]}
        result = trade_answer(payload, 'information_only')
        self.assertEqual(result['decision'], 'no_findings')
        panel = str(words.order_panel(self.env, 'no_findings', result, {'checked_at': now}))
        self.assertIn('Information only (no hold), ', panel)
        self.assertIn('DEMO Info Co (BIS export enforcement record, score 0.88)', panel)
        self.assertIn('BIS export enforcement record (MONITOR)', panel)   # the server's note
        self.assertNotIn('Potential match', panel)
        self.assertNotIn('green', panel.lower())
        chatter = words.order_chatter('no_findings', result)
        self.assertIn('no findings', chatter)
        self.assertIn('Customer: Information only (no hold): DEMO Info Co (BIS export enforcement record, score 0.88).',
                      chatter)
        self.assertNotIn('Ship-to:', chatter, 'the ship-to is the customer: said once')
        # A ship-to of its own is named; and the line rides a held order's chatter too.
        result['ship_to'] = dict(result['customer'], name='DEMO Ship Co', same_as_customer=False)
        self.assertIn('Ship-to: Information only (no hold): ', words.order_chatter('review', result))
        self.assertIn('Ship-to: Information only (no hold): ', words.order_chatter('refused', result))
        self.assertEqual(hits(panel + chatter), [])

    def test_information_only_is_read_from_the_boolean(self):
        """The 1.1.2 server keeps the 1.1.1 states: an information-only party is state
        potential_match plus `information_only: true`; every party carries the key, an older
        answer has none."""
        result = trade_answer({'customer': {'name': 'DEMO Info Co', 'country': 'KZ'},
                               'ship_to': {'name': 'DEMO Info Co', 'country': 'KZ'}, 'lines': []}, 'information_only')
        self.assertEqual(result['customer']['state'], 'potential_match')
        self.assertIs(result['customer']['information_only'], True)
        self.assertIs(result['ship_to']['information_only'], False)
        state = words.party_answer_state
        self.assertEqual(state({'state': 'potential_match', 'information_only': True}), 'information_only')
        self.assertEqual(state({'state': 'potential_match', 'information_only': False}), 'potential_match')
        self.assertEqual(state({'state': 'potential_match'}), 'potential_match', 'an older answer reads as before')
        self.assertEqual(state({'state': 'potential_match', 'information_only': 'yes'}), 'potential_match',
                         'only a JSON true counts')
        self.assertEqual(state({'state': 'information_only'}), 'information_only')
        self.assertEqual(state({'state': 'listed', 'information_only': True}), 'listed', 'a listing is never softened')
        self.assertEqual(state({'verdict': 'MONITOR', 'total_matches': 1, 'information_only': True}), 'information_only')
        # The panel says the label, not "Potential match"; an older answer still says "Potential match".
        older = trade_answer({'customer': {'name': 'DEMO P', 'country': 'KZ'},
                              'ship_to': {'name': 'DEMO P', 'country': 'KZ'}, 'lines': []}, 'potential')
        self.assertNotIn('information_only', older['customer'])
        self.assertIn('Potential match', str(words.order_panel(self.env, 'review', older)))
        self.assertNotIn('Information only', words.order_chatter('review', older))


LICENSE_STATUSES = ('prohibited', 'required', 'depends_on_classification', 'country_advisory', 'not_required')


REGIMES_SUMMARY = {
    'arms_embargo': {'EU': 'countrywide', 'UK': 'countrywide', 'UN': None},
    'eu_un_regimes': [{'id': 23, 'adopted_by': 'UN and EU', 'measures': ['asset freeze'], 'arms_embargo': 'countrywide'}],
    'uk_arms_embargo': True,
    'ear_country_groups': ['D:1'],
}
