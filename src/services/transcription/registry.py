"""
Connector registry for managing transcription connectors.

Provides factory pattern for creating and managing transcription connectors,
with auto-detection from environment variables for backwards compatibility.
"""

import os
import logging
from typing import Dict, Any, Optional, Type, List

from .base import BaseTranscriptionConnector, TranscriptionCapability, TranscriptionRequest, TranscriptionResponse
from .exceptions import ConfigurationError

logger = logging.getLogger(__name__)


class ConnectorRegistry:
    """
    Registry for managing transcription connectors.

    Singleton pattern - use get_registry() to get the shared instance.
    """

    _instance = None
    _connectors: Dict[str, Type[BaseTranscriptionConnector]] = {}
    _active_connector: Optional[BaseTranscriptionConnector] = None
    _connector_name: str = ""
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._register_builtin_connectors()
        self._initialized = True

    def _register_builtin_connectors(self):
        """Register all built-in connectors."""
        from .connectors.openai_whisper import OpenAIWhisperConnector
        from .connectors.asr_endpoint import ASREndpointConnector
        from .connectors.clinic_stub import ClinicStubTranscriptionConnector

        self.register('openai_whisper', OpenAIWhisperConnector)
        self.register('asr_endpoint', ASREndpointConnector)
        self.register('clinic_stub', ClinicStubTranscriptionConnector)

    def register(self, name: str, connector_class: Type[BaseTranscriptionConnector]):
        """
        Register a connector class.

        Args:
            name: Unique name for the connector
            connector_class: The connector class to register
        """
        self._connectors[name] = connector_class
        logger.debug(f"Registered transcription connector: {name}")

    def get_connector_class(self, name: str) -> Type[BaseTranscriptionConnector]:
        """
        Get a connector class by name.

        Args:
            name: The connector name

        Returns:
            The connector class

        Raises:
            ConfigurationError: If connector not found
        """
        if name not in self._connectors:
            raise ConfigurationError(
                f"Unknown connector: {name}. Available: {list(self._connectors.keys())}"
            )
        return self._connectors[name]

    def create_connector(self, name: str, config: Dict[str, Any]) -> BaseTranscriptionConnector:
        """
        Create a connector instance.

        Args:
            name: The connector name
            config: Configuration dict for the connector

        Returns:
            Configured connector instance
        """
        connector_class = self.get_connector_class(name)
        connector = connector_class(config)
        # The one factory: every connector is built here, so every path
        # (upload, reprocess, retry, chunked) gets the clinic GPU wrapper.
        from src.clinic.asr_fallback import wrap_with_gpu_fallback
        return wrap_with_gpu_fallback(connector)

    def list_connectors(self) -> List[Dict[str, Any]]:
        """
        List all registered connectors with their capabilities.

        Returns:
            List of connector info dicts
        """
        result = []
        for name, cls in self._connectors.items():
            result.append({
                'name': name,
                'provider_name': cls.PROVIDER_NAME,
                'capabilities': [c.name for c in cls.CAPABILITIES],
                'config_schema': cls.get_config_schema()
            })
        return result

    def initialize_from_env(self) -> BaseTranscriptionConnector:
        """
        Initialize the active connector from environment variables.

        Auto-detection priority:
        1. TRANSCRIPTION_CONNECTOR - explicit connector name (openai_whisper, asr_endpoint, clinic_stub)
        2. ASR_BASE_URL is set - use ASR endpoint (smarter detection)
           - USE_ASR_ENDPOINT=true also works (backwards compat, with deprecation warning)
        3. Default to OpenAI Whisper (whisper-1 or TRANSCRIPTION_MODEL)

        Returns:
            The initialized connector
        """
        connector_name = os.environ.get('TRANSCRIPTION_CONNECTOR', '').lower().strip()

        if not connector_name:
            asr_base_url = os.environ.get('ASR_BASE_URL', '').strip()
            use_asr_flag = os.environ.get('USE_ASR_ENDPOINT', 'false').lower() == 'true'
            transcription_model = os.environ.get('TRANSCRIPTION_MODEL', '').lower()
            whisper_model = os.environ.get('WHISPER_MODEL', '').lower()

            # Deprecation warning for legacy USE_ASR_ENDPOINT flag
            if use_asr_flag:
                logger.warning(
                    "USE_ASR_ENDPOINT=true is deprecated. "
                    "Set ASR_BASE_URL instead for auto-detection, or use TRANSCRIPTION_CONNECTOR=asr_endpoint"
                )

            # Priority 2: ASR endpoint - check ASR_BASE_URL or legacy flag
            if asr_base_url or use_asr_flag:
                connector_name = 'asr_endpoint'
                if asr_base_url:
                    logger.info("Auto-detected ASR endpoint from ASR_BASE_URL")
            # Priority 3: OpenAI Whisper (with custom or default model)
            else:
                connector_name = 'openai_whisper'
                model = transcription_model or whisper_model or 'whisper-1'
                logger.info(f"Using OpenAI Whisper connector with model: {model}")

        config = self._build_config_from_env(connector_name)

        try:
            self._active_connector = self.create_connector(connector_name, config)
            self._connector_name = connector_name

            logger.info(f"Initialized transcription connector: {connector_name}")
            logger.info(f"Capabilities: {[c.name for c in self._active_connector.get_capabilities()]}")

            return self._active_connector

        except Exception as e:
            logger.error(f"Failed to initialize connector '{connector_name}': {e}")
            raise ConfigurationError(f"Failed to initialize connector '{connector_name}': {e}") from e

    def _get_asr_timeout(self) -> int:
        """
        Get ASR timeout with fallback chain: ENV -> Admin UI -> default.

        Priority:
        1. ASR_TIMEOUT environment variable
        2. asr_timeout_seconds environment variable (legacy)
        3. SystemSetting from Admin UI (database)
        4. Default: 1800 seconds (30 minutes)
        """
        # Check environment variables first
        env_timeout = os.environ.get('ASR_TIMEOUT') or os.environ.get('asr_timeout_seconds')
        if env_timeout:
            return int(env_timeout)

        # Fall back to Admin UI setting (SystemSetting in database)
        try:
            from src.models import SystemSetting
            db_timeout = SystemSetting.get_setting('asr_timeout_seconds', None)
            if db_timeout is not None:
                return int(db_timeout)
        except Exception as e:
            # May fail if no app context or during initialization
            logger.debug(f"Could not read ASR timeout from database: {e}")

        # Default: 30 minutes
        return 1800

    def _build_config_from_env(self, connector_name: str) -> Dict[str, Any]:
        """
        Build connector config from environment variables.

        Args:
            connector_name: The connector to build config for

        Returns:
            Configuration dict
        """
        if connector_name == 'asr_endpoint':
            base_url = os.environ.get('ASR_BASE_URL', '')
            if base_url:
                base_url = base_url.split('#')[0].strip()

            return {
                'base_url': base_url,
                'api_key': os.environ.get('ASR_API_KEY', ''),
                'timeout': self._get_asr_timeout(),
                'diarize': os.environ.get('ASR_DIARIZE', 'true').lower() == 'true',
                'return_speaker_embeddings': os.environ.get('ASR_RETURN_SPEAKER_EMBEDDINGS', 'false').lower() == 'true'
            }

        elif connector_name == 'clinic_stub':
            return {
                'profile': os.environ.get('CLINIC_STUB_PROFILE', 'default'),
            }

        else:  # openai_whisper (default)
            base_url = os.environ.get('TRANSCRIPTION_BASE_URL', '')
            if base_url:
                base_url = base_url.split('#')[0].strip()

            # Support both TRANSCRIPTION_MODEL and legacy WHISPER_MODEL
            # TRANSCRIPTION_MODEL takes priority for custom Whisper variants
            model = os.environ.get('TRANSCRIPTION_MODEL', '') or os.environ.get('WHISPER_MODEL', 'whisper-1')

            config = {
                'api_key': os.environ.get('TRANSCRIPTION_API_KEY', ''),
                'base_url': base_url or None,
                'model': model
            }
            from src.clinic.config import clinic_mode_enabled
            if clinic_mode_enabled() and os.environ.get(
                    'TRANSCRIPTION_WORD_TIMESTAMPS', '').strip().lower() in ('1', 'true', 'yes'):
                config['word_timestamps'] = True
            return config

    def get_active_connector(self) -> BaseTranscriptionConnector:
        """
        Get the currently active connector.

        Initializes from environment if not already initialized.

        Returns:
            The active connector
        """
        if not self._active_connector:
            self.initialize_from_env()
        return self._active_connector

    def get_active_connector_name(self) -> str:
        """Get the name of the currently active connector."""
        if not self._active_connector:
            self.initialize_from_env()
        return self._connector_name

    def reinitialize(self) -> BaseTranscriptionConnector:
        """
        Force re-initialization of the connector.

        Useful when environment variables have changed.

        Returns:
            The newly initialized connector
        """
        self._active_connector = None
        self._connector_name = ""
        return self.initialize_from_env()


# Global registry instance
_registry: Optional[ConnectorRegistry] = None


def get_registry() -> ConnectorRegistry:
    """Get the global connector registry."""
    global _registry
    if _registry is None:
        _registry = ConnectorRegistry()
    return _registry


# Convenience aliases
connector_registry = get_registry()


def transcribe(request: TranscriptionRequest) -> TranscriptionResponse:
    """
    Transcribe audio using the active connector.

    This is a convenience function that uses the global registry.

    Args:
        request: The transcription request

    Returns:
        Transcription response
    """
    connector = get_registry().get_active_connector()
    return connector.transcribe(request)


def get_connector() -> BaseTranscriptionConnector:
    """Get the active transcription connector."""
    return get_registry().get_active_connector()


def supports_diarization() -> bool:
    """Check if the active connector supports diarization."""
    return get_registry().get_active_connector().supports_diarization
