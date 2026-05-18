import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { patch } from "@web/core/utils/patch";

patch(PaymentScreen.prototype, {
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
