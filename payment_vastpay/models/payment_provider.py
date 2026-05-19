import logging
import requests

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

VASTPAY_BASE_URLS = {
    'enabled': 'https://api.vast-pay.com/api/v2',
    'test': 'https://api-staging.vast-pay.com/api/v2',
}

# PWA host prefix per selected PWA version. The environment (Test vs Live)
# adds the "-staging" suffix the same way the API URLs do.
VASTPAY_PWA_PREFIXES = {
    'v1': 'pwa',
    'v2': 'pwa-v2',
}

TIMEOUT = 30


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('vastpay', 'VastPay')],
        ondelete={'vastpay': 'set default'},
    )
    # Credentials are intentionally NOT required_if_provider: the provider
    # ships in Test Mode without credentials. VastPay is simply hidden from
    # the POS until both credentials are set (see pos.payment.method
    # _load_pos_data_domain), instead of blocking the provider record.
    vastpay_client_id = fields.Char(
        string="Client ID",
        groups='base.group_system',
    )
    vastpay_client_secret = fields.Char(
        string="Client Secret",
        groups='base.group_system',
    )
    vastpay_access_token = fields.Char(
        string="Access Token",
        groups='base.group_system',
    )
    vastpay_webhook_registered = fields.Boolean(
        string="Webhook Registered",
        default=False,
    )
    vastpay_auto_validate_order = fields.Boolean(
        string="Auto-validate POS order on payment",
        default=False,
        help="When enabled, a paid VastPay webhook automatically adds the payment "
             "line and validates the POS order. When disabled, the payment is "
             "recorded but the cashier must validate the order manually.",
    )
    vastpay_auto_invoice = fields.Boolean(
        string="Auto-invoice paid POS order",
        default=False,
        help="When enabled (and the order is validated), the customer invoice for "
             "the POS order is created and posted automatically on payment.",
    )
    vastpay_pwa_version = fields.Selection(
        selection=[('v1', "Default (pwa)"), ('v2', "v2 / preview (pwa-v2)")],
        string="PWA Version",
        default='v1',
        required=True,
        help="Which VastPay hosted payment page (PWA) the QR code points to. "
             "'Default' uses pwa(-staging).vast-pay.com; 'v2 / preview' uses "
             "pwa-v2(-staging).vast-pay.com. The Test/Live environment adds the "
             "'-staging' suffix automatically.",
    )

    # --- Helpers ---

    def _vastpay_get_base_url(self):
        """Return the VastPay API base URL for the current environment."""
        self.ensure_one()
        return VASTPAY_BASE_URLS.get(self.state, VASTPAY_BASE_URLS['test'])

    def _vastpay_get_payment_url(self):
        """Return the VastPay PWA base URL for the current environment.

        Combines the selected PWA version (pwa / pwa-v2) with the
        environment: Live (state 'enabled') uses production, anything else
        (Test / Disabled) uses the '-staging' host.
        """
        self.ensure_one()
        prefix = VASTPAY_PWA_PREFIXES.get(self.vastpay_pwa_version, 'pwa')
        suffix = '' if self.state == 'enabled' else '-staging'
        return f'https://{prefix}{suffix}.vast-pay.com'

    def _vastpay_get_headers(self):
        """Return common headers for VastPay API calls."""
        self.ensure_one()
        return {
            'Accept': 'application/json',
            'Authorization': self.vastpay_access_token or '',
        }

    # --- Authentication ---

    def _vastpay_authenticate(self):
        """Perform the two-step OAuth flow to obtain an access token.

        Step 1: POST /auth/get-code with client_id + client_secret → { code }
        Step 2: POST /auth/get-access-token with client_id + client_secret + code → { access_token }
        """
        self.ensure_one()
        base_url = self._vastpay_get_base_url()

        # Step 1: Get authorization code
        try:
            resp = requests.post(
                f'{base_url}/auth/get-code',
                headers={'Accept': 'application/json'},
                data={
                    'client_id': self.vastpay_client_id,
                    'client_secret': self.vastpay_client_secret,
                },
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            code = resp.json().get('code')
            if not code:
                raise ValidationError(_(
                    "VastPay authentication failed: no authorization code returned."
                ))
        except requests.RequestException as e:
            _logger.error("VastPay get-code failed: %s", e)
            raise ValidationError(_(
                "Could not connect to VastPay to get authorization code. "
                "Please verify your Client ID and Client Secret."
            )) from e

        # Step 2: Exchange code for access token
        try:
            resp = requests.post(
                f'{base_url}/auth/get-access-token',
                headers={'Accept': 'application/json'},
                data={
                    'client_id': self.vastpay_client_id,
                    'client_secret': self.vastpay_client_secret,
                    'code': code,
                },
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            token = resp.json().get('access_token')
            if not token:
                raise ValidationError(_(
                    "VastPay authentication failed: no access token returned."
                ))
        except requests.RequestException as e:
            _logger.error("VastPay get-access-token failed: %s", e)
            raise ValidationError(_(
                "Could not exchange authorization code for access token."
            )) from e

        self.vastpay_access_token = token
        _logger.info("VastPay: access token obtained successfully for provider %s", self.id)
        return token

    def _vastpay_ensure_token(self):
        """Ensure we have a valid access token, refreshing if needed."""
        self.ensure_one()
        if not self.vastpay_access_token:
            self._vastpay_authenticate()
        return self.vastpay_access_token

    # --- API calls ---

    def _vastpay_make_request(self, method, endpoint, **kwargs):
        """Make an authenticated request to VastPay API.

        Handles token refresh on 401 (one retry).
        """
        self.ensure_one()
        self._vastpay_ensure_token()

        base_url = self._vastpay_get_base_url()
        url = f'{base_url}{endpoint}'
        headers = self._vastpay_get_headers()

        try:
            resp = requests.request(
                method, url, headers=headers, timeout=TIMEOUT, **kwargs
            )

            # If 401, re-authenticate once and retry
            if resp.status_code == 401:
                _logger.info("VastPay: token expired, re-authenticating...")
                self._vastpay_authenticate()
                headers = self._vastpay_get_headers()
                resp = requests.request(
                    method, url, headers=headers, timeout=TIMEOUT, **kwargs
                )

            resp.raise_for_status()
            return resp.json()

        except requests.RequestException as e:
            _logger.error("VastPay API request failed: %s %s - %s", method, endpoint, e)
            raise ValidationError(_(
                "VastPay API request failed: %(error)s", error=str(e)
            )) from e

    def _vastpay_create_invoice(self, table_id, total, order_number, invoice_number,
                                is_recurring=None, recurring_period=None):
        """Create an invoice on VastPay.

        :param str table_id: UUID of the table associated with the invoice
        :param float total: Total amount
        :param int order_number: Order number (required, integer)
        :param int invoice_number: Invoice number (required, integer)
        :param int is_recurring: Optional, 1 for recurring / 0 for one-time
        :param str recurring_period: Optional, hourly/daily/weekly/monthly
        :return: dict with invoice data including 'id', 'table_id', 'status'
        """
        self.ensure_one()
        payload = {
            'table_id': table_id,
            'total': total,
            'order_number': int(order_number),
            'invoice_number': int(invoice_number),
        }
        if is_recurring is not None:
            payload['is_recurring'] = int(is_recurring)
        if recurring_period:
            payload['recurring_period'] = recurring_period

        return self._vastpay_make_request('POST', '/invoices', data=payload)

    def _vastpay_get_invoice(self, invoice_id):
        """Get invoice details from VastPay."""
        self.ensure_one()
        return self._vastpay_make_request('GET', f'/invoices/{invoice_id}')

    def _vastpay_cancel_invoice(self, order_id):
        """Cancel an invoice on VastPay."""
        self.ensure_one()
        return self._vastpay_make_request('PATCH', f'/invoices/cancel/{order_id}')

    @staticmethod
    def _vastpay_response_enabled(result, default):
        """Read the ``enabled`` flag VastPay returns for the webhook update.

        The flag may be top-level or nested under ``data`` and may come back
        as a real bool, an int, or a string ("true"/"1"). Falls back to
        ``default`` when the API does not return the flag at all.
        """
        enabled = None
        if isinstance(result, dict):
            if 'enabled' in result:
                enabled = result.get('enabled')
            elif isinstance(result.get('data'), dict) and 'enabled' in result['data']:
                enabled = result['data'].get('enabled')
        if enabled is None:
            return default
        if isinstance(enabled, str):
            return enabled.strip().lower() in ('1', 'true', 'yes', 'enabled')
        return bool(enabled)

    def _vastpay_register_webhook(self, webhook_url):
        """Register, update or disable the webhook URL on VastPay.

        Passing an empty ``webhook_url`` disables the webhook on VastPay.
        The ``vastpay_webhook_registered`` flag follows the ``enabled``
        status returned by VastPay (falling back to whether a URL was sent).
        """
        self.ensure_one()
        result = self._vastpay_make_request(
            'PATCH',
            '/integrations/webhook/update',
            params={'webhook_url': webhook_url},
        )
        enabled = self._vastpay_response_enabled(result, default=bool(webhook_url))
        self.vastpay_webhook_registered = enabled
        if enabled:
            _logger.info("VastPay: webhook registered at %s", webhook_url)
        else:
            _logger.info("VastPay: webhook disabled")
        return result

    # --- Provider actions ---

    def action_vastpay_test_connection(self):
        """Test the VastPay connection by authenticating."""
        self.ensure_one()
        self._vastpay_authenticate()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Connection Successful"),
                'message': _("Successfully connected to VastPay."),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_vastpay_register_webhook(self):
        """Register the webhook URL with VastPay."""
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        webhook_url = f'{base_url}/payment/vastpay/webhook'
        self._vastpay_register_webhook(webhook_url)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Webhook Registered"),
                'message': _("VastPay will send payment notifications to: %s") % webhook_url,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_vastpay_disable_webhook(self):
        """Disable the webhook on VastPay by sending an empty URL."""
        self.ensure_one()
        self._vastpay_register_webhook('')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Webhook Disabled"),
                'message': _("VastPay will no longer send payment notifications."),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    # --- Overrides ---

    def _get_supported_currencies(self):
        """Override to return VastPay supported currencies."""
        supported_currencies = super()._get_supported_currencies()
        if self.code == 'vastpay':
            supported_currencies = supported_currencies.filtered(
                lambda c: c.name == 'SAR'
            )
        return supported_currencies

    def _get_default_payment_method_codes(self):
        """Override to return default payment method codes."""
        default_codes = super()._get_default_payment_method_codes()
        if self.code != 'vastpay':
            return default_codes
        return {'card'}

    @api.constrains('code', 'available_currency_ids', 'available_country_ids')
    def _check_vastpay_availability(self):
        """VastPay is SAR-only and operates only in Saudi Arabia."""
        for provider in self:
            if provider.code != 'vastpay':
                continue
            bad_ccy = provider.available_currency_ids.filtered(
                lambda c: c.name != 'SAR'
            )
            if bad_ccy:
                raise ValidationError(_(
                    "VastPay only supports SAR. Remove: %(names)s",
                    names=', '.join(bad_ccy.mapped('name')),
                ))
            bad_country = provider.available_country_ids.filtered(
                lambda c: c.code != 'SA'
            )
            if bad_country:
                raise ValidationError(_(
                    "VastPay is only available in Saudi Arabia. Remove: "
                    "%(names)s",
                    names=', '.join(bad_country.mapped('name')),
                ))
