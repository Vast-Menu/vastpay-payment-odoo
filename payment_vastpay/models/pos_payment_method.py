import base64
import io
import logging

import qrcode

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.osv import expression
from odoo.tools import file_open

_logger = logging.getLogger(__name__)


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    def _get_payment_terminal_selection(self):
        return super()._get_payment_terminal_selection() + [('vastpay', 'VastPay')]

    vastpay_provider_id = fields.Many2one(
        'payment.provider',
        string="VastPay Provider",
        domain=[('code', '=', 'vastpay')],
        help="VastPay payment provider holding the API credentials.",
    )
    vastpay_auto_validate_order = fields.Boolean(
        related='vastpay_provider_id.vastpay_auto_validate_order',
        readonly=True,
    )

    @api.model
    def _load_pos_data_domain(self, data):
        # Don't load the VastPay method into the POS unless it can actually
        # take payments: the linked provider must exist, not be Disabled,
        # and have both API credentials set. Otherwise the cashier would
        # see a VastPay option that fails on use.
        domain = super()._load_pos_data_domain(data)
        vastpay_methods = self.sudo().search(
            [('use_payment_terminal', '=', 'vastpay')]
        )
        if not vastpay_methods:
            return domain
        fallback = self.env['payment.provider'].sudo().search(
            [('code', '=', 'vastpay')], limit=1,
        )
        hidden_ids = []
        for m in vastpay_methods:
            provider = (m.vastpay_provider_id or fallback).sudo()
            if (not provider
                    or provider.state == 'disabled'
                    or not provider.vastpay_client_id
                    or not provider.vastpay_client_secret):
                hidden_ids.append(m.id)
        if hidden_ids:
            domain = expression.AND([[('id', 'not in', hidden_ids)], domain])
        return domain

    @api.model
    def _load_pos_data_fields(self, config_id):
        # Expose the non-secret auto-validate flag (so the payment interface
        # can honour it) and the image (so the POS shows the brand logo).
        params = super()._load_pos_data_fields(config_id)
        params += ['vastpay_auto_validate_order']
        if 'image' not in params:
            params += ['image']
        return params

    @staticmethod
    def _vastpay_brand_image():
        """Module-shipped VastPay logo, used as the default method image."""
        try:
            with file_open(
                'payment_vastpay/static/src/img/vastpay_icon.png', 'rb'
            ) as fh:
                return base64.b64encode(fh.read())
        except OSError:
            return False

    @api.onchange('use_payment_terminal')
    def _onchange_vastpay_brand_image(self):
        if self.use_payment_terminal == 'vastpay' and not self.image:
            self.image = self._vastpay_brand_image()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.use_payment_terminal == 'vastpay' and not rec.image:
                rec.image = self._vastpay_brand_image()
        return records

    # --- Helpers ---

    def _vastpay_get_provider(self):
        self.ensure_one()
        provider = self.vastpay_provider_id
        if not provider:
            provider = self.env['payment.provider'].sudo().search(
                [('code', '=', 'vastpay')], limit=1,
            )
        if not provider:
            raise UserError(_("No VastPay payment provider configured."))
        return provider.sudo()

    def _vastpay_check_access(self):
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_("Access denied for VastPay POS payment."))

    @staticmethod
    def _vastpay_qr_data_url(text):
        img = qrcode.make(text)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()

    # --- POS frontend RPC endpoints ---

    def vastpay_make_payment(self, data):
        """Create a VastPay invoice for a POS payment line.

        :param dict data: ``amount``, ``pos_reference``, ``session_id``
        :return: ``{invoice_id, table_id, payment_url, qr_image}`` or ``{error}``
        """
        self.ensure_one()
        self._vastpay_check_access()
        provider = self._vastpay_get_provider()

        amount = data.get('amount') or 0.0
        if amount <= 0:
            return {'error': _("Cannot process a non-positive amount.")}

        session = self.env['pos.session'].browse(
            data.get('session_id')
        ).exists()
        currency = (
            session.currency_id
            or self.env.company.currency_id
        )
        if currency.name != 'SAR':
            return {'error': _(
                "VastPay only supports SAR. This Point of Sale uses %s.",
                currency.name,
            )}

        # table_id is the VastPay-provided identifier of the physical QR
        # stand. It is configured per Point of Sale, not generated.
        table_id = session.config_id.vastpay_table_id
        if not table_id:
            return {'error': _(
                "No VastPay Stand (Table ID) configured on this Point of "
                "Sale. Set it in the POS configuration."
            )}
        table_id = str(table_id).strip()

        tracking = self.env['vastpay.pos.payment'].sudo().create({
            'provider_id': provider.id,
            'pos_session_id': session.id or False,
            'pos_reference': data.get('pos_reference'),
            'vastpay_invoice_id': '',
            'table_id': table_id,
            'amount': amount,
            'currency_id': currency.id,
            'state': 'draft',
        })

        try:
            # The tracking record id is a guaranteed-unique integer, which
            # VastPay requires for order_number / invoice_number.
            response = provider._vastpay_create_invoice(
                table_id=table_id,
                total=amount,
                order_number=tracking.id,
                invoice_number=tracking.id,
            )
        except Exception as e:  # noqa: BLE001 - surface as POS error
            tracking.write({'state': 'error', 'raw_status': str(e)[:200]})
            _logger.exception("VastPay: create invoice failed")
            return {'error': _("VastPay error: %s", str(e))}

        invoice_data = response.get('data', response) or {}
        invoice_id = str(invoice_data.get('id') or '')
        table_id = str(invoice_data.get('table_id') or table_id)
        if not invoice_id:
            tracking.write({'state': 'error', 'raw_status': 'no invoice id'})
            return {'error': _("VastPay did not return an invoice id.")}

        tracking.write({
            'vastpay_invoice_id': invoice_id,
            'table_id': table_id,
            'state': 'pending',
            'raw_status': invoice_data.get('status') or 'unpaid',
        })

        payment_url = '%s/?table_id=%s' % (
            provider._vastpay_get_payment_url(), table_id,
        )
        return {
            'invoice_id': invoice_id,
            'table_id': table_id,
            'payment_url': payment_url,
            'qr_image': self._vastpay_qr_data_url(payment_url),
            'auto_validate': provider.vastpay_auto_validate_order,
        }

    def vastpay_poll_status(self, data):
        """Return the latest known status of a VastPay payment.

        Cheap: reads the tracking record (kept fresh by the webhook). Falls
        back to a direct re-fetch if still pending after a grace period.
        """
        self.ensure_one()
        self._vastpay_check_access()
        invoice_id = str(data.get('invoice_id') or '')
        tracking = self.env['vastpay.pos.payment'].sudo().search(
            [('vastpay_invoice_id', '=', invoice_id)], limit=1,
        )
        if not tracking:
            return {'state': 'error', 'error': _("Unknown payment.")}

        state = tracking.state
        if state in ('draft', 'pending'):
            # Safety net in case the webhook never arrives.
            try:
                state = tracking._sync_from_vastpay()
            except Exception:  # noqa: BLE001
                _logger.exception("VastPay: poll re-fetch failed")
        # Return the live auto-validate flag so the POS honours the current
        # provider setting, not the value cached when the POS session opened.
        return {
            'state': state,
            'amount': tracking.amount,
            'auto_validate': tracking.provider_id.vastpay_auto_validate_order,
        }

    def vastpay_cancel_payment(self, data):
        """Cancel a pending VastPay invoice."""
        self.ensure_one()
        self._vastpay_check_access()
        invoice_id = str(data.get('invoice_id') or '')
        tracking = self.env['vastpay.pos.payment'].sudo().search(
            [('vastpay_invoice_id', '=', invoice_id)], limit=1,
        )
        if not tracking:
            return {'cancelled': True}
        try:
            tracking.provider_id._vastpay_cancel_invoice(invoice_id)
        except Exception:  # noqa: BLE001
            _logger.exception("VastPay: cancel failed")
        tracking.write({'state': 'cancelled'})
        return {'cancelled': True}
