import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# VastPay invoice status -> internal state
_STATUS_MAP = {
    'paid': 'paid',
    'unpaid': 'pending',
    'pending': 'pending',
    'cancelled': 'cancelled',
    'canceled': 'cancelled',
}


class VastPayPosPayment(models.Model):
    _name = 'vastpay.pos.payment'
    _description = "VastPay POS Payment Tracking"
    _order = 'create_date desc'

    provider_id = fields.Many2one(
        'payment.provider', string="VastPay Provider", required=True,
        ondelete='cascade',
    )
    pos_session_id = fields.Many2one('pos.session', string="POS Session")
    pos_order_id = fields.Many2one('pos.order', string="POS Order")
    pos_reference = fields.Char(string="POS Order Reference", index=True)
    vastpay_invoice_id = fields.Char(
        string="VastPay Invoice ID", index=True,
    )
    table_id = fields.Char(string="VastPay Table ID", index=True)
    amount = fields.Float(string="Amount")
    currency_id = fields.Many2one('res.currency', string="Currency")
    state = fields.Selection(
        [
            ('draft', "Draft"),
            ('pending', "Pending"),
            ('paid', "Paid"),
            ('cancelled', "Cancelled"),
            ('error', "Error"),
        ],
        default='draft', required=True, index=True,
    )
    raw_status = fields.Char(string="VastPay Raw Status")

    @api.model
    def _find_for_notification(self, notification_data):
        """Locate the tracking record from a (loosely parsed) webhook body."""
        data = notification_data.get('data') or notification_data
        invoice_id = (
            data.get('id')
            or notification_data.get('invoice_id')
            or notification_data.get('id')
        )
        rec = self.browse()
        if invoice_id:
            rec = self.search(
                [('vastpay_invoice_id', '=', str(invoice_id))], limit=1,
            )
        if not rec:
            # table_id identifies a stand, not a single payment, so prefer
            # the active (not yet finalised) payment at that stand.
            table_id = data.get('table_id') or notification_data.get('table_id')
            if table_id:
                rec = self.search(
                    [('table_id', '=', str(table_id)),
                     ('state', 'in', ('draft', 'pending'))],
                    limit=1,
                ) or self.search(
                    [('table_id', '=', str(table_id))], limit=1,
                )
        return rec

    def _sync_from_vastpay(self):
        """Re-fetch the authoritative invoice from VastPay and update self.

        Returns the internal state after syncing.
        """
        self.ensure_one()
        invoice = self.provider_id._vastpay_get_invoice(self.vastpay_invoice_id)
        data = invoice.get('data', invoice) or {}
        raw_status = (data.get('status') or '').lower()
        new_state = _STATUS_MAP.get(raw_status, 'pending')

        captured = 0.0
        for hist in data.get('payment_histories') or []:
            if (hist.get('status') or '').upper() == 'CAPTURED':
                try:
                    captured += float(hist.get('amount') or 0.0)
                except (TypeError, ValueError):
                    pass
        vals = {'raw_status': raw_status, 'state': new_state}
        if captured:
            vals['amount'] = captured
        elif data.get('total') is not None:
            try:
                vals['amount'] = float(data['total'])
            except (TypeError, ValueError):
                pass
        self.write(vals)

        if new_state == 'paid':
            self._apply_to_pos_order()
        return new_state

    def _apply_to_pos_order(self):
        """Apply the configured close behaviour once the payment is paid.

        By default (both flags off) nothing is done to the POS order: the
        payment + amount is recorded and the cashier validates manually.
        """
        self.ensure_one()
        provider = self.provider_id
        order = self.pos_order_id
        if not order and self.pos_reference:
            order = self.env['pos.order'].search(
                [('pos_reference', '=', self.pos_reference)], limit=1,
            )
            if order:
                self.pos_order_id = order

        if not order:
            _logger.info(
                "VastPay: payment %s recorded (paid) but no POS order yet; "
                "cashier will validate from POS.", self.vastpay_invoice_id,
            )
            return

        if provider.vastpay_auto_validate_order:
            self._validate_pos_order(order)
            if provider.vastpay_auto_invoice:
                self._invoice_pos_order(order)
        else:
            _logger.info(
                "VastPay: payment %s paid; auto-validate disabled, leaving "
                "POS order %s open.", self.vastpay_invoice_id, order.name,
            )

    def _validate_pos_order(self, order):
        """Mark the POS order as paid/validated server-side.

        Method name confirmed against the installed point_of_sale source.
        """
        if order.state in ('draft', 'invoiced'):
            order.action_pos_order_paid()
            _logger.info("VastPay: POS order %s validated (paid).", order.name)

    def _invoice_pos_order(self, order):
        if not order.account_move:
            order.action_pos_order_invoice()
            _logger.info("VastPay: POS order %s invoiced.", order.name)
