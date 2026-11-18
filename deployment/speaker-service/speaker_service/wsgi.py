"""Gunicorn entry point: loads the real sherpa-onnx models once at start."""

from .app import create_app
from .engine import EngineConfig, SherpaEngine

app = create_app(lambda: SherpaEngine(EngineConfig.from_env()))
