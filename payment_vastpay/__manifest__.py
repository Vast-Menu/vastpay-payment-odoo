{
    'name': 'VastPay POS Payment',
    'version': '19.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Accept POS payments via VastPay QR / PWA (SAR, Saudi Arabia)',
    'description': """
VastPay POS Payment for Odoo 19
===============================

Adds VastPay as a Point of Sale payment method for Saudi Arabia (SAR).

Flow:
- Cashier selects VastPay on a POS order.
- Odoo creates a VastPay invoice for the configured QR stand (Table ID) and
  shows a QR code of the VastPay payment page on the payment screen.
- Customer scans the QR with their phone (VastPay PWA / App Clip) and pays.
- VastPay calls back via webhook; Odoo re-fetches the invoice for the
  authoritative status/amount and records the payment.
- The POS order is auto-validated (and optionally auto-invoiced) only when the
  corresponding opt-in flag is enabled; otherwise the cashier validates the
  order manually.

Supports Test/Live environments and a selectable PWA version (pwa / pwa-v2).
Currency and country are locked to SAR / Saudi Arabia.

For API documentation visit: https://documenter.getpostman.com/view/50697047/2sBXiesu8J
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
