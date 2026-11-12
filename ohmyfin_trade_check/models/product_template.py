"""The service type of a product (changes/0051): what a line with no HS code is screened as.

A product with an HS code is goods and is sent by its code, exactly as before. A product with no
HS code and a service type is sent as that service (``service_type`` on the order line), never
both. The field lives on product.template, so every variant carries its template's type.

The HS code is not on the same model everywhere: product.template on 18.0 and 19.0
(``stock_delivery``), product.product on 20.0, and absent without ``stock_delivery``. The form
hides the service type when the product has a code through ``ohmyfin_has_hs_code``, whose
dependencies are worked out from the fields that exist on this install.
"""
from odoo import api, fields, models

from .ohmyfin_words import SERVICE_TYPES


def _hs_depends(model):
    if 'hs_code' in model._fields:
        return ['hs_code']
    if 'hs_code' in model.env['product.product']._fields:
        return ['product_variant_ids.hs_code']
    return []


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    ohmyfin_service_type = fields.Selection(
        SERVICE_TYPES, string='Service type',
        help='For a product with no HS code: the kind of service it is. The order check sends it '
             'in place of an HS code, so the line is screened as that service.')
    ohmyfin_has_hs_code = fields.Boolean(compute='_compute_ohmyfin_has_hs_code')

    @api.depends(_hs_depends)
    def _compute_ohmyfin_has_hs_code(self):
        on_template = 'hs_code' in self._fields
        on_variant = 'hs_code' in self.env['product.product']._fields
        for template in self:
            if on_template:
                template.ohmyfin_has_hs_code = bool((template.hs_code or '').strip())
            elif on_variant:
                template.ohmyfin_has_hs_code = any((v.hs_code or '').strip() for v in template.product_variant_ids)
            else:
                template.ohmyfin_has_hs_code = False
