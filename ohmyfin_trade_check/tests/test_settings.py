"""Settings (§4.1 item 1) through the real Settings form: the key, the consent box and the two
switches round-trip on every version (Odoo 20 stores system parameters through typed setters),
and the client sends only with both a key and the box ticked."""
from lxml import etree

from odoo.tests import tagged

from ..models.ohmyfin_client import OhmyfinClient, get_param
from .common import KEY, OhmyfinCase


@tagged('post_install', '-at_install')
class TestSettings(OhmyfinCase):

    def _save(self, **vals):
        self.env['res.config.settings'].create(vals).execute()
        return OhmyfinClient(self.env)

    def test_key_and_consent_are_both_needed(self):
        self.activate(key=False, consent=False)
        self.assertFalse(self._save(ohmyfin_api_key=KEY, ohmyfin_consent=False).is_active())
        self.assertFalse(self._save(ohmyfin_api_key=False, ohmyfin_consent=True).is_active())
        client = self._save(ohmyfin_api_key=KEY, ohmyfin_consent=True)
        self.assertTrue(client.is_active())
        self.assertEqual(get_param(self.env, 'ohmyfin_trade_check.api_key'), KEY)

    def test_switches_round_trip(self):
        client = self._save(ohmyfin_api_key=KEY, ohmyfin_consent=True, ohmyfin_auto_screen=False,
                            ohmyfin_refuse_when_unavailable=True)
        self.assertFalse(client.auto_screen)
        self.assertTrue(client.refuse_when_unavailable)
        values = self.env['res.config.settings'].create({})
        self.assertFalse(values.ohmyfin_auto_screen)
        self.assertTrue(values.ohmyfin_refuse_when_unavailable)
        client = self._save(ohmyfin_api_key=KEY, ohmyfin_consent=True, ohmyfin_auto_screen=True,
                            ohmyfin_refuse_when_unavailable=False)
        self.assertTrue(client.auto_screen)
        self.assertFalse(client.refuse_when_unavailable)

    def test_auto_screen_defaults_on(self):
        from ..models.ohmyfin_client import set_param
        set_param(self.env, 'ohmyfin_trade_check.auto_screen', False)
        self.assertTrue(OhmyfinClient(self.env).auto_screen)
        self.assertTrue(self.env['res.config.settings'].create({}).ohmyfin_auto_screen)

    def test_settings_section_is_rendered(self):
        """The key and the consent box can only be entered here. Odoo's settings compiler drops an
        <app notApp="1"> entirely (it renders no section and no sidebar entry), which on a store
        install would leave no screen to enter the key: found in the founder demo, 27/10/2026."""
        arch = self.env['res.config.settings'].get_views([(False, 'form')])['views']['form']['arch']
        apps = etree.fromstring(arch).xpath('//app[@name="ohmyfin_trade_check"]')
        self.assertEqual(len(apps), 1)
        self.assertNotEqual(apps[0].get('notApp'), '1')
        self.assertTrue(apps[0].xpath('.//field[@name="ohmyfin_api_key"]'))
        self.assertTrue(apps[0].xpath('.//field[@name="ohmyfin_consent"]'))
