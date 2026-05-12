"""Konstanten für KSEM Component"""

DOMAIN = "ksem"

# Dispatcher-Signal: wird vom WS-Task gesendet, wenn sich der Lademodus ändert.
# Format: SIGNAL_CHARGEMODE_UPDATE.format(entry_id)
SIGNAL_CHARGEMODE_UPDATE = "ksem_chargemode_update_{}"
