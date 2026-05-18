from . import models
from . import controllers


def _post_init_hook(env):
    """Activate VastPay provider after module installation."""
    pass


def _uninstall_hook(env):
    """Archive VastPay provider on module uninstall."""
    vastpay_provider = env['payment.provider'].search(
        [('code', '=', 'vastpay')]
    )
    if vastpay_provider:
        vastpay_provider.write({
            'state': 'disabled',
        })
