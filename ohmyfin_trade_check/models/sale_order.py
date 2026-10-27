"""The order check (PRD 0022 §4.1 item 3): one POST /api/v5/trade/check when a quotation is
confirmed, with the customer, the delivery address's name and country, the ship-from country
(the order's warehouse address, else the company's) and every goods line's HS code and country of
origin.

- Listed party, embargoed destination or prohibited goods: confirm is refused.
- Potential match, licence possibly required, or a CHP item to a diversion-risk country: confirm
  waits for a user in "Ohmyfin: compliance reviewer" to record a written reason.
- The service cannot answer: the order is confirmed with "not screened: <reason>" in the chatter,
  unless "refuse when the check cannot run" is on.

A refused or waiting order is NOT confirmed and the call returns a warning instead of raising: an
exception would roll the transaction back and take the stored result and the chatter line with
it. The result, the report id and the chatter line are what the reviewer and an auditor read.

A reviewer's release holds for exactly what was checked: change the customer, the address or a
line and the next confirm checks again.
"""
import hashlib
import json
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import ohmyfin_words as words
from .ohmyfin_client import OhmyfinClient, OhmyfinUnavailable
from .ohmyfin_review import REVIEWER_GROUP, take_reason

HS_DIGITS = re.compile(r'^\d{4,10}$')


def normalise_hs(value):
    return re.sub(r'[\s.\-]', '', (value or '').strip())


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    ohmyfin_check_state = fields.Selection([
        ('none', 'Not checked yet'),
        ('no_findings', 'No findings'),
        ('review', 'Waiting for a compliance reviewer'),
        ('released', 'Released by a compliance reviewer'),
        ('refused', 'Confirm refused'),
        ('not_screened', 'Not screened'),
    ], string='Trade check', default='none', readonly=True, copy=False)
    ohmyfin_check_json = fields.Text(readonly=True, copy=False)
    ohmyfin_check_id = fields.Char('Trade check report', readonly=True, copy=False)
    ohmyfin_checked_at = fields.Datetime('Checked on', readonly=True, copy=False)
    ohmyfin_check_reason = fields.Char('Why not screened', readonly=True, copy=False)
    ohmyfin_check_fingerprint = fields.Char(readonly=True, copy=False)
    ohmyfin_review_input = fields.Text('Reason to release', groups=REVIEWER_GROUP, copy=False)
    ohmyfin_review_reason = fields.Text('Reviewer reason', readonly=True, copy=False)
    ohmyfin_reviewed_by = fields.Many2one('res.users', 'Reviewed by', readonly=True, copy=False)
    ohmyfin_reviewed_at = fields.Datetime('Reviewed on', readonly=True, copy=False)
    ohmyfin_check_html = fields.Html('Trade check result', compute='_compute_ohmyfin_check_html', sanitize=True)
    ohmyfin_missing_hs_count = fields.Integer('Lines without an HS code', compute='_compute_ohmyfin_check_html')

    @api.depends('ohmyfin_check_state', 'ohmyfin_check_json', 'ohmyfin_checked_at', 'ohmyfin_check_reason',
                 'ohmyfin_review_reason', 'ohmyfin_reviewed_by', 'partner_id', 'partner_shipping_id',
                 'order_line.product_id', 'order_line.display_type')
    def _compute_ohmyfin_check_html(self):
        for order in self:
            _payload, _lines, extra = order._ohmyfin_payload()
            result = words.load(order.ohmyfin_check_json)
            extra.update({
                'checked_at': order.ohmyfin_checked_at,
                'reason': order.ohmyfin_check_reason,
                'review_reason': order.ohmyfin_review_reason,
                'reviewer': order.ohmyfin_reviewed_by.name,
            })
            order.ohmyfin_check_html = words.order_panel(self.env, order.ohmyfin_check_state, result, extra)
            order.ohmyfin_missing_hs_count = len(extra['missing_products'])

    # ── what is sent ──────────────────────────────────────────────────────────────────────

    def _ohmyfin_goods_fields(self):
        product_fields = self.env['product.product']._fields
        return 'hs_code' in product_fields, 'country_of_origin' in product_fields

    def _ohmyfin_origin_country(self):
        """The ship-from country: the order's warehouse address (when Inventory is installed),
        else the company's. A route input only: the seller is not screened."""
        self.ensure_one()
        warehouse = self.warehouse_id if 'warehouse_id' in self._fields else None
        country = warehouse.partner_id.country_id if warehouse else self.env['res.country']
        return (country or self.company_id.country_id).code or None

    def _ohmyfin_ship_to(self):
        self.ensure_one()
        ship = self.partner_shipping_id or self.partner_id
        customer = self.partner_id.commercial_partner_id
        name = ship.name if (ship.is_company or not ship.parent_id) and ship.name else ship.commercial_partner_id.name
        country = ship.country_id.code or customer.country_id.code
        return name, country

    def _ohmyfin_payload(self):
        """(payload, order lines in payload order, panel extras). Goods lines only: sections,
        notes, down payments and services carry no HS code and are not goods."""
        self.ensure_one()
        has_hs, has_origin = self._ohmyfin_goods_fields()
        customer = self.partner_id.commercial_partner_id
        ship_name, ship_country = self._ohmyfin_ship_to()

        goods_lines = self.env['sale.order.line']
        services = 0
        for line in self.order_line:
            if line.display_type or not line.product_id or getattr(line, 'is_downpayment', False):
                continue
            if getattr(line, 'is_delivery', False):
                continue
            if line.product_id.type != 'consu':
                services += 1
                continue
            goods_lines |= line

        lines = []
        names = {}
        overrides = {}
        missing = self.env['product.product']
        for n, line in enumerate(goods_lines, start=1):
            product = line.product_id
            names[str(n)] = product.display_name
            entry = {}
            raw = product.hs_code if has_hs else ''
            hs = normalise_hs(raw)
            if hs and HS_DIGITS.match(hs):
                entry['hs_code'] = hs
            else:
                missing |= product
                if hs:
                    overrides[str(n)] = 'invalid_code'
            if has_origin and product.country_of_origin:
                entry['country_of_origin'] = product.country_of_origin.code
            lines.append(entry)

        payload = {
            'customer': {'name': (customer.name or '').strip(), 'country': customer.country_id.code},
            'ship_to': {'name': (ship_name or '').strip(), 'country': ship_country},
            'lines': lines,
        }
        origin = self._ohmyfin_origin_country()
        if origin:
            payload['origin_country'] = origin
        extra = {'line_names': names, 'line_overrides': overrides, 'service_lines': services,
                 'missing_products': missing.ids}
        return payload, goods_lines, extra

    @staticmethod
    def _ohmyfin_fingerprint(payload):
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:40]

    # ── confirm ───────────────────────────────────────────────────────────────────────────

    def action_confirm(self):
        client = OhmyfinClient(self.env)
        if not client.is_active() or self.env.context.get('ohmyfin_skip_check'):
            return super().action_confirm()

        go = self.browse()
        held = []
        for order in self:
            if order.state not in ('draft', 'sent'):
                go |= order  # the standard refusal is the standard code's
                continue
            if order._ohmyfin_check(client, confirming=True):
                go |= order
            else:
                held.append(order)

        res = super(SaleOrder, go).action_confirm() if go else True
        if held:
            return self._ohmyfin_notification(held)
        return res

    def action_ohmyfin_check(self):
        """Run the check without confirming (one unit of the day's allowance)."""
        client = OhmyfinClient(self.env)
        if not client.is_active():
            raise UserError(_('Save an Ohmyfin API key and tick the consent box in Settings first.'))
        for order in self:
            order._ohmyfin_check(client, confirming=False)
        return True

    def _ohmyfin_check(self, client, confirming):
        """Check one order and store the result. True when the order may be confirmed."""
        self.ensure_one()
        payload, _lines, _extra = self._ohmyfin_payload()
        fingerprint = self._ohmyfin_fingerprint(payload)

        if confirming and self.ohmyfin_check_state == 'released' and self.ohmyfin_check_fingerprint == fingerprint:
            return True

        vals = {'ohmyfin_check_fingerprint': fingerprint, 'ohmyfin_review_reason': False,
                'ohmyfin_reviewed_by': False, 'ohmyfin_reviewed_at': False}
        try:
            if not payload['customer']['country'] or not payload['ship_to']['country']:
                raise OhmyfinUnavailable('the customer or the delivery address has no country')
            result = client.trade_check(payload)
        except OhmyfinUnavailable as exc:
            go = not client.refuse_when_unavailable
            vals.update({'ohmyfin_check_state': 'not_screened', 'ohmyfin_check_reason': exc.reason,
                         'ohmyfin_check_json': False, 'ohmyfin_check_id': False,
                         'ohmyfin_checked_at': fields.Datetime.now()})
            self.write(vals)
            self.message_post(body=words.order_chatter('not_screened', {}, exc.reason, confirmed=go and confirming))
            return go

        decision = result.get('decision')
        state = {'refused': 'refused', 'review_required': 'review', 'no_findings': 'no_findings'}.get(decision)
        if state is None:
            # An answer we cannot read is a check that did not run, never a pass.
            go = not client.refuse_when_unavailable
            reason = 'the service sent an answer that could not be read'
            vals.update({'ohmyfin_check_state': 'not_screened', 'ohmyfin_check_reason': reason,
                         'ohmyfin_check_json': False, 'ohmyfin_checked_at': fields.Datetime.now()})
            self.write(vals)
            self.message_post(body=words.order_chatter('not_screened', {}, reason, confirmed=go and confirming))
            return go

        vals.update({
            'ohmyfin_check_state': state,
            'ohmyfin_check_reason': False,
            'ohmyfin_check_json': json.dumps(result),
            'ohmyfin_check_id': result.get('check_id') or False,
            'ohmyfin_checked_at': fields.Datetime.now(),
        })
        self.write(vals)
        self.message_post(body=words.order_chatter(state, result))
        return state == 'no_findings'

    def _ohmyfin_notification(self, held):
        lines = []
        for order in held:
            label = words.DECISION_LABELS.get(order.ohmyfin_check_state, order.ohmyfin_check_state)
            if order.ohmyfin_check_state == 'not_screened' and order.ohmyfin_check_reason:
                label = 'Not screened: %s' % order.ohmyfin_check_reason
            lines.append('%s: %s' % (order.name, label))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Not confirmed'),
                'message': '\n'.join(lines) + '\n' + _('The result is on the order, under "Trade check".'),
                'type': 'danger' if any(o.ohmyfin_check_state == 'refused' for o in held) else 'warning',
                'sticky': True,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    # ── the reviewer ──────────────────────────────────────────────────────────────────────

    def action_ohmyfin_release(self):
        """A compliance reviewer releases a waiting order with the reason typed in "Reason to
        release", and the order is confirmed."""
        self.ensure_one()
        return self._ohmyfin_release(take_reason(self))

    def _ohmyfin_release(self, reason):
        """Record a compliance reviewer's reason on a waiting order, then confirm it."""
        self.ensure_one()
        if self.ohmyfin_check_state != 'review':
            raise UserError(_('Only an order waiting for a compliance reviewer can be released.'))
        self.write({
            'ohmyfin_check_state': 'released',
            'ohmyfin_review_reason': reason,
            'ohmyfin_reviewed_by': self.env.user.id,
            'ohmyfin_reviewed_at': fields.Datetime.now(),
        })
        self.message_post(body=words.release_note(self.env.user.name, reason, self.ohmyfin_check_id))
        return self.action_confirm()

    def action_ohmyfin_products_missing_hs(self):
        """The products on this order that carry no HS code, to fill them in."""
        self.ensure_one()
        _payload, _lines, extra = self._ohmyfin_payload()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Products without an HS code'),
            'res_model': 'product.product',
            'view_mode': 'list,form',
            'domain': [('id', 'in', extra['missing_products'])],
        }
