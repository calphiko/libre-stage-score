import os
from pathlib import Path


def data_root() -> Path:
    configured = os.getenv("LIBRE_STAGE_DATA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parent.parent
