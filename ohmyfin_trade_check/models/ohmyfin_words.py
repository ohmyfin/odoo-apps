"""Every sentence and panel the module shows about a result. One place, so the word test
(tests/test_words.py) reads them all.

PRD 0022 §4.7, binding: never "clear", "cleared", "safe", "verified" or "compliant"; a no-match
is "No match on N lists, <date>"; a reviewed potential match stays grey, never green; a goods
result is a flag to check the item's classification, never a licence determination. "BIC", not
the other name. Nothing about tracking.
"""
import json

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
    checked = len([line for line in lines if line.get('hs_code')])
    text = 'Trade check%s: no findings. %s line(s) goods-checked' % (ref, checked)
    unchecked = len(lines) - checked
    if unchecked:
        text += ', %s not goods-screened' % unchecked
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

    lines = result.get('lines') or []
    names = extra.get('line_names') or {}
    if lines:
        body = []
        for line in lines:
            goods = line.get('goods') or {}
            chp = line.get('chp') or {}
            goods_text = GOODS_LABELS.get(goods.get('status'), goods.get('status') or '')
            if goods.get('status') == 'licence_may_be_required' and goods.get('jurisdictions_requiring_license'):
                goods_text += ' (%s)' % ', '.join(goods['jurisdictions_requiring_license'])
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
            body.append(Markup('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>') % (
                line.get('line'), product, line.get('hs_code') or '', goods_text, chp_text))
            actions = chp.get('required_actions') or []
            if actions:
                body.append(Markup('<tr><td></td><td colspan="4"><span style="font-weight: 600;">Required actions '
                                   '(line %s)</span><ul class="mb-0">%s</ul></td></tr>') % (
                    line.get('line'), Markup('').join(Markup('<li>%s</li>') % a for a in actions)))
        parts.append(Markup(
            '<table class="table table-sm table-bordered mb-2"><thead><tr><th>Line</th><th>Product</th>'
            '<th>HS code</th><th>Goods</th><th>CHP</th></tr></thead><tbody>%s</tbody></table>'
        ) % Markup('').join(body))
    if extra.get('service_lines'):
        parts.append(Markup('<p class="text-muted mb-1">%s</p>') % (
            '%s service line(s) not goods-screened: services carry no HS code.' % extra['service_lines']))
    parts.append(Markup('<p class="text-muted mb-0">%s</p>') % GOODS_FOOTNOTE)
    return Markup('').join(parts)


def escape_text(text):
    return escape(text)
