"""A compliance reviewer's written reason (PRD 0022 §4.1 items 3 and 4, AC 4).

Only a user in "Ohmyfin: compliance reviewer" may record one. The reason is typed into a field
that only that group can read or write (`groups=` on the field, so the ORM refuses anyone else),
and the Release button checks the group again on the server. No wizard model: Odoo 20 replaced
the access-rights file a wizard needs, and one module installs on 18, 19 and 20.
"""
from odoo.exceptions import AccessError, UserError

REVIEWER_GROUP = 'ohmyfin_trade_check.group_compliance_reviewer'
MIN_REASON = 10


def take_reason(record):
    """The typed reason, checked and cleared. Raises for a non-reviewer or a too-short reason."""
    record.ensure_one()
    if not record.env.user.has_group(REVIEWER_GROUP):
        raise AccessError('Only a user in "Ohmyfin: compliance reviewer" can record a reason.')
    reason = (record.ohmyfin_review_input or '').strip()
    if len(reason) < MIN_REASON:
        raise UserError('Write the reason in a sentence (at least %s characters) in "Reason to release".' % MIN_REASON)
    record.ohmyfin_review_input = False
    return reason
