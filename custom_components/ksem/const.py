"""Konstanten für KSEM Component"""

DOMAIN = "ksem"

# Dispatcher-Signal: wird gefeuert, wenn ein Zeitplan gesetzt oder gelöscht wird.
# Argumente: windows (list | None), readable (list | None)
SIGNAL_SCHEDULE_UPDATED = f"{DOMAIN}_schedule_updated"
