"""Opt-in bootstrap for supplementary tests on this worker's Python 3.14.

Only loaded when this directory is explicitly set as PYTHONPATH. Installed
system dependencies take precedence over the existing Python 3.11 environment.
The local tests namespace takes precedence over an unrelated installed package.
This is verification infrastructure, not an application runtime configuration.
"""

import importlib.machinery
from pathlib import Path
import sys
import types


root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
sys.path.append('/nvmetank1/projects/speakr/venv/lib/python3.11/site-packages')
tests = types.ModuleType('tests')
tests.__path__ = [str(root / 'tests')]
tests.__spec__ = importlib.machinery.ModuleSpec('tests', loader=None, is_package=True)
tests.__spec__.submodule_search_locations = tests.__path__
sys.modules.setdefault('tests', tests)
