import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
        // Reconcile first (it may mark a line "done" if the customer paid
        // the old-amount QR), THEN void anything still unpaid whose order
        // changed underneath it. Voiding before reconcile resolves could
        // cancel/delete a payment the customer actually completed.
        this._vastpayReconcileOrphanLines().then(() => {
            this.pos._vastpayVoidStaleLines(this.currentOrder, {
                requireSnapshot: true,
            });
        });
    },

    /**
     * Reconcile orphaned VastPay payment lines with the server.
     *
     * A VastPay payment completes the open screen via the live poll/bus
     * only while its QR dialog is alive. If the cashier navigates away or
     * the browser is closed/reopened, the order is restored with the
     * VastPay line still "waiting" but with NO in-flight request — the POS
     * never re-calls sendPaymentRequest for an existing line, so it sits
     * dead ("Waiting for card" / endless spinner) forever, even though the
     * customer may have already paid (the webhook marked it paid
     * server-side).
     *
     * On entering the payment screen we therefore ASK THE SERVER about
     * each such line's invoice (its transaction_id):
     *   - paid      -> mark the line done so the order can be validated
     *                  (server-side _apply_to_pos_order then honours the
     *                  auto-validate flag on sync). The webhook thus closes
     *                  the order even though no dialog was open.
     *   - otherwise -> reset to "retry" so the cashier can act, instead of
     *                  a dead screen (and without blindly re-charging a
     *                  customer who already paid).
     *
     * Best-effort and async: never blocks or breaks the payment screen.
     */
    async _vastpayReconcileOrphanLines() {
        try {
            const order = this.currentOrder;
            const lines = (order && order.payment_ids) || [];
            for (const line of lines) {
                const pm = line.payment_method_id;
                if (pm?.use_payment_terminal !== "vastpay") {
                    continue;
                }
                const status = line.get_payment_status?.();
                // Any non-final VastPay line is a reconciliation
                // candidate. After a browser close the in-flight request
                // is resolved locally and the line ends up "retry" (not
                // "waitingCard"), so we must cover that too.
                if (!["waiting", "waitingCard", "retry", "pending"].includes(status)) {
                    continue;
                }
                const term = pm.payment_terminal;
                // An in-flight request (dialog open) handles itself.
                if (term && term._settled === false) {
                    continue;
                }
                const invoiceId = line.transaction_id;
                if (!invoiceId) {
                    if (status !== "retry") {
                        line.set_payment_status("retry");
                    }
                    continue;
                }
                let resp;
                try {
                    resp = await this.env.services.orm.silent.call(
                        "pos.payment.method",
                        "vastpay_reconcile_status",
                        [[pm.id], { invoice_id: invoiceId }]
                    );
                } catch {
                    if (status !== "retry") {
                        line.set_payment_status("retry");
                    }
                    continue;
                }
                if (resp && resp.state === "paid") {
                    if (resp.amount) {
                        line.set_amount(resp.amount);
                    }
                    line.set_payment_status("done");
                } else {
                    line.set_payment_status("retry");
                }
            }
        } catch {
            // best effort; never break the payment screen
        }
    },

    /**
     * Deleting a VastPay payment line must release the invoice at VastPay,
     * otherwise the customer could still pay a QR for an order line the
     * cashier already removed. Native `deletePaymentLine` only sends a
     * cancel for terminal-in-progress states (waiting/waitingCard/timeout);
     * a VastPay line is commonly deleted from "retry"/"pending" (e.g. after
     * a local cancel or an orphan reconcile), where native removes it
     * silently. For those states we explicitly cancel the invoice first.
     * A "done" (already paid) line is left untouched — that is a captured
     * payment, not a cancellable invoice.
     */
    deletePaymentLine(uuid) {
        const line = this.paymentLines.find((l) => l.uuid === uuid);
        const pm = line?.payment_method_id;
        if (pm?.use_payment_terminal === "vastpay" && line.transaction_id) {
            const status = line.get_payment_status?.();
            const nativeCancels = ["waiting", "waitingCard", "timeout"].includes(
                status
            );
            if (!nativeCancels && !["waitingCancel", "done"].includes(status)) {
                this.env.services.orm.silent
                    .call("pos.payment.method", "vastpay_cancel_payment", [
                        [pm.id],
                        { invoice_id: line.transaction_id },
                    ])
                    .catch(() => {}); // best effort; still remove the line
            }
        }
        return super.deletePaymentLine(...arguments);
    },

    /**
     * Odoo POS auto-validates terminal payments whenever
     * `pos.config.auto_validate_terminal_payment` is set (it defaults to
     * True). For VastPay the order must be auto-closed ONLY when the
     * provider flag "Auto-validate POS order on payment" is enabled,
     * regardless of that global POS setting. We scope the global flag to
     * the VastPay flag for the duration of the VastPay payment and restore
     * it afterwards, leaving other payment methods untouched.
     */
    async sendPaymentRequest(line) {
        const pm = line?.payment_method_id;
        if (pm?.use_payment_terminal !== "vastpay") {
            return super.sendPaymentRequest(...arguments);
        }
        // Auto-invoicing a paid POS order needs a customer (the invoice
        // partner). VastPay confirms out of band (webhook/poll), so once the
        // QR is paid there is no chance to ask for one — the server-side
        // auto-invoice would just fail. Refuse to start the payment until a
        // customer is set, rather than capture money for an order that can
        // never be invoiced.
        if (pm.vastpay_auto_invoice && !this.currentOrder.get_partner()) {
            this.env.services.dialog.add(AlertDialog, {
                title: _t("Customer required"),
                body: _t(
                    "This VastPay payment automatically invoices the order, " +
                        "which requires a customer. Select a customer before " +
                        "taking the payment."
                ),
            });
            if (line.get_payment_status?.() !== "retry") {
                line.set_payment_status("retry");
            }
            return false;
        }
        const cfg = this.pos.config;
        const original = cfg.auto_validate_terminal_payment;
        cfg.auto_validate_terminal_payment = !!pm.vastpay_auto_validate_order;
        try {
            return await super.sendPaymentRequest(...arguments);
        } finally {
            cfg.auto_validate_terminal_payment = original;
        }
    },
});
