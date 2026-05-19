import { _t } from "@web/core/l10n/translation";
import { PaymentInterface } from "@point_of_sale/app/utils/payment/payment_interface";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { VastPayQRDialog } from "@payment_vastpay/app/vastpay_qr_dialog";

const POLL_INTERVAL = 60000; // ms (60 seconds)
const PAYMENT_TIMEOUT = 300000; // ms (5 minutes)

export class PaymentVastPay extends PaymentInterface {
    setup() {
        super.setup(...arguments);
        this.pollTimer = null;
        this.timeoutTimer = null;
        this.closeDialog = null;
        this.invoiceId = null;
        this._resolve = null;
        this._settled = true;
        this._checking = false;
    }

    sendPaymentRequest(uuid) {
        super.sendPaymentRequest(uuid);
        return this._vastpayPay();
    }

    sendPaymentCancel(order, uuid) {
        super.sendPaymentCancel(order, uuid);
        this._vastpayCancel();
        return Promise.resolve(true);
    }

    close() {
        super.close();
        // Always resolve any in-flight request so the POS clears its
        // `paymentTerminalInProgress` flag (otherwise the next electronic
        // payment is blocked with "already an electronic payment in progress").
        this._vastpayCancel();
    }

    _call(action, data) {
        return this.env.services.orm.silent.call(
            "pos.payment.method",
            action,
            [[this.payment_method_id.id], data]
        );
    }

    _line() {
        return (
            this.pos.getPendingPaymentLine("vastpay") ||
            this.pos.getOrder()?.getSelectedPaymentline()
        );
    }

    _cleanup() {
        clearTimeout(this.pollTimer);
        clearTimeout(this.timeoutTimer);
        this.pollTimer = null;
        this.timeoutTimer = null;
        if (this.closeDialog) {
            this.closeDialog();
            this.closeDialog = null;
        }
    }

    _showError(message) {
        this.env.services.dialog.add(AlertDialog, {
            title: _t("VastPay Error"),
            body: message,
        });
    }

    /**
     * Settle the in-flight payment exactly once: stop timers/dialog, set the
     * line status, optionally show an error, and resolve the promise returned
     * by sendPaymentRequest so the POS can clear its in-progress flag.
     */
    _finish(success, errorMsg) {
        if (this._settled) {
            return;
        }
        this._settled = true;
        this._cleanup();
        const line = this._line();
        line?.setPaymentStatus(success ? "done" : "retry");
        if (errorMsg) {
            this._showError(errorMsg);
        }
        const resolve = this._resolve;
        this._resolve = null;
        if (resolve) {
            resolve(success);
        }
    }


    async _vastpayPay() {
        const order = this.pos.getOrder();
        const line = order?.getSelectedPaymentline();
        if (!line || line.amount <= 0) {
            this._showError(_t("Cannot process a non-positive amount."));
            return false;
        }

        this.invoiceId = null;
        let resp;
        try {
            resp = await this._call("vastpay_make_payment", {
                amount: line.amount,
                pos_reference: order.name,
                session_id: this.pos.session.id,
            });
        } catch {
            this._showError(_t("Could not reach Odoo. Please try again."));
            line.setPaymentStatus("retry");
            return false;
        }

        if (!resp || resp.error) {
            this._showError(resp?.error || _t("VastPay payment could not be started."));
            line.setPaymentStatus("retry");
            return false;
        }

        this.invoiceId = resp.invoice_id;
        line.transaction_id = resp.invoice_id;
        line.setPaymentStatus("waitingCard");

        const amountLabel =
            this.env.utils?.formatCurrency?.(line.amount) ?? String(line.amount);
        this.closeDialog = this.env.services.dialog.add(VastPayQRDialog, {
            qrImage: resp.qr_image,
            amountLabel,
            paymentUrl: resp.payment_url,
            onCancel: () => this._vastpayCancel(),
            onCheck: () => this._manualCheck(),
            // Without a webhook the paid status only arrives via polling;
            // give the cashier an explicit "check now" action.
            showCheckButton: !this.payment_method_id.vastpay_webhook_registered,
        });

        this._settled = false;
        return new Promise((resolve) => {
            this._resolve = resolve;
            this.timeoutTimer = setTimeout(() => {
                this._serverCancel();
                this._finish(false, _t("VastPay payment timed out."));
            }, PAYMENT_TIMEOUT);
            this._poll();
        });
    }

    async _poll() {
        // Polling stops via _settled (set by _finish) when the payment
        // resolves, is cancelled, or times out, and via the PaymentInterface
        // close()/sendPaymentCancel lifecycle when the cashier leaves the
        // payment screen. We deliberately do NOT probe the current POS
        // screen here: that internal API differs across Odoo versions
        // (pos.mainScreen is unused in Odoo 19) and tripping it aborted the
        // payment before the first status poll.
        if (this._settled) {
            return;
        }
        let resp;
        try {
            resp = await this._call("vastpay_poll_status", {
                invoice_id: this.invoiceId,
            });
        } catch {
            if (!this._settled) {
                this.pollTimer = setTimeout(() => this._poll(), POLL_INTERVAL);
            }
            return;
        }
        if (this._settled) {
            return;
        }

        const state = resp?.state;
        if (state === "paid") {
            this._finish(true);
        } else if (state === "cancelled") {
            this._finish(false);
        } else if (state === "error") {
            this._finish(false, resp?.error || _t("VastPay payment failed."));
        } else {
            this.pollTimer = setTimeout(() => this._poll(), POLL_INTERVAL);
        }
    }

    /**
     * Force an immediate status check (used by the QR dialog's manual
     * "Check payment" button when no webhook is registered). Reuses the
     * same server endpoint as the poll loop, which re-fetches the invoice
     * from VastPay. Returns a result the dialog can render:
     *   {state} on success, {unreachable:true} if Odoo/VastPay is down,
     *   or null if the payment was already settled/closed.
     */
    async _manualCheck() {
        if (this._settled || this._checking) {
            return null;
        }
        this._checking = true;
        try {
            let resp;
            try {
                resp = await this._call("vastpay_poll_status", {
                    invoice_id: this.invoiceId,
                });
            } catch {
                return { unreachable: true };
            }
            if (this._settled) {
                return null;
            }
            const state = resp?.state;
            if (state === "paid") {
                this._finish(true);
            } else if (state === "cancelled") {
                this._finish(false);
            } else if (state === "error") {
                this._finish(false, resp?.error || _t("VastPay payment failed."));
            }
            return resp || { state: "pending" };
        } finally {
            this._checking = false;
        }
    }

    _serverCancel() {
        const invoiceId = this.invoiceId;
        if (invoiceId) {
            this._call("vastpay_cancel_payment", { invoice_id: invoiceId }).catch(
                () => {} // best effort
            );
        }
    }

    _vastpayCancel() {
        this._serverCancel();
        this._finish(false);
    }
}
