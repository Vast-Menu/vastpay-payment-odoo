import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PosStore } from "@point_of_sale/app/services/pos_store";

/**
 * Session-level VastPay payment listener.
 *
 * `PaymentVastPay._subscribeBus` already reacts to the server push, but it
 * is bound to a single in-flight payment attempt: it ignores the push once
 * the attempt has settled or the QR dialog/payment screen is gone. So when
 * a webhook confirms a payment while the cashier is NOT watching that
 * dialog (moved to another order/screen, closed the dialog), the bus
 * message arrives but nothing on screen updates and the cashier has to
 * refresh the browser.
 *
 * This subscribes ONCE for the whole POS session (mirrors
 * `pos_online_payment`'s `PosStore` patch) so a confirmed VastPay payment
 * reconciles the matching order's payment line live, regardless of which
 * screen is showing. The bus message carries no sensitive data — only a
 * trigger to run the same authoritative reconcile RPC the payment screen
 * already uses.
 */
patch(PosStore.prototype, {
    async setup() {
        await super.setup(...arguments);
        try {
            this.data.connectWebSocket(
                "VASTPAY_PAYMENT_NOTIFICATION",
                ({ invoice_id, state }) => {
                    if (!invoice_id || !["paid", "cancelled", "error"].includes(state)) {
                        return;
                    }
                    this._vastpaySyncConfirmedPayment(invoice_id);
                }
            );
        } catch {
            // Bus unavailable; the payment screen still reconciles on open
            // and the in-flight poll still covers completion.
        }
    },

    /**
     * Reconcile every open order's VastPay line matching `invoiceId` with
     * the authoritative server status. Skips a line whose terminal is still
     * mid-flight — the live `PaymentVastPay` instance owns that one and
     * handles it itself (avoids racing/double work).
     */
    async _vastpaySyncConfirmedPayment(invoiceId) {
        const orders = this.models["pos.order"].filter((o) => !o.finalized);
        for (const order of orders) {
            for (const line of order.payment_ids || []) {
                const pm = line.payment_method_id;
                if (pm?.use_payment_terminal !== "vastpay") {
                    continue;
                }
                if (line.transaction_id !== invoiceId) {
                    continue;
                }
                const term = pm.payment_terminal;
                if (term && term._settled === false) {
                    continue;
                }
                if (["done"].includes(line.getPaymentStatus?.())) {
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
                    continue;
                }
                if (resp && resp.state === "paid") {
                    if (resp.amount) {
                        line.setAmount(resp.amount);
                    }
                    line.setPaymentStatus("done");
                } else if (resp && resp.state === "cancelled") {
                    line.setPaymentStatus("retry");
                }
            }
        }
    },

    /**
     * Adding a product to an order that already has a live (unpaid) VastPay
     * QR makes that QR stale: it was generated for the old order total, so
     * the customer would be charged the wrong amount. Catch the change at
     * the single add-product funnel and void the stale line right away
     * (removal / qty / discount changes are caught when the cashier returns
     * to the payment screen — see PaymentScreen._vastpayVoidStaleLines).
     */
    async addLineToOrder(vals, order) {
        const line = await super.addLineToOrder(...arguments);
        if (line) {
            // The order definitely changed here, so no snapshot guard.
            this._vastpayVoidStaleLines(order, { requireSnapshot: false });
        }
        return line;
    },

    /**
     * Single chokepoint for setting/removing the current order's customer
     * (partner list, customer button on the product/payment screen all
     * funnel through here). When the customer is removed, void any pending
     * VastPay QR that is set to auto-invoice — that QR can no longer be
     * invoiced once paid (see _vastpayVoidLinesNeedingPartner).
     */
    setPartnerToCurrentOrder(partner) {
        const res = super.setPartnerToCurrentOrder(...arguments);
        if (!partner) {
            this._vastpayVoidLinesNeedingPartner(this.getOrder());
        }
        return res;
    },

    /**
     * Cancel at VastPay and remove every unpaid VastPay payment line whose
     * QR no longer matches the order total.
     *
     * A "paid"/"reversed" line is captured money and is never touched. A
     * line with no invoice yet (transaction_id unset) has no QR a customer
     * could pay, so it is left for the cashier. For the rest:
     *   - if the line's live terminal is still mid-flight, cancel through it
     *     (cancels at VastPay AND clears the POS in-progress flag);
     *   - otherwise fire the cancel RPC directly (best effort).
     * The line is then dropped so a fresh QR is issued for the new total.
     *
     * `requireSnapshot` (default true): only void when the snapshot taken at
     * QR creation differs from the current total. The add-product hook
     * passes false — the order is known to have just changed, and a line
     * created before this snapshot existed (e.g. across a browser reload)
     * must still be invalidated on a content change.
     */
    _vastpayVoidStaleLines(order, { requireSnapshot = true } = {}) {
        if (!order || order.finalized) {
            return;
        }
        const total = order.totalDue;
        const stale = [];
        for (const line of order.payment_ids || []) {
            const pm = line.payment_method_id;
            if (pm?.use_payment_terminal !== "vastpay") {
                continue;
            }
            if (!line.transaction_id) {
                continue; // no QR the customer could pay
            }
            if (line.isDone?.()) {
                continue; // captured payment — never cancel/delete
            }
            if (line.getPaymentStatus?.() === "waitingCancel") {
                continue; // a cancel is already in flight
            }
            const snap = line.vastpay_order_total_snapshot;
            if (requireSnapshot) {
                if (snap === undefined || snap === null) {
                    continue;
                }
                if (order.currency.isZero(snap - total)) {
                    continue; // order unchanged since the QR was generated
                }
            }
            stale.push(line);
        }
        this._vastpayCancelAndDropLines(
            order,
            stale,
            _t(
                "The order changed, so the pending VastPay QR was cancelled. " +
                    "Generate a new QR for the updated amount."
            )
        );
    },

    /**
     * Removing the customer from an order whose VastPay payment auto-invoices
     * would leave a still-payable QR that can never be invoiced (the invoice
     * needs a partner). Cancel and drop those pending lines so the cashier
     * re-selects a customer and issues a fresh QR. Lines whose method does
     * not auto-invoice, already-captured ("done") lines, and lines with no
     * QR yet are left untouched.
     */
    _vastpayVoidLinesNeedingPartner(order) {
        if (!order || order.finalized || order.getPartner()) {
            return;
        }
        const orphan = [];
        for (const line of order.payment_ids || []) {
            const pm = line.payment_method_id;
            if (pm?.use_payment_terminal !== "vastpay") {
                continue;
            }
            if (!pm.vastpay_auto_invoice) {
                continue; // no invoice will be attempted; QR stays valid
            }
            if (!line.transaction_id) {
                continue; // no QR the customer could pay
            }
            if (line.isDone?.()) {
                continue; // captured payment — never cancel/delete
            }
            if (line.getPaymentStatus?.() === "waitingCancel") {
                continue; // a cancel is already in flight
            }
            orphan.push(line);
        }
        this._vastpayCancelAndDropLines(
            order,
            orphan,
            _t(
                "The customer was removed, so the pending VastPay QR was " +
                    "cancelled (this payment auto-invoices the order and needs " +
                    "a customer). Select a customer and generate a new QR."
            )
        );
    },

    /**
     * Cancel each line at VastPay (through its live terminal if one is still
     * mid-flight, otherwise via a direct best-effort RPC), drop it from the
     * order, and show one explanatory warning. Shared by the stale-amount
     * and missing-customer void paths. No-op on an empty list.
     */
    _vastpayCancelAndDropLines(order, lines, message) {
        if (!lines || !lines.length) {
            return;
        }
        for (const line of lines) {
            const pm = line.payment_method_id;
            const term = pm?.payment_terminal;
            if (
                term &&
                term._settled === false &&
                term.invoiceId === line.transaction_id
            ) {
                try {
                    term._vastpayCancel(); // server cancel + settle locally
                } catch {
                    // best effort; still drop the line below
                }
            } else {
                this.env.services.orm.silent
                    .call("pos.payment.method", "vastpay_cancel_payment", [
                        [pm.id],
                        { invoice_id: line.transaction_id },
                    ])
                    .catch(() => {}); // best effort; still drop the line
            }
            order.removePaymentline(line);
        }
        this.env.services.notification?.add?.(message, { type: "warning" });
    },
});
