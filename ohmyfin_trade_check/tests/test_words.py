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
        for model in ('res.partner', 'sale.order', 'account.payment', 'res.partner.bank', 'res.config.settings'):
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
                   'origin_country': 'DE', 'lines': [{'hs_code': '854231'}, {'hs_code': '847130'}, {}]}
        for case in ('no_findings', 'listed', 'potential', 'embargoed', 'prohibited_goods', 'licence', 'chp_r2'):
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
        rendered.extend([
            words.order_chatter('not_screened', {}, 'the service did not answer in time', confirmed=True),
            words.order_chatter('not_screened', {}, 'the service did not answer in time', confirmed=False),
            words.release_note('DEMO Reviewer', 'Different entity.', 'TC-1'),
        ])
        for state in ('no_match', 'potential_match', 'listed', 'not_screened'):
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
