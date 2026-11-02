import logging

_logger = logging.getLogger(__name__)


def _has_column(cr, table, column):
    cr.execute(
        "SELECT 1 FROM information_schema.columns"
        " WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return bool(cr.rowcount)


def migrate(cr, version):
    """Carry the 19.0 settings over to the 20.0 fields.

    19.0 kept the environment in ``payment_provider.state`` (enabled / test /
    staging / disabled) and linked the POS method through
    ``use_payment_terminal = 'vastpay'``. 20.0 uses ``is_live`` / ``active`` /
    ``vastpay_staging`` and ``payment_method_type`` / ``payment_provider``.
    The old columns are only read if the database upgrade left them in place.
    """
    if _has_column(cr, 'payment_provider', 'state'):
        cr.execute("""
            UPDATE payment_provider
               SET is_live = (state = 'enabled'),
                   active = (state != 'disabled'),
                   vastpay_staging = (state = 'staging')
             WHERE code = 'vastpay'
         RETURNING id, state
        """)
        _logger.info("VastPay: migrated provider environment %s", cr.fetchall())
    else:
        _logger.warning(
            "VastPay: payment_provider.state is gone, cannot tell Test from"
            " Staging. Check 'Use VastPay Staging' on the provider by hand."
        )

    where = "payment_provider = 'vastpay'"
    if _has_column(cr, 'pos_payment_method', 'use_payment_terminal'):
        where += " OR use_payment_terminal = 'vastpay'"
    cr.execute(f"""
        UPDATE pos_payment_method
           SET payment_method_type = 'external_qr',
               payment_provider = 'vastpay'
         WHERE {where}
     RETURNING id
    """)
    _logger.info("VastPay: migrated POS payment methods %s", cr.fetchall())
