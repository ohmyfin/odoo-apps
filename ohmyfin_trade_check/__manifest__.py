{
    'name': 'Sanctions & Export Check',
    # The source carries x.y.z, which every Odoo series reads as <series>.x.y.z.
    # integrations/odoo/publish.sh writes the full 18.0.1.0.0 / 19.0.1.0.0 /
    # 20.0.1.0.0 into each store branch, as the store wants it spelled out.
    'version': '20.0.1.0.0',
    'summary': 'Trade compliance on the sales order: sanctions and OFAC denied party screening of '
               'the customer, embargo and Russia (Art. 12g) checks of the destination, export '
               'control and dual-use goods check by HS code. Needs an Ohmyfin account.',
    'description': 'Needs an Ohmyfin account and API key: the checks are answered by the Ohmyfin '
                   'API, an external service. Nothing is sent until an administrator saves the key '
                   'and ticks the consent box. Sent: contact names and countries, the delivery '
                   'country, each product HS code and country of origin.',
    'category': 'Sales/Sales',
    'license': 'LGPL-3',
    'author': 'Ohmyfin',
    'depends': ['sale', 'account', 'mail'],
    'data': [
        'security/ohmyfin_security.xml',
        'data/ir_cron.xml',
        'views/res_config_settings_views.xml',
        'views/res_partner_views.xml',
        'views/res_partner_bank_views.xml',
        'views/sale_order_views.xml',
        'views/account_payment_views.xml',
    ],
    'images': ['static/description/banner.png', 'static/description/order_check_screenshot.png'],
    'installable': True,
    'application': False,
}
