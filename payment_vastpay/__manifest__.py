{
    'name': 'VastPay POS Payment',
    'version': '18.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Accept POS payments via VastPay (SAR, Saudi Arabia)',
    'description': """
VastPay POS Payment for Odoo 18
===============================

Adds VastPay as a Point of Sale payment method for Saudi Arabia (SAR).

How it works
------------

- Cashier selects VastPay on a POS order.
- Odoo creates a VastPay invoice for the configured QR stand (Table ID)
  and shows a QR code of the VastPay payment page on the payment screen.
- Customer scans the QR with their phone (VastPay PWA / App Clip) and pays.
- VastPay calls back via webhook; Odoo re-fetches the invoice for the
  authoritative status/amount and records the payment.
- The POS order is auto-validated (and optionally auto-invoiced) only when
  the corresponding opt-in flag is enabled; otherwise the cashier validates
  the order manually.

Supports Test / Live environments and a selectable PWA version
(pwa / pwa-v2). Currency and country are locked to SAR / Saudi Arabia.

Installation
------------

- This is the Odoo 18 release (18.0.x). Use the 19.0 branch for Odoo 19.
- Available on Odoo.sh and On Premise. Not available on Odoo Online (SaaS).
- Get the module from the Odoo Apps store (v18) or clone the 18.0 branch.
- Place the payment_vastpay folder in your Odoo addons path, or deploy the
  repository on Odoo.sh.
- Restart Odoo, enable Developer Mode, then Apps and Update Apps List.
- Search for VastPay POS Payment and click Install. It installs the Point
  of Sale and Payment apps automatically.
- A VastPay POS payment method is created on install and added to your
  Points of Sale. Finish setup in Payment Providers, VastPay.

For API documentation visit
https://documenter.getpostman.com/view/50697047/2sBXiesu8J
    """,
    'author': 'Vast Group',
    'maintainer': 'Vast Group',
    'support': 'support@vast-pay.com',
    'website': 'https://vast-pay.com',
    'license': 'LGPL-3',
    'depends': ['point_of_sale', 'payment'],
    'data': [
        'security/ir.model.access.csv',
        'views/payment_provider_views.xml',
        'views/pos_payment_method_views.xml',
        'views/pos_payment_views.xml',
        'views/pos_config_views.xml',
        'data/payment_provider_data.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'payment_vastpay/static/src/app/**/*',
        ],
    },
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
    'post_init_hook': '_post_init_hook',
    'uninstall_hook': '_uninstall_hook',
}
