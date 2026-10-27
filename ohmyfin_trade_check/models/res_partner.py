"""Contact screening (PRD 0022 §4.1 item 2): a Screen button, a badge, the top matches, the
report id, a chatter note per screen, and a re-screen when a company or person is created or
its name, type or country changes (the "screen contacts automatically" switch, default on).

A creation or an edit of one or a few contacts is screened at once; a bulk import is marked
pending and screened by the hourly job, so an import of thousands of contacts neither waits on
thousands of calls nor spends the day's allowance in one go.
"""
import hashlib
import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import ohmyfin_words as words
from .ohmyfin_client import OhmyfinClient, OhmyfinUnavailable
from .ohmyfin_review import REVIEWER_GROUP, take_reason

_logger = logging.getLogger(__name__)

SCREEN_FIELDS = ('name', 'is_company', 'company_type', 'country_id', 'type', 'parent_id')
# A create or write of more records than this at once is screened by the job, not inline.
INLINE_LIMIT = 5
# A payee screened longer ago than this is screened again before a payment (§4.1 item 4).
STALE_DAYS = 30
CRON_BATCH = 20


class ResPartner(models.Model):
    _inherit = 'res.partner'

    ohmyfin_screen_state = fields.Selection([
        ('not_screened', 'Not screened'),
        ('no_match', 'No match'),
        ('potential_match', 'Potential match'),
        ('listed', 'Listed'),
    ], string='Sanctions screen', default='not_screened', readonly=True, copy=False)
    ohmyfin_screened_at = fields.Datetime('Screened on', readonly=True, copy=False)
    ohmyfin_lists_count = fields.Integer('Lists searched', readonly=True, copy=False)
    ohmyfin_report_id = fields.Char('Screening report', readonly=True, copy=False)
    ohmyfin_screen_reason = fields.Char('Why not screened', readonly=True, copy=False)
    ohmyfin_matches_json = fields.Text(readonly=True, copy=False)
    ohmyfin_screen_key = fields.Char(readonly=True, copy=False)
    ohmyfin_screen_pending = fields.Boolean(readonly=True, copy=False)
    # A compliance reviewer's reason on a potential match. It holds for the screen it was given
    # for (report id and date): the next screen needs a new reason.
    ohmyfin_review_input = fields.Text('Reason to release', groups=REVIEWER_GROUP, copy=False)
    ohmyfin_review_reason = fields.Text('Reviewer reason', readonly=True, copy=False)
    ohmyfin_reviewed_by = fields.Many2one('res.users', 'Reviewed by', readonly=True, copy=False)
    ohmyfin_reviewed_at = fields.Datetime('Reviewed on', readonly=True, copy=False)
    ohmyfin_released_for = fields.Char(readonly=True, copy=False)
    ohmyfin_review_current = fields.Boolean('Reviewed', compute='_compute_ohmyfin_badge')
    ohmyfin_badge = fields.Char('Sanctions status', compute='_compute_ohmyfin_badge')
    ohmyfin_matches_html = fields.Html('Top matches', compute='_compute_ohmyfin_badge', sanitize=True)

    @api.depends('ohmyfin_screen_state', 'ohmyfin_screened_at', 'ohmyfin_lists_count', 'ohmyfin_matches_json',
                 'ohmyfin_screen_reason', 'ohmyfin_released_for', 'ohmyfin_reviewed_by')
    def _compute_ohmyfin_badge(self):
        for partner in self:
            partner.ohmyfin_review_current = partner._ohmyfin_released()
            partner.ohmyfin_badge = words.badge(
                self.env, partner.ohmyfin_screen_state, partner.ohmyfin_screened_at,
                partner.ohmyfin_lists_count, partner.ohmyfin_screen_reason,
                reviewer=partner.ohmyfin_reviewed_by.name if partner.ohmyfin_review_current else None)
            matches = words.load(partner.ohmyfin_matches_json).get('matches')
            partner.ohmyfin_matches_html = words.matches_html(matches)

    # ── which contacts, and when ──────────────────────────────────────────────────────────

    def _ohmyfin_screenable(self):
        """Companies and persons; not delivery or invoice addresses (the order check screens the
        ship-to party), not our own companies, not our own users."""
        own = self.env['res.company'].sudo().search([]).partner_id
        return self.filtered(lambda p: (p.name or '').strip()
                             and (p.is_company or p.type in (False, 'contact'))
                             and p not in own
                             and not p.sudo().user_ids)

    def _ohmyfin_entity_type(self):
        """`organization`, `individual`, or None when Odoo cannot tell. On 18 and 19 "Is a Company"
        is the user's own choice; Odoo 20 computes it (a company is a contact with a VAT that is its
        own commercial entity), so there a contact that is not a company may still be one, and the
        screen is sent without a type rather than with a wrong one."""
        self.ensure_one()
        if self.is_company:
            return 'organization'
        return None if self._fields['is_company'].compute else 'individual'

    def _ohmyfin_key(self):
        self.ensure_one()
        raw = '|'.join([(self.name or '').strip().lower(), 'company' if self.is_company else 'person',
                        self.country_id.code or ''])
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _ohmyfin_is_stale(self):
        self.ensure_one()
        if self.ohmyfin_screen_state == 'not_screened' or not self.ohmyfin_screened_at:
            return True
        if self.ohmyfin_screen_key != self._ohmyfin_key():
            return True
        return self.ohmyfin_screened_at < fields.Datetime.now() - timedelta(days=STALE_DAYS)

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        if not self.env.context.get('ohmyfin_no_screen'):
            partners._ohmyfin_auto_screen()
        return partners

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('ohmyfin_no_screen') and any(f in vals for f in SCREEN_FIELDS):
            self._ohmyfin_auto_screen()
        return res

    def _ohmyfin_auto_screen(self):
        client = OhmyfinClient(self.env)
        if not client.is_active() or not client.auto_screen:
            return
        todo = self._ohmyfin_screenable().filtered(
            lambda p: p.ohmyfin_screen_key != p._ohmyfin_key() or p.ohmyfin_screen_state == 'not_screened')
        if not todo:
            return
        if len(todo) > INLINE_LIMIT or self.env.context.get('import_file'):
            todo.sudo().with_context(ohmyfin_no_screen=True).write({'ohmyfin_screen_pending': True})
            return
        todo._ohmyfin_screen(client)

    # ── the screen ────────────────────────────────────────────────────────────────────────

    def action_ohmyfin_screen(self):
        client = OhmyfinClient(self.env)
        if not client.is_active():
            raise UserError(_('Save an Ohmyfin API key and tick the consent box in Settings first.'))
        self._ohmyfin_screen(client)
        return True

    def _ohmyfin_screen(self, client=None):
        """Screen each contact once. Returns {partner id: state}. Never raises on a service fault:
        the contact reads "Not screened: <reason>" and the chatter says why."""
        client = client or OhmyfinClient(self.env)
        out = {}
        for partner in self:
            if not client.is_active():
                break
            vals = {'ohmyfin_screen_pending': False, 'ohmyfin_screen_key': partner._ohmyfin_key()}
            try:
                answer = client.screen_party(partner.name.strip(), partner._ohmyfin_entity_type(),
                                             partner.country_id.code or None)
            except OhmyfinUnavailable as exc:
                vals.update({'ohmyfin_screen_state': 'not_screened', 'ohmyfin_screen_reason': exc.reason})
                partner.sudo().with_context(ohmyfin_no_screen=True).write(vals)
                partner.message_post(body=words.contact_note(self.env, 'not_screened', reason=exc.reason))
                out[partner.id] = 'not_screened'
                continue

            matches = [m for m in (answer.get('matches') or []) if isinstance(m, dict)]
            state = words.party_state(answer.get('recommended_action'), answer.get('total_matches') or len(matches))
            lists = answer.get('lists_searched') or {}
            vals.update({
                'ohmyfin_screen_state': state,
                'ohmyfin_screen_reason': False,
                'ohmyfin_screened_at': fields.Datetime.now(),
                'ohmyfin_lists_count': int(lists.get('count') or 0) if isinstance(lists, dict) else 0,
                'ohmyfin_report_id': answer.get('report_id') or False,
                'ohmyfin_matches_json': json.dumps({'matches': [
                    {k: m.get(k) for k in ('name', 'list_source', 'list_name', 'match_score', 'action')}
                    for m in matches[:5]
                ]}),
            })
            partner.sudo().with_context(ohmyfin_no_screen=True).write(vals)
            partner.message_post(body=words.contact_note(
                self.env, state, vals['ohmyfin_report_id'], vals['ohmyfin_screened_at'],
                vals['ohmyfin_lists_count'], matches))
            out[partner.id] = state
        return out

    # ── the reviewer ──────────────────────────────────────────────────────────────────────

    def _ohmyfin_screen_ref(self):
        self.ensure_one()
        return '%s@%s' % (self.ohmyfin_report_id or '', self.ohmyfin_screened_at or '')

    def _ohmyfin_released(self):
        """A reviewer recorded a reason for THIS screen of a potential match."""
        self.ensure_one()
        return (self.ohmyfin_screen_state == 'potential_match' and bool(self.ohmyfin_released_for)
                and self.ohmyfin_released_for == self._ohmyfin_screen_ref())

    def action_ohmyfin_release(self):
        """A compliance reviewer records a reason on this contact's potential match, with the
        text typed in "Reason to release"."""
        self.ensure_one()
        return self._ohmyfin_release(take_reason(self))

    def _ohmyfin_release(self, reason):
        """Record a compliance reviewer's reason on a contact's potential match. Payments to it
        then go through until it is screened again."""
        self.ensure_one()
        if self.ohmyfin_screen_state != 'potential_match':
            raise UserError(_('Only a potential match can be released by a compliance reviewer.'))
        self.sudo().with_context(ohmyfin_no_screen=True).write({
            'ohmyfin_review_reason': reason,
            'ohmyfin_reviewed_by': self.env.user.id,
            'ohmyfin_reviewed_at': fields.Datetime.now(),
            'ohmyfin_released_for': self._ohmyfin_screen_ref(),
        })
        self.message_post(body=words.release_note(self.env.user.name, reason, self.ohmyfin_report_id))
        return True

    @api.model
    def _cron_ohmyfin_screen_pending(self):
        """Hourly: screen contacts an import left pending, a batch at a time, until the service
        says the day's allowance is used up."""
        client = OhmyfinClient(self.env)
        if not client.is_active():
            return
        pending = self.search([('ohmyfin_screen_pending', '=', True)], limit=CRON_BATCH)
        for partner in pending:
            result = partner._ohmyfin_screen(client)
            if result.get(partner.id) == 'not_screened':
                # The service did not answer, or the allowance is spent: try again next hour.
                partner.sudo().with_context(ohmyfin_no_screen=True).write({'ohmyfin_screen_pending': True})
                break
