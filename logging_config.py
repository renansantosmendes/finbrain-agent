"""Centralized logging setup for the FinBrain agent app."""
import logging
import sys


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("finbrain")
    if logger.handlers:
        return logger

    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = configure_logging()
