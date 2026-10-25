"""Config-driven, read-only SAP OData connector."""
from .client import SapClient, SapConnectionError, SapHttpError
from .config import Config, ConfigError
from .metadata import describe_service, parse_metadata, save_metadata
from .query import query_entity_set

__all__ = [
    "Config", "ConfigError", "SapClient", "SapConnectionError", "SapHttpError",
    "describe_service", "parse_metadata", "save_metadata", "query_entity_set",
]
