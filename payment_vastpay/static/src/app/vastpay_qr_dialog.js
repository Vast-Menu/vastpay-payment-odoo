import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { Component, useState } from "@odoo/owl";

export class VastPayQRDialog extends Component {
    static components = { Dialog };
    static template = "payment_vastpay.VastPayQRDialog";
    static props = {
        close: Function,
        qrImage: String,
        amountLabel: String,
        paymentUrl: String,
        onCancel: { type: Function, optional: true },
        onCheck: { type: Function, optional: true },
        showCheckButton: { type: Boolean, optional: true },
    };

    setup() {
        this.state = useState({ checking: false, message: "" });
    }

    cancel() {
        this.props.onCancel?.();
        this.props.close();
    }

    async check() {
        if (this.state.checking || !this.props.onCheck) {
            return;
        }
        this.state.checking = true;
        this.state.message = "";
        try {
            const resp = await this.props.onCheck();
            // null => payment already settled and this dialog is being
            // closed by the payment interface; nothing to show.
            if (!resp) {
                return;
            }
            if (resp.unreachable) {
                this.state.message = _t("Could not reach VastPay. Please try again.");
            } else if (resp.state === "paid" || resp.state === "cancelled") {
                // The payment interface closes this dialog.
            } else if (resp.state === "error") {
                this.state.message = _t("VastPay reported a payment error.");
            } else {
                this.state.message = _t("Not paid yet. Ask the customer to complete the payment, then check again.");
            }
        } finally {
            this.state.checking = false;
        }
    }
}
