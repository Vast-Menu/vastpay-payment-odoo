import { registry } from "@web/core/registry";
import { PaymentVastPay } from "@payment_vastpay/app/payment_vastpay";

registry.category("pos_payment_providers").add("vastpay", PaymentVastPay);
