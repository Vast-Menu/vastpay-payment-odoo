import { PaymentScreenPaymentLines } from "@point_of_sale/app/screens/payment_screen/payment_lines/payment_lines";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

// For a VastPay line, the QR sheet can be closed while the invoice is still
// live (the customer can keep paying; it completes via webhook/poll). Closing
// settles the line to "retry", whose stock title "Transaction cancelled" is
// misleading: nothing was cancelled. Only deleting the line cancels at
// VastPay. Swap the title for VastPay lines only.
patch(PaymentScreenPaymentLines.prototype, {
    getPaymentActionState(line) {
        const state = super.getPaymentActionState(...arguments);
        if (
            state.id === "retry" &&
            line.transaction_id &&
            line.payment_method_id?.payment_provider === "vastpay"
        ) {
            state.title = _t("QR still active, waiting for customer");
        }
        return state;
    },
});
