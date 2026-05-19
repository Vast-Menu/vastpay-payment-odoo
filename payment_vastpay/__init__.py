from . import models
from . import controllers


def _post_init_hook(env):
    """Create the VastPay POS payment method on install.

    Without this the cashier has no VastPay option until a payment method
    is created by hand. We create one (idempotently), link it to the
    VastPay provider, and add it to the existing Points of Sale of the
    same company. New POS configs pick it up automatically because it is a
    non-cash, non-split method (see pos.config._default_payment_methods).
    """
    provider = env['payment.provider'].search(
        [('code', '=', 'vastpay')], limit=1,
    )
    # A bank journal so VastPay payments post to accounting like any other
    # electronic POS method (mirrors how core creates the "Card" method).
    bank_journal = env['account.journal'].search(
        [('type', '=', 'bank'), ('company_id', '=', env.company.id)],
        limit=1,
    )
    PaymentMethod = env['pos.payment.method']
    method = PaymentMethod.search(
        [('use_payment_terminal', '=', 'vastpay')], limit=1,
    )
    if not method:
        method = PaymentMethod.create({
            'name': 'VastPay',
            'payment_method_type': 'terminal',
            'use_payment_terminal': 'vastpay',
            'company_id': env.company.id,
            'journal_id': bank_journal.id if bank_journal else False,
            'vastpay_provider_id': provider.id if provider else False,
        })
    elif not method.journal_id and bank_journal:
        method.journal_id = bank_journal.id

    configs = env['pos.config'].search(
        [('company_id', '=', method.company_id.id)],
    )
    for config in configs:
        if method not in config.payment_method_ids:
            config.payment_method_ids = [(4, method.id)]


def _uninstall_hook(env):
    """Archive VastPay provider on module uninstall."""
    vastpay_provider = env['payment.provider'].search(
        [('code', '=', 'vastpay')]
    )
    if vastpay_provider:
        vastpay_provider.write({
            'state': 'disabled',
        })
