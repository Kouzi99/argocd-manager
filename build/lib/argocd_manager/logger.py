import logging
from pathlib import Path

LOG_FILE = str(Path.home() / ".argocd_manager.log")


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    handlers = [logging.FileHandler(LOG_FILE)]
    if verbose:
        handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers,
    )
