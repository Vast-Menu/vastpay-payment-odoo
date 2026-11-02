# vastpay-payment-odoo

VastPay payment integration for **Odoo Point of Sale**.

This repository follows the standard Odoo layout: the module lives in
[`payment_vastpay/`](payment_vastpay/) and branches are named after the Odoo
version series.

| Branch | Odoo version | Status     |
|--------|--------------|------------|
| `20.0` | Odoo 20      | Current    |
| `19.0` | Odoo 19      | Maintained |
| `18.0` | Odoo 18      | Maintained |

See [`payment_vastpay/README.md`](payment_vastpay/README.md) for module
documentation, configuration, and the VastPay API reference.

## Upgrading from 19.0

1. Upgrade the database to Odoo 20 (Odoo upgrade service or OpenUpgrade).
2. Switch the module checkout to `20.0` and run
   `odoo -d <db> -u payment_vastpay --stop-after-init`.

The migration maps the 19.0 provider environment to Live / Active /
Use VastPay Staging and sets the POS method to External QR with provider
VastPay. If the database upgrade already dropped the old `state` column, the
Staging flag cannot be recovered: tick it by hand (the log warns). Hard-refresh
the POS afterwards.

## Local testing

A Docker Compose stack (Postgres 16 + Odoo 20 + a cloudflared tunnel for
inbound webhooks) is included:

```bash
docker compose up -d
docker compose run --rm odoo odoo -i payment_vastpay -d vastpay --stop-after-init
docker compose logs tunnel    # public HTTPS URL for the VastPay webhook
```

## License

LGPL-3 — see [LICENSE](LICENSE).
