import json
import logging
import pprint

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class VastPayController(http.Controller):

    _webhook_url = '/payment/vastpay/webhook'
    _return_url = '/payment/vastpay/return'

    @http.route(
        _webhook_url,
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
        save_session=False,
    )
    def vastpay_webhook(self, **kwargs):
        """Handle incoming VastPay webhook notifications.

        The webhook body is undocumented, so it is treated only as a trigger:
        we locate the tracking record then re-fetch the authoritative invoice
        from VastPay before doing anything. Always returns HTTP 200 so VastPay
        does not retry.
        """
        raw_body = request.httprequest.get_data(as_text=True)
        try:
            data = json.loads(raw_body) if raw_body else {}
        except (json.JSONDecodeError, ValueError):
            data = dict(kwargs)
        if not isinstance(data, dict):
            data = {}

        _logger.info("VastPay webhook received:\n%s", pprint.pformat(data))

        try:
            rec = (
                request.env['vastpay.pos.payment']
                .sudo()
                ._find_for_notification(data)
            )
            if not rec:
                _logger.warning(
                    "VastPay: no tracking record for webhook %s", data,
                )
                return request.make_json_response(
                    {'status': 'ignored'}, status=200,
                )
            state = rec._sync_from_vastpay()
            _logger.info(
                "VastPay: webhook processed for invoice %s -> %s",
                rec.vastpay_invoice_id, state,
            )
        except Exception:
            _logger.exception("VastPay: error processing webhook")
            return request.make_json_response({'status': 'error'}, status=200)

        return request.make_json_response({'status': 'ok'}, status=200)

    @http.route(
        _return_url,
        type='http',
        auth='public',
        methods=['GET', 'POST'],
        csrf=False,
        save_session=False,
    )
    def vastpay_return(self, **kwargs):
        """Landing page after the customer pays on the VastPay PWA.

        The POS screen itself is driven by status polling, so this page just
        tells the customer they can return to the cashier.
        """
        _logger.info("VastPay return: %s", pprint.pformat(kwargs))
        return request.make_response(
            "<html><body style='font-family:sans-serif;text-align:center;"
            "margin-top:3rem'><h2>Payment received</h2>"
            "<p>You can return to the cashier.</p></body></html>",
            headers=[('Content-Type', 'text/html')],
        )
