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
    # VastPay also returns the short/expired forms; a cancelled or
    # expired invoice can no longer be paid, so it must NOT be treated as
    # a resumable 'pending' (otherwise Retry would re-show a dead QR).
    'cancel': 'cancelled',
    'expired': 'cancelled',
    'failed': 'cancelled',
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
    pos_order_uuid = fields.Char(
        string="POS Order UUID", index=True,
        help="Stable POS order identifier used to link the payment to the "
             "order. Unlike the order reference/name (which is '/' until the "
             "order is sequenced) the uuid is set as soon as the order is "
             "created in the browser.",
    )
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
        """Locate the tracking record from a (loosely parsed) webhook body.

        VastPay nests the invoice under ``payload``; ``data`` and a flat
        top-level shape are kept as fallbacks since the body is undocumented.
        """
        data = (
            notification_data.get('payload')
            or notification_data.get('data')
            or notification_data
        )
        if not isinstance(data, dict):
            data = notification_data
        invoice_id = (
            data.get('id')
            or data.get('invoice_id')
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
        previous_state = self.state
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
        # Push to the open POS screen ONLY on a terminal state change.
        # Notifying on 'pending' would feed back into the poll loop
        # (poll -> sync -> notify -> checkNow -> poll ...) and storm the
        # server. Terminal states are what the screen is waiting for.
        if new_state != previous_state and new_state in (
            'paid', 'cancelled', 'error',
        ):
            self._notify_pos(new_state)
        return new_state

    def _notify_pos(self, state):
        """Notify the originating POS that this payment changed state.

        Mirrors Odoo's pos_online_payment bus pattern: a name-only channel
        notification inviting the browser to re-check via a safe RPC. No
        sensitive data is sent.
        """
        self.ensure_one()
        config = self.pos_session_id.config_id
        if not config or not self.vastpay_invoice_id:
            return
        try:
            config._notify('VASTPAY_PAYMENT_NOTIFICATION', {
                'invoice_id': self.vastpay_invoice_id,
                'state': state,
            })
        except Exception:
            _logger.exception(
                "VastPay: failed to push POS notification for invoice %s",
                self.vastpay_invoice_id,
            )

    def _apply_to_pos_order(self):
        """Apply the configured close behaviour once the payment is paid.

        By default (both flags off) nothing is done to the POS order: the
        payment + amount is recorded and the cashier validates manually.
        """
        self.ensure_one()
        provider = self.provider_id
        order = self.pos_order_id
        if not order and self.pos_order_uuid:
            order = self.env['pos.order'].search(
                [('uuid', '=', self.pos_order_uuid)], limit=1,
            )
            if order:
                self.pos_order_id = order
        if not order and self.pos_reference and self.pos_reference != '/':
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
        if order.state == 'draft':
            order.action_pos_order_paid()
            _logger.info("VastPay: POS order %s validated (paid).", order.name)

    def _invoice_pos_order(self, order):
        if not order.account_move:
            order.action_pos_order_invoice()
            _logger.info("VastPay: POS order %s invoiced.", order.name)
