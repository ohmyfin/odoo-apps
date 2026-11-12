"""Every sentence and panel the module shows about a result. One place, so the word test
(tests/test_words.py) reads them all.

PRD 0022 §4.7, binding: never "clear", "cleared", "safe", "verified" or "compliant"; a no-match
is "No match on N lists, <date>"; a reviewed potential match stays grey, never green; a goods
result is a flag to check the item's classification, never a licence determination. "BIC", not
the other name. Nothing about tracking.
"""
import json
import re

from markupsafe import Markup, escape

from odoo.tools.misc import format_date

PARTY_LABELS = {
    'no_match': 'No match',
    'potential_match': 'Potential match',
    'listed': 'Listed',
    'not_screened': 'Not screened',
}

DECISION_LABELS = {
    'none': 'Not checked yet',
    'no_findings': 'No findings',
    'review': 'Waiting for a compliance reviewer',
    'released': 'Released by a compliance reviewer',
    'refused': 'Confirm refused',
    'not_screened': 'Not screened',
}

GOODS_LABELS = {
    'prohibited': 'Prohibited for this destination',
    'licence_may_be_required': "Licence may be required: check the item's classification",
    'no_flag': 'No flag on the export-control lists',
    'not_goods_screened': 'Not goods-screened: no HS code',
    'invalid_code': 'Not goods-screened: the HS code is not 4 to 10 digits',
    'not_accepted': 'Not goods-screened: the service did not accept this HS code',
    'no_classification': 'Not goods-screened: no classification is held for this HS code',
}

# A service line (changes/0051): the same statuses as goods, worded as a service. The line's
# service type is filled in for 'not_goods_screened' ("service type 'x' is not screened").
SERVICE_LABELS = {
    'prohibited': 'Service prohibited for this destination',
    'licence_may_be_required': "Licence may be required for this service: check what it covers",
    'no_flag': 'Service screened: no flag',
    'not_goods_screened': "Not screened: service type '%s' is not screened",
    'not_accepted': 'Not screened: the service type was not accepted',
    'no_classification': 'Not screened: no rule is held for this service type',
}

# The product's service type, offered on the product form when it has no HS code. Keys are the
# API's values, sent as `service_type` on an order line.
SERVICE_TYPES = [
    ('technical_assistance', 'Technical assistance'),
    ('brokering', 'Brokering'),
    ('software', 'Software'),
    ('technology', 'Technology'),
    ('it_services', 'IT services'),
    ('engineering', 'Engineering'),
    ('architecture', 'Architecture'),
    ('legal_advisory', 'Legal advisory'),
    ('accounting_auditing_tax', 'Accounting, auditing and tax'),
    ('management_consulting', 'Management consulting'),
    ('public_relations', 'Public relations'),
    ('advertising', 'Advertising'),
    ('market_research', 'Market research'),
    ('financial_services', 'Financial services'),
    ('transport_logistics', 'Transport and logistics'),
    ('other_service', 'Other service'),
]
SERVICE_TYPE_LABELS = dict(SERVICE_TYPES)

# goods.classification: what the control lists hold for the HS code. Information only; the
# status above is what the order waits on.
CLASSIFICATION_LABELS = {
    'controlled': 'The HS code maps to a control-list entry',
    'possibly_controlled': "Possibly controlled: it depends on the item's technical parameters",
    'not_listed': 'The HS code maps to no control-list entry',
    'not_controlled': 'No control-list entry for this HS code',
}

CHP_LABELS = {
    'prohibited': 'EU common high priority (CHP) item: export to this destination is prohibited',
    'enhanced_due_diligence': 'EU common high priority (CHP) item to a diversion-risk country: due diligence required',
    'no_rule': 'CHP list checked, no rule applies',
    'not_checked': 'CHP list not checked',
    'not_accepted': 'CHP list not checked: the service did not accept this HS code',
    'no_origin': 'CHP list not checked: no ship-from country',
    'no_hs_code': '',
}

GOODS_FOOTNOTE = ("A goods result is a flag to check the item's classification, never a licence "
                  "determination: an HS code does not decide a dual-use control, the item's "
                  "technical parameters do.")

# Tones for the panel's header line. A reviewed or released result is grey, never green.
TONES = {
    'refused': '#b42318',
    'review': '#b54708',
    'not_screened': '#b54708',
    'released': '#475467',
    'no_findings': '#475467',
    'none': '#475467',
}


def party_state(verdict, total_matches=0):
    """Ohmyfin's verdict for one party, in the module's three words."""
    verdict = (verdict or '').upper()
    if verdict == 'BLOCK':
        return 'listed'
    if verdict == 'CLEAR' or (not verdict and not total_matches):
        return 'no_match'
    return 'potential_match'


def badge(env, state, screened_at=None, lists_count=None, reason=None, reviewer=None):
    """The contact badge: "No match on N lists, <date>", "Potential match, <date>", "Listed, <date>".
    A reviewed potential match says who reviewed it and stays a potential match."""
    if state == 'not_screened' or not state:
        return 'Not screened: %s' % reason if reason else 'Not screened'
    when = format_date(env, screened_at) if screened_at else ''
    if state == 'no_match':
        text = 'No match on %s lists' % lists_count if lists_count else 'No match'
    else:
        text = PARTY_LABELS.get(state, state)
    text = '%s, %s' % (text, when) if when else text
    if reviewer and state == 'potential_match':
        text += ', reviewed by %s' % reviewer
    return text


def match_lines(matches, limit=5):
    """"<name> (<list>, score 0.84)" per top match."""
    out = []
    for match in (matches or [])[:limit]:
        if not isinstance(match, dict):
            continue
        name = match.get('name') or '?'
        where = match.get('list_name') or match.get('list_source') or ''
        score = match.get('match_score')
        bits = [b for b in (where, ('score %.2f' % score) if isinstance(score, (int, float)) else '') if b]
        out.append('%s (%s)' % (name, ', '.join(bits)) if bits else name)
    return out


def contact_note(env, state, report_id=None, screened_at=None, lists_count=None, matches=None, reason=None):
    """The chatter line of one contact screen."""
    if state == 'not_screened':
        return 'Sanctions screen not run: not screened: %s.' % (reason or 'the check could not run')
    text = 'Sanctions screen: %s.' % badge(env, state, screened_at, lists_count)
    lines = match_lines(matches, 3)
    if lines and state != 'no_match':
        text += ' Top matches: %s.' % '; '.join(lines)
    if report_id:
        text += ' Report %s.' % report_id
    return text


def matches_html(matches):
    lines = match_lines(matches)
    if not lines:
        return Markup('')
    return Markup('<ul class="mb-0">%s</ul>') % Markup('').join(Markup('<li>%s</li>') % line for line in lines)


def load(text):
    try:
        data = json.loads(text or '{}')
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _party_text(env, party, lists_count, checked_at):
    if not party:
        return 'Not screened'
    state = party.get('state') or party_state(party.get('verdict'), party.get('total_matches'))
    text = badge(env, state, checked_at, lists_count)
    lines = match_lines(party.get('matches'), 3)
    if lines and state != 'no_match':
        text += ': %s' % '; '.join(lines)
    return text


def order_chatter(decision, result, reason=None, confirmed=None):
    """The chatter line of one order check."""
    check_id = (result or {}).get('check_id')
    ref = ' (report %s)' % check_id if check_id else ''
    if decision == 'not_screened':
        tail = ('The order was confirmed without the check.' if confirmed
                else 'The order was not confirmed: "refuse when the check cannot run" is on.')
        return 'Trade check not run%s: not screened: %s. %s' % (ref, reason or 'the check could not run', tail)
    reasons = list(result.get('refusal_reasons') or []) + list(result.get('review_reasons') or [])
    if decision == 'refused':
        return 'Trade check%s: confirm refused. %s' % (ref, ' '.join(result.get('refusal_reasons') or []))
    if decision == 'review':
        return ('Trade check%s: waiting for a compliance reviewer. %s' % (ref, ' '.join(result.get('review_reasons') or []))).strip()
    lines = result.get('lines') or []
    # By the line's STATE, not by whether it carries a code: since 01/11/2026 a
    # coded line can still come back not goods-screened, because Ohmyfin holds no
    # classification for that code. Counting codes called those lines checked.
    screened = [line for line in lines if line.get('state') != 'not_goods_screened']
    services = len([line for line in screened if line_kind(line) == 'service'])
    text = 'Trade check%s: no findings. %s line(s) goods-checked' % (ref, len(screened) - services)
    if services:
        text += ', %s service line(s) screened' % services
    unchecked = len(lines) - len(screened)
    if unchecked:
        text += ', %s not screened' % unchecked
    text += '.'
    if reasons:
        text += ' ' + ' '.join(reasons)
    return text


def release_note(user_name, reason, report=None):
    ref = ' (report %s)' % report if report else ''
    return 'Released by %s%s with the reason: %s' % (user_name, ref, reason)


def payment_note(state, vendor_name, report_id=None, reason=None, lists=None, posted=None):
    ref = ' Report %s.' % report_id if report_id else ''
    if state == 'listed':
        listed_on = ' (%s)' % ', '.join(lists) if lists else ''
        return 'Payee screen: %s is listed%s. The payment was not posted.%s' % (vendor_name, listed_on, ref)
    if state == 'potential_match':
        return ("Payee screen: %s has a potential match. The payment waits for a compliance reviewer's "
                "reason.%s" % (vendor_name, ref))
    if state == 'not_screened':
        tail = ('The payment was posted without the screen.' if posted
                else 'The payment was not posted: "refuse when the check cannot run" is on.')
        return 'Payee not screened: %s. %s' % (reason or 'the check could not run', tail)
    return 'Payee screen: %s.%s' % (PARTY_LABELS.get(state, state), ref)


def line_kind(line):
    """'goods', 'service' or 'none'. The server sends `kind`; an older answer does not, and a
    line is then a service when it carries a service type or a service classification."""
    kind = line.get('kind')
    if kind in ('goods', 'service', 'none'):
        return kind
    goods = line.get('goods') if isinstance(line.get('goods'), dict) else {}
    if line.get('service_type') or goods.get('classification') == 'service_screened':
        return 'service'
    return 'goods'


def service_type_label(value):
    return SERVICE_TYPE_LABELS.get(value, (value or '').replace('_', ' '))


def line_result_text(line):
    """The goods or service result of one answer line, as shown in the panel."""
    goods = line.get('goods') if isinstance(line.get('goods'), dict) else {}
    status = goods.get('status')
    if line_kind(line) == 'service':
        text = SERVICE_LABELS.get(status, status or '')
        if status == 'not_goods_screened':
            text = text % service_type_label(line.get('service_type')).lower()
        elif status in ('not_accepted', 'no_classification') and goods.get('reason'):
            # The server says why (e.g. it does not screen this service type yet).
            text = 'Not screened: %s' % goods['reason'].rstrip('.')
    else:
        text = GOODS_LABELS.get(status, status or '')
    if status == 'licence_may_be_required' and goods.get('jurisdictions_requiring_license'):
        text += ' (%s)' % ', '.join(goods['jurisdictions_requiring_license'])
    return text


def possible_control_lines(goods):
    """goods.possible_controls as information on the line, never as a hold:
    "May be controlled if the item is <decides> (<list> <entry>): classify." """
    out = []
    for item in (goods or {}).get('possible_controls') or []:
        if not isinstance(item, dict) or not item.get('decides'):
            continue
        where = ' '.join(str(b) for b in (item.get('list'), item.get('entry')) if b)
        text = 'May be controlled if the item is %s' % item['decides']
        if where:
            text += ' (%s)' % where
        out.append(text + ': classify.')
    return out


def _measure_text(measure):
    if isinstance(measure, dict):
        return measure.get('label') or measure.get('name') or measure.get('type') or ''
    return str(measure) if measure else ''


def _itar_text(value):
    match = re.fullmatch(r'([a-z])(\d*)', str(value)) if isinstance(value, str) else None
    if match:
        policy = {'d1': ', policy of denial', 'd2': ', denial with exceptions'}.get(value, '')
        return 'US: ITAR §126.1(%s)%s%s' % (match.group(1), '(%s)' % match.group(2) if match.group(2) else '', policy)
    return 'US: ITAR §126.1 country' if value is True else 'US: ITAR §126.1 (%s)' % value


def regime_lines(regimes):
    """The order-level "Destination regimes" block, from destination.regimes. Every key is
    optional; an absent or empty answer gives no lines, and nothing here raises."""
    if not isinstance(regimes, dict):
        return []
    out = []
    eu_un = regimes.get('eu_un_regimes')
    embargoes = []
    # The server's own summary, {UN|EU|UK|US: scope}; when it names any, it is the whole answer.
    arms = regimes.get('arms_embargo')
    arms = {k: v for k, v in arms.items() if v} if isinstance(arms, dict) else {}
    for by, scope in sorted(arms.items()):
        if by == 'US' and regimes.get('itar_126_1'):
            continue  # the ITAR line below is what the US scope is
        embargoes.append('Arms embargo (%s): %s' % (by, str(scope).replace('_', ' ')))
    for regime in eu_un if isinstance(eu_un, list) else []:
        if not isinstance(regime, dict):
            continue
        by = regime.get('adopted_by') or 'UN or EU'
        measures = [m for m in (_measure_text(x) for x in regime.get('measures') or []) if m]
        if regime.get('arms_embargo') and not arms:
            embargoes.append('Arms embargo (%s): %s' % (by, str(regime['arms_embargo']).replace('_', ' ')))
        if measures:
            out.append('Restrictive measures (%s): %s' % (by, ', '.join(measures)))
    uk = regimes.get('uk_arms_embargo')
    if uk and 'UK' not in arms:
        embargoes.append('Arms embargo (UK)' + (': %s' % str(uk).replace('_', ' ') if isinstance(uk, str) else ''))
    if embargoes:
        out = embargoes + out
    elif isinstance(regimes.get('arms_embargo'), (dict, list)) or (isinstance(eu_un, list) and uk is False):
        # The server's summary is present and names none (or, older answers, UK said false).
        out.insert(0, 'Arms embargo: none on record (UN, EU, UK)')
    groups = regimes.get('ear_country_groups')
    if isinstance(groups, list) and groups:
        out.append('EAR country groups: %s' % ', '.join(str(g) for g in groups))
    if regimes.get('itar_126_1'):
        out.append(_itar_text(regimes['itar_126_1']))
    if regimes.get('eu_member') is True:
        out.append('EU member state (EU single market)')
    elif regimes.get('eu001') is True:
        out.append('EU general export authorisation EU001: this destination is listed')
    elif regimes.get('eu001') is False:
        out.append('EU general export authorisation EU001: this destination is not listed')
    return out


def order_panel(env, state, result, extra=None):
    """The result panel on the sales order (§4.1 item 3): party, destination, goods line by line."""
    extra = extra or {}
    tone = TONES.get(state, '#475467')
    parts = []
    head = DECISION_LABELS.get(state, state)
    if state == 'not_screened' and extra.get('reason'):
        head = 'Not screened: %s' % extra['reason']
    parts.append(Markup('<p style="color: %s; font-weight: 600;" class="mb-2">%s</p>') % (tone, head))
    if not result:
        return Markup('').join(parts)

    checked_at = extra.get('checked_at')
    lists_count = result.get('lists_searched')
    meta = []
    if result.get('check_id'):
        meta.append('Report %s' % result['check_id'])
    if checked_at:
        meta.append(format_date(env, checked_at))
    if meta:
        parts.append(Markup('<p class="text-muted mb-2">%s</p>') % ', '.join(meta))
    if state == 'released' and extra.get('review_reason'):
        parts.append(Markup('<p class="mb-2" style="color: #475467;">%s</p>') % release_note(
            extra.get('reviewer') or '', extra['review_reason'], result.get('check_id')))

    review_title = 'Reviewed' if state == 'released' else 'To review'
    for title, key in (('Refused because', 'refusal_reasons'), (review_title, 'review_reasons'), ('Notes', 'notes')):
        items = result.get(key) or []
        if items:
            parts.append(Markup('<p class="mb-1" style="font-weight: 600;">%s</p><ul>%s</ul>') % (
                title, Markup('').join(Markup('<li>%s</li>') % i for i in items)))

    customer = result.get('customer') or {}
    ship_to = result.get('ship_to') or {}
    rows = [('Customer', '%s (%s): %s' % (customer.get('name', ''), customer.get('country', ''),
                                          _party_text(env, customer, lists_count, checked_at)))]
    if ship_to.get('same_as_customer'):
        rows.append(('Ship-to', 'the customer'))
    else:
        rows.append(('Ship-to', '%s (%s): %s' % (ship_to.get('name', ''), ship_to.get('country', ''),
                                                 _party_text(env, ship_to, lists_count, checked_at))))
    destination = result.get('destination') or {}
    if destination.get('comprehensive_embargo'):
        dest = 'under a comprehensive embargo (%s)' % ', '.join(destination.get('embargoed_by') or [])
    elif destination.get('data_on_file'):
        dest = 'no country-wide embargo on record'
    else:
        dest = 'no export-control data on file'
    rows.append(('Destination', '%s: %s' % (destination.get('country', ''), dest)))
    if result.get('origin_country'):
        rows.append(('Ships from', result['origin_country']))
    parts.append(Markup('<table class="table table-sm mb-2"><tbody>%s</tbody></table>') % Markup('').join(
        Markup('<tr><td style="font-weight: 600;">%s</td><td>%s</td></tr>') % row for row in rows))
    regimes = regime_lines(destination.get('regimes'))
    if regimes:
        parts.append(Markup('<p class="mb-1" style="font-weight: 600;">Destination regimes</p><ul>%s</ul>') % (
            Markup('').join(Markup('<li>%s</li>') % r for r in regimes)))

    lines = result.get('lines') or []
    names = extra.get('line_names') or {}
    if lines:
        body = []
        for line in lines:
            goods = line.get('goods') if isinstance(line.get('goods'), dict) else {}
            chp = line.get('chp') if isinstance(line.get('chp'), dict) else {}
            is_service = line_kind(line) == 'service'
            goods_text = line_result_text(line)
            override = (extra.get('line_overrides') or {}).get(str(line.get('line')))
            if override:
                goods_text = GOODS_LABELS.get(override, goods_text)
            chp_text = CHP_LABELS.get(chp.get('status'), chp.get('status') or '')
            if chp.get('status') in ('prohibited', 'enhanced_due_diligence'):
                details = [d for d in ('Tier %s' % chp['tier'] if chp.get('tier') else '',
                                       'CHP list: %s' % chp['list_version'] if chp.get('list_version') else '') if d]
                if details:
                    chp_text += ' (%s)' % '; '.join(details)
            product = Markup('%s') % names.get(str(line.get('line')), '')
            if line.get('country_of_origin'):
                product += Markup('<br/><span class="text-muted">made in %s</span>') % line['country_of_origin']
            classification = CLASSIFICATION_LABELS.get(goods.get('classification')) if not is_service else None
            if classification:
                goods_text = Markup('%s<br/><span class="text-muted">%s</span>') % (goods_text, classification)
            code = ('Service: %s' % service_type_label(line.get('service_type'))) if is_service \
                else (line.get('hs_code') or '')
            body.append(Markup('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>') % (
                line.get('line'), product, code, goods_text, chp_text))
            controls = possible_control_lines(goods)
            if controls:
                body.append(Markup('<tr><td></td><td colspan="4"><span style="font-weight: 600;">For information '
                                   '(line %s)</span><ul class="mb-0">%s</ul></td></tr>') % (
                    line.get('line'), Markup('').join(Markup('<li>%s</li>') % c for c in controls)))
            actions = chp.get('required_actions') or []
            if actions:
                body.append(Markup('<tr><td></td><td colspan="4"><span style="font-weight: 600;">Required actions '
                                   '(line %s)</span><ul class="mb-0">%s</ul></td></tr>') % (
                    line.get('line'), Markup('').join(Markup('<li>%s</li>') % a for a in actions)))
        parts.append(Markup(
            '<table class="table table-sm table-bordered mb-2"><thead><tr><th>Line</th><th>Product</th>'
            '<th>HS code or service</th><th>Result</th><th>CHP</th></tr></thead><tbody>%s</tbody></table>'
        ) % Markup('').join(body))
    if extra.get('service_lines'):
        parts.append(Markup('<p class="text-muted mb-1">%s</p>') % (
            '%s service line(s) not screened: the product has no service type.' % extra['service_lines']))
    parts.append(Markup('<p class="text-muted mb-0">%s</p>') % GOODS_FOOTNOTE)
    return Markup('').join(parts)


def escape_text(text):
    return escape(text)
