from odoo import fields, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    vastpay_table_id = fields.Char(
        string="VastPay Stand (Table ID)",
        help="The VastPay-provided Table ID identifying the physical QR "
             "stand for this Point of Sale. Provided by VastPay and mapped "
             "to a branch/stand. Required to take VastPay payments here.",
    )
