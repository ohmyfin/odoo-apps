"""Module-owned state is written by the module only (changes/0053, version 1.1.1).

Every hold this module places is decided from fields it stores itself: the order's check state
and fingerprint, the payment's guard state, the contact's screen state, its screen key and a
reviewer's release. ``readonly=True`` on those fields is a VIEW setting, not an ORM rule: over
JSON-RPC any user who may edit the record could write them, so a salesperson could write
"released" on a held order, or "no_match" on a listed payee, and skip the hold with no reviewer
and no chatter line.

So ``create()`` and ``write()`` of the three models keep these fields for the superuser
environment (``env.su``), which the module's own code reaches through ``sudo()`` and a user's
JSON-RPC call never does:

- ``write()`` from any other environment DROPS them and logs one warning (model, user id, field
  names and record ids, never a value). Dropped rather than refused: the fields were never
  editable in any view, so no user sends them on purpose, and an import or a contact merge that
  happens to carry them goes on without them instead of failing.
- ``create()`` from any other environment SETS each one to the value a new record has. Set rather
  than dropped: a field missing from the values would be filled from the context
  (``default_<field>``) or from a personal default (``ir.default``), and the caller controls both.
  It warns only for a value that differs from a new record's: creating a user hands the partner
  every default of its inherited fields (``_inherits``), ``ohmyfin_screen_state`` included.

The module's own writes go through ``sudo()`` and its chatter lines are posted on the caller's
record, so the author of each line is still the user who acted.
"""
import logging

_logger = logging.getLogger(__name__)


def _warn(model, uid, names, ids):
    _logger.warning('ohmyfin_trade_check: %s values left out for user %s, written by the module only: %s (ids %s)',
                    model, uid, ', '.join(sorted(names)), ids)


def for_create(model, vals_list, protected):
    """``vals_list`` with every protected field at its initial value, unless the environment is
    the superuser's. ``protected`` maps each field to the value a new record has."""
    if model.env.su:
        return vals_list
    sent = {name for vals in vals_list for name, value in vals.items()
            if name in protected and (value or False) != (protected[name] or False)}
    if sent:
        _warn(model._name, model.env.uid, sent, 'new')
    return [dict(vals, **protected) for vals in vals_list]


def for_write(records, vals, protected):
    """``vals`` without the protected fields, unless the environment is the superuser's."""
    if records.env.su:
        return vals
    sent = [name for name in vals if name in protected]
    if not sent:
        return vals
    _warn(records._name, records.env.uid, sent, records.ids[:20])
    return {name: value for name, value in vals.items() if name not in protected}
