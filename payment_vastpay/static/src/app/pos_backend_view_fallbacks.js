import { registry } from "@web/core/registry";
import { Component, xml } from "@odoo/owl";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { formView } from "@web/views/form/form_view";

// Stock Odoo form views reachable from inside the POS UI -- the
// `pos.payment.method` form (VastPay method) and, via the payment provider,
// the `res.currency` form -- reference JS components that ship only in
// `web.assets_backend` and are stripped from the POS bundle
// (`point_of_sale.assets_prod` removes `point_of_sale/static/src/backend/**`
// and never pulls `account/static/src/components/**`). Parsing/loading those
// forms inside POS then crashes the Owl lifecycle with a registry
// KeyNotFoundError. Register inert POS-only fallbacks so the forms render in
// POS; the real backend implementations are untouched (this file is loaded
// into `point_of_sale._assets_pos` only, never `web.assets_backend`).

class PosPaymentProviderCardsStub extends Component {
    static template = xml``;
    static props = { ...standardWidgetProps };
}

const viewWidgets = registry.category("view_widgets");
if (!viewWidgets.contains("pos_payment_provider_cards")) {
    viewWidgets.add("pos_payment_provider_cards", {
        component: PosPaymentProviderCardsStub,
    });
}

// `account` sets js_class="currency_form" on the currency form; its only
// addition over the standard form view is a backend confirmation dialog that
// is irrelevant inside POS, so falling back to the plain form view is safe.
const views = registry.category("views");
if (!views.contains("currency_form")) {
    views.add("currency_form", formView);
}
