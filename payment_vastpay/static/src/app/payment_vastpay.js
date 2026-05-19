import { _t } from "@web/core/l10n/translation";
import { PaymentInterface } from "@point_of_sale/app/payment/payment_interface";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { VastPayQRDialog } from "@payment_vastpay/app/vastpay_qr_dialog";

const POLL_INTERVAL = 60000; // ms (1 min)
const PAYMENT_TIMEOUT = 300000; // ms (5 min)

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
        // Live "auto-validate POS order" flag, refreshed from every server
        // response so the close behaviour follows the current provider
        // setting rather than the value cached when the POS session opened.
        this._autoValidate = null;
        this._busBound = false;
        this._subscribeBus();
    }

    /**
     * Subscribe once to the server push sent when a VastPay payment is
     * confirmed out of band (webhook / late order-sync). The bus message
     * is only a trigger: we react by running the same safe poll RPC, so a
     * confirmed payment completes the open screen immediately instead of
     * waiting for the next 60s poll tick. The periodic poll remains as a
     * fallback if the bus is unavailable.
     */
    _subscribeBus() {
        if (this._busBound) {
            return;
        }
        try {
            this.pos.data.connectWebSocket(
                "VASTPAY_PAYMENT_NOTIFICATION",
                ({ invoice_id }) => {
                    if (
                        this._settled ||
                        !this.invoiceId ||
                        invoice_id !== this.invoiceId
                    ) {
                        return;
                    }
                    this._checkNow();
                }
            );
            this._busBound = true;
        } catch {
            // Bus unavailable; the periodic poll still covers completion.
        }
    }

    /** Run a status check immediately (bus-triggered), reusing the poll path. */
    _checkNow() {
        clearTimeout(this.pollTimer);
        this.pollTimer = null;
        this._poll();
    }

    send_payment_request(uuid) {
        super.send_payment_request(uuid);
        return this._vastpayPay();
    }

    send_payment_cancel(order, uuid) {
        super.send_payment_cancel(order, uuid);
        this._vastpayCancel();
        return Promise.resolve(true);
    }

    close() {
        super.close();
        // Resolve any in-flight request LOCALLY so the POS clears its
        // `paymentTerminalInProgress` flag (otherwise the next electronic
        // payment is blocked with "already an electronic payment in
        // progress"). Crucially we do NOT call _serverCancel() here: a
        // screen teardown / navigate-away / browser close must not cancel
        // the VastPay invoice -- the customer may still pay and the
        // webhook will confirm it. The line is reconciled with the server
        // when the order's payment screen is next opened (see
        // PaymentScreen._vastpayReconcileOrphanLines). Explicit cashier
        // cancel still goes through sendPaymentCancel -> _vastpayCancel.
        this._finish(false);
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
            this.pos.get_order()?.get_selected_paymentline()
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
     * Resolve the auto-validate decision from the freshest source: the live
     * flag returned by the server, falling back to the POS-loaded payment
     * method value only if no server response carried it yet.
     */
    _liveAutoValidate() {
        if (this._autoValidate !== null) {
            return !!this._autoValidate;
        }
        return !!this.payment_method_id?.vastpay_auto_validate_order;
    }

    _trackAutoValidate(resp) {
        if (resp && resp.auto_validate !== undefined) {
            this._autoValidate = !!resp.auto_validate;
        }
    }

    /**
     * Settle the in-flight payment exactly once: stop timers/dialog, set the
     * line status, optionally show an error, and resolve the promise returned
     * by send_payment_request so the POS can clear its in-progress flag.
     */
    _finish(success, errorMsg) {
        if (this._settled) {
            return;
        }
        this._settled = true;
        this._cleanup();
        // The parent PaymentScreen.sendPaymentRequest auto-validates the
        // order right after this promise resolves, gated on
        // pos.config.auto_validate_terminal_payment. Set it to the live
        // VastPay flag so a disabled flag is honoured even if the POS cached
        // a stale (enabled) value at session start.
        if (success) {
            this.pos.config.auto_validate_terminal_payment =
                this._liveAutoValidate();
        }
        const line = this._line();
        line?.set_payment_status(success ? "done" : "retry");
        if (errorMsg) {
            this._showError(errorMsg);
        }
        const resolve = this._resolve;
        this._resolve = null;
        if (resolve) {
            resolve(success);
        }
    }


    /**
     * Open the QR dialog for a given invoice and return the promise the
     * POS awaits, resolved once the payment settles (poll/bus/timeout).
     * Shared by the "create new invoice" and "resume existing invoice"
     * paths so a resume never re-creates an invoice.
     */
    _startSession({ invoice_id, qr_image, payment_url, amount }) {
        const order = this.pos.get_order();
        const line = order?.get_selected_paymentline();
        this.invoiceId = invoice_id;
        if (line) {
            line.transaction_id = invoice_id;
            // Snapshot the order total this QR was generated for. If the
            // cashier later adds/removes an item (or changes a qty, price
            // or discount), the order total no longer matches and the
            // still-payable QR is stale — it would charge the customer the
            // wrong amount. The payment-screen reconcile / add-product hook
            // cancels the invoice at VastPay and drops the line so a fresh
            // QR is issued for the corrected amount.
            line.vastpay_order_total_snapshot =
                order != null ? order.get_total_with_tax() : null;
            line.set_payment_status("waitingCard");
        }
        const amountLabel =
            this.env.utils?.formatCurrency?.(amount) ?? String(amount);
        this.closeDialog = this.env.services.dialog.add(VastPayQRDialog, {
            qrImage: qr_image,
            amountLabel,
            paymentUrl: payment_url,
            onClose: () => this._vastpayCloseOnly(),
            // Always offer an explicit "check now" action: even with a
            // webhook, confirmation can lag or fail, so the cashier can
            // force an immediate status re-fetch.
            onCheck: () => this._manualCheck(),
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

    async _vastpayPay() {
        const order = this.pos.get_order();
        const line = order?.get_selected_paymentline();
        if (!line || line.amount <= 0) {
            this._showError(_t("Cannot process a non-positive amount."));
            return false;
        }

        this.invoiceId = null;

        // Retry on a line that already has an invoice: don't blindly
        // recreate (that would orphan a still-payable invoice). Pull the
        // existing invoice's authoritative status first.
        const existingId = line.transaction_id;
        if (existingId) {
            let r = null;
            try {
                r = await this._call("vastpay_reconcile_status", {
                    invoice_id: existingId,
                });
            } catch {
                r = null;
            }
            if (r && r.state === "paid") {
                // Already paid (e.g. webhook while dialog was gone): just
                // complete the order, no new invoice.
                this._trackAutoValidate(r);
                this.invoiceId = existingId;
                this._finish(true);
                return true;
            }
            if (
                r &&
                (r.state === "pending" || r.state === "draft") &&
                r.qr_image
            ) {
                // Still alive and NOT cancelled -> resume the SAME invoice.
                this._trackAutoValidate(r);
                return this._startSession({
                    invoice_id: existingId,
                    qr_image: r.qr_image,
                    payment_url: r.payment_url,
                    amount: line.amount,
                });
            }
            // cancelled / expired / error / unknown -> fall through and
            // create a fresh invoice below.
        }

        let resp;
        try {
            resp = await this._call("vastpay_make_payment", {
                amount: line.amount,
                pos_reference: order.name,
                pos_order_uuid: order.uuid,
                session_id: this.pos.session.id,
            });
        } catch {
            this._showError(_t("Could not reach Odoo. Please try again."));
            line.set_payment_status("retry");
            return false;
        }

        if (!resp || resp.error) {
            this._showError(resp?.error || _t("VastPay payment could not be started."));
            line.set_payment_status("retry");
            return false;
        }

        this._trackAutoValidate(resp);
        return this._startSession({
            invoice_id: resp.invoice_id,
            qr_image: resp.qr_image,
            payment_url: resp.payment_url,
            amount: line.amount,
        });
    }

    async _poll() {
        // Polling stops via _settled (set by _finish) when the payment
        // resolves, is cancelled, or times out, and via the PaymentInterface
        // close()/send_payment_cancel lifecycle when the cashier leaves the
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

        this._trackAutoValidate(resp);
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
            this._trackAutoValidate(resp);
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

    /**
     * Close the QR sheet without cancelling the VastPay invoice. Settles
     * the in-flight request LOCALLY (stops the poll/timeout, clears the
     * POS in-progress flag) but deliberately does NOT call _serverCancel:
     * the customer may still pay and the webhook / session bus listener /
     * payment-screen reconcile will complete the order. The ONLY path that
     * cancels at VastPay is deleting the payment line (PaymentScreen
     * .deletePaymentLine -> vastpay_cancel_payment).
     */
    _vastpayCloseOnly() {
        this._finish(false);
    }
}
