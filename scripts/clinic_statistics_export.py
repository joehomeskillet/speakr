#!/usr/bin/env python3
"""Export scoped statistics using CLINIC_MODE and SQLALCHEMY_DATABASE_URI."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.clinic.statistics_export import main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
