# vastpay-payment-odoo

VastPay payment integration for **Odoo Point of Sale**.

This repository follows the standard Odoo layout: the module lives in
[`payment_vastpay/`](payment_vastpay/) and branches are named after the Odoo
version series.

| Branch | Odoo version | Status     |
|--------|--------------|------------|
| `19.0` | Odoo 19      | Current    |
| `18.0` | Odoo 18      | Maintained |

See [`payment_vastpay/README.md`](payment_vastpay/README.md) for module
documentation, configuration, and the VastPay API reference.

## Local testing

A Docker Compose stack (Postgres 16 + Odoo 19 + a cloudflared tunnel for
inbound webhooks) is included:

```bash
docker compose up -d
docker compose run --rm odoo odoo -i payment_vastpay -d vastpay --stop-after-init
docker compose logs tunnel    # public HTTPS URL for the VastPay webhook
```

## License

LGPL-3 — see [LICENSE](LICENSE).
