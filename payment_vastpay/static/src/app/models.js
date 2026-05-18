import { register_payment_method } from "@point_of_sale/app/services/pos_store";
import { PaymentVastPay } from "@payment_vastpay/app/payment_vastpay";

register_payment_method("vastpay", PaymentVastPay);
