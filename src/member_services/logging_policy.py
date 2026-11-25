"""Disable raw transport diagnostics in command-line entry points."""

import logging


def suppress_transport_logs() -> None:
    # HTTPX logs full request URLs at INFO and HTTPcore can emit request details at DEBUG.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)
