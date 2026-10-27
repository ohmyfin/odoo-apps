# Ohmyfin apps for Odoo

Odoo modules published on the Odoo Apps store by Ohmyfin. One branch per Odoo version:

| Branch | Odoo |
|---|---|
| `18.0` | Odoo 18 |
| `19.0` | Odoo 19 |
| `20.0` | Odoo 20 |

Each branch holds every module for that version as a folder at its root.

## Modules

### `ohmyfin_trade_check`: Sanctions & Export Check

Checks every sales order before it is confirmed: the customer and the delivery address against
sanctions lists, the delivery country against embargoes and country export restrictions, and
every order line against export controls by the product's HS code. Also screens contacts, guards
outgoing payments to a listed vendor, and looks up the BIC of vendor bank accounts.

- **Needs an Ohmyfin account and API key.** The checks are answered by Ohmyfin's API
  (`https://api.ohmyfin.ai`), an external service. Nothing is sent until an administrator saves
  the key and ticks the consent box in Settings. What is sent: contact names and countries, the
  delivery country, each product's HS code and country of origin.
- Community and Enterprise, on-premise and Odoo.sh. Not Odoo Online, which does not install
  third-party modules.
- A goods result is a flag to check the item's classification, never a licence determination.

## Install

From the Odoo Apps store, or from this repository: check out the branch for your Odoo version,
put the module folder in your addons path, update the apps list and install it. Then open
Settings, paste your Ohmyfin API key and tick the consent box.

## Licence

LGPL-3, see `LICENSE`.

## Changes and issues

This repository is published from Ohmyfin's own source tree, one commit per branch per release.
Issues are read; pull requests are not merged here.
