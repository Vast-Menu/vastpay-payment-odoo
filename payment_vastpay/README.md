# VastPay POS Payment for Odoo 18

Accept **Point of Sale** payments in Odoo 18 through **VastPay** — the customer
scans a QR code at the till, pays on the VastPay PWA / App Clip, and VastPay
confirms the payment back to Odoo via webhook.

- **Version:** 18.0.1.0.0
- **License:** LGPL-3
- **Category:** Point of Sale
- **Author:** [Vast Group](https://vast-pay.com)
- **Currency:** SAR (Saudi Riyal) only
- **Availability:** Saudi Arabia
- **API Docs:** [VastPay Postman Collection](https://documenter.getpostman.com/view/50697047/2sBXiesu8J)

---

## Features

- **POS QR payment** — a QR code for the VastPay hosted payment page is shown
  on the POS payment screen; no card data touches Odoo.
- **Two-step OAuth** (`client_id` + `client_secret`) with a long-lived token and
  automatic re-authentication on HTTP 401.
- **Webhook-driven confirmation** — VastPay notifies Odoo; the webhook is
  treated only as a trigger and the invoice is **re-fetched** from VastPay for
  the authoritative status/amount (protects the public endpoint from spoofing).
- **Opt-in order closing** — by default a paid webhook records the payment and
  amount but leaves the POS order open for the cashier to validate. Two
  independent provider flags can enable automatic validation and invoicing.
- **Test / Live environments** via the standard Odoo provider state, plus a
  selectable **PWA version** (`pwa` or `pwa-v2` preview).
- **SAR / Saudi Arabia only** — enforced in the UI and by a model constraint.
- **Test Connection** and **Register Webhook** buttons in the provider form.

---

## How it works

```
Cashier selects "VastPay" on a POS order
        │
        ▼
POS calls pos.payment.method.vastpay_make_payment()
        ├── reads the VastPay Stand (Table ID) from the POS config
        ├── provider._vastpay_create_invoice(table_id, total, order_number, invoice_number)
        │       └── POST /invoices → { data: { id, table_id, status } }
        ├── stores a vastpay.pos.payment tracking record
        └── returns the PWA URL + a QR image
        │
        ▼
QR shown on the payment screen → customer scans → pays on VastPay PWA
        │
        ▼
VastPay → POST /payment/vastpay/webhook   (public, CSRF off)
        ├── locate the tracking record (by VastPay invoice id; table_id fallback)
        ├── re-fetch GET /invoices/{id}  → authoritative status + amount
        └── status "paid":
              • record amount on the tracking record (always)
              • auto-validate POS order   → only if "Auto-validate" flag ON
              • auto-create invoice       → only if "Auto-invoice" flag ON
        │
        ▼
POS screen polls vastpay_poll_status() → marks the payment line paid
```

`table_id` is **not** generated per payment — it is the VastPay-provided
identifier of a physical QR stand, configured per Point of Sale (and thus per
branch). It is reused for every payment taken at that stand.

---

## Installation

> **Odoo version:** this is the **Odoo 18** release (`18.0.x`). Branches are
> named after the Odoo series — use the `19.0` branch for Odoo 19.
> Available on **Odoo.sh** and **On Premise**; **not** on Odoo Online (SaaS).

1. Get the module:
   - **Odoo Apps store** — download *VastPay POS Payment* for **v18**, or
   - **Git** — clone this repository and check out the `18.0` branch.
2. Place the `payment_vastpay/` folder in your Odoo **addons path** (or deploy
   the repository on **Odoo.sh**).
3. Restart Odoo, enable **Developer Mode**, then **Apps → Update Apps List**.
4. Search for **VastPay POS Payment** and click **Install**. It automatically
   installs the **Point of Sale** and **Payment** apps.
5. On install a **VastPay QR** POS payment method is created and added to your
   Points of Sale (no manual setup needed). Continue with **Configuration**.

---

## Configuration

1. Install **VastPay POS Payment**. The provider ships in **Test Mode** by
   default (no credentials yet), so it is **hidden in the POS** until you
   enter the API credentials below.
2. **Accounting → Payment Providers → VastPay**:
   - Enter **Client ID** / **Client Secret** (system-group only, masked).
     VastPay appears in the POS only once **both** are set.
   - Choose **PWA Version** (Default `pwa`, or `v2`/preview `pwa-v2`).
   - Keep state **Test Mode** (uses `*-staging.vast-pay.com`) or set
     **Enabled** (production). **Disabled** hides VastPay in the POS.
   - Click **Test Connection**, then **Register Webhook** (registers
     `{web.base.url}/payment/vastpay/webhook`; ensure `web.base.url` is publicly
     reachable).
   - Optionally enable **Auto-validate POS order on payment** and
     **Auto-invoice paid POS order** (both default OFF).
3. **Point of Sale → Configuration → Point of Sale**: set the
   **VastPay Stand (Table ID)** for the register.
4. A **VastPay QR** payment method is created automatically on install and
   added to your existing Points of Sale (new ones pick it up automatically).
   Under **Point of Sale → Configuration → Payment Methods** you only need to
   confirm it is linked to the VastPay provider and present on the register you
   use.
5. Open a register and take a VastPay payment.

> Currency and country are locked to **SAR** / **Saudi Arabia**: a non-SAR POS
> is refused at payment time, and the provider's Currencies/Countries fields are
> read-only for VastPay.

---

## Environments

| Provider state | PWA Version | API base (& webhook) | QR / PWA target |
|---|---|---|---|
| Test Mode | Default | `api-staging.vast-pay.com` | `pwa-staging.vast-pay.com` |
| Test Mode | v2 | `api-staging.vast-pay.com` | `pwa-v2-staging.vast-pay.com` |
| Enabled (Live) | Default | `api.vast-pay.com` | `pwa.vast-pay.com` |
| Enabled (Live) | v2 | `api.vast-pay.com` | `pwa-v2.vast-pay.com` |

### VastPay API endpoints used

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/auth/get-code` | Get authorization code |
| `POST` | `/auth/get-access-token` | Exchange code for access token |
| `POST` | `/invoices` | Create a payment invoice |
| `GET`  | `/invoices/{id}` | Re-fetch authoritative status/amount |
| `PATCH`| `/invoices/cancel/{id}` | Cancel an invoice |
| `PATCH`| `/integrations/webhook/update?webhook_url={url}` | Register the webhook |

### Odoo controller routes

| Method | Route | Auth | Purpose |
|--------|-------|------|---------|
| `POST` | `/payment/vastpay/webhook` | public, CSRF off | Receive VastPay notifications (always HTTP 200) |
| `GET/POST` | `/payment/vastpay/return` | public, CSRF off | Customer landing page after paying |

---

## Models

- **`payment.provider`** (extended) — VastPay credentials, API client, the two
  auto-close flags, `vastpay_pwa_version`, SAR/Saudi-Arabia constraint, and the
  Test Connection / Register Webhook actions.
- **`pos.payment.method`** (extended) — adds `vastpay` to the payment-terminal
  selection, links the VastPay provider, ships the brand logo, and exposes the
  RPC endpoints used by the POS frontend (`vastpay_make_payment`,
  `vastpay_poll_status`, `vastpay_cancel_payment`).
- **`pos.config`** (extended) — `vastpay_table_id` (the QR-stand identifier).
- **`vastpay.pos.payment`** (new) — correlates a VastPay invoice ↔ table_id ↔
  POS order, holds status/amount, and applies the flag-gated order closing.

POS frontend (`static/src/app/`): a `PaymentInterface` that shows the QR dialog
and polls for confirmation, plus a `PaymentScreen` patch so auto-validation
follows the provider flag rather than the global POS setting.

---

## Test environment (Docker)

A Docker Compose stack is included for local testing:

```bash
docker compose up -d                 # Postgres 16 + Odoo 18 + cloudflared tunnel
docker compose run --rm odoo odoo -i payment_vastpay -d vastpay --stop-after-init
docker compose logs tunnel           # public HTTPS URL for the webhook
```

Set Odoo's `web.base.url` to the printed tunnel URL before registering the
webhook. The VastPay sandbox is available 10:00–22:00 on weekdays.

---

## Requirements

- **Odoo 18.0** with **Point of Sale** and **Payment** apps.
- Python `requests` and `qrcode` (both ship with Odoo).
- A **VastPay merchant account** — [vast-pay.com](https://vast-pay.com).
