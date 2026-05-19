import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = 'pos.order'

    @api.model
    def _process_order(self, order, existing_order):
        """Apply an already-confirmed VastPay payment to a late-synced order.

        The webhook (and the status poll) confirm the payment on the
        ``vastpay.pos.payment`` tracking record, but that often happens
        *before* the cashier's in-browser order is synced to the server,
        so ``_apply_to_pos_order`` had no order to act on at the time.

        When the order finally syncs we re-run the **same**
        ``_apply_to_pos_order`` used by the webhook and the poll, so
        auto-validate / auto-invoice are honoured identically regardless
        of whether the webhook or the order-sync happened first, and even
        when no cashier is polling. The shared method is idempotent
        (validate only acts on draft orders, invoice only when none yet),
        so this is safe if the frontend already finalised the order.
        """
        order_id = super()._process_order(order, existing_order)
        try:
            pos_order = self.browse(order_id)
            domain = []
            if pos_order.uuid:
                domain.append(('pos_order_uuid', '=', pos_order.uuid))
            if pos_order.pos_reference and pos_order.pos_reference != '/':
                domain.append(('pos_reference', '=', pos_order.pos_reference))
            if not domain:
                return order_id
            domain = ['|'] * (len(domain) - 1) + domain
            tracking = self.env['vastpay.pos.payment'].sudo().search([
                *domain,
                ('state', '=', 'paid'),
            ])
            for rec in tracking:
                if not rec.pos_order_id:
                    rec.pos_order_id = pos_order.id
                rec._apply_to_pos_order()
        except Exception:
            _logger.exception(
                "VastPay: failed to apply confirmed payment to synced "
                "POS order %s", order_id,
            )
        return order_id
