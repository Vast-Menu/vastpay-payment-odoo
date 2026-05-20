from odoo import api, fields, models


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    vastpay_payment_method = fields.Char(
        string="VastPay Payment Method",
        readonly=True,
        help="Raw payment method reported by VastPay for this payment (for "
             "example a value containing 'softpos…' or 'qr…'). Recorded "
             "when VastPay confirms the payment; kept for reference and "
             "reporting and never sent back to VastPay. Empty for non-VastPay "
             "payments and for VastPay payments confirmed before this field "
             "existed.",
    )
    vastpay_payment_type = fields.Selection(
        selection=[('qr', "QR"), ('softpos', "SoftPOS")],
        string="VastPay Type",
        compute='_compute_vastpay_payment_type',
        store=True,
        help="Normalized classification of the VastPay payment method: "
             "SoftPOS when the raw method contains 'softpos' (case "
             "insensitive), otherwise QR. Empty for non-VastPay payments.",
    )

    @api.depends('vastpay_payment_method')
    def _compute_vastpay_payment_type(self):
        for rec in self:
            value = (rec.vastpay_payment_method or '').lower()
            if not value:
                rec.vastpay_payment_type = False
            elif 'softpos' in value:
                rec.vastpay_payment_type = 'softpos'
            else:
                rec.vastpay_payment_type = 'qr'
