import { Dialog } from "@web/core/dialog/dialog";
import { Component } from "@odoo/owl";

export class VastPayQRDialog extends Component {
    static components = { Dialog };
    static template = "payment_vastpay.VastPayQRDialog";
    static props = {
        close: Function,
        qrImage: String,
        amountLabel: String,
        paymentUrl: String,
        onCancel: { type: Function, optional: true },
    };

    cancel() {
        this.props.onCancel?.();
        this.props.close();
    }
}
