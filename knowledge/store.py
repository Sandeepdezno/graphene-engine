"""Thin Neo4j wrapper: connection settings from .env, `run()` for Cypher, friendly errors."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
METADATA_DIR = PROJECT_ROOT / "data" / "metadata"


class GraphError(Exception):
    """Neo4j is unreachable, rejected the credentials, or the configuration is incomplete."""


@dataclass(frozen=True)
class Neo4jConfig:
    uri: str
    user: str
    password: str = field(repr=False)

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Neo4jConfig":
        load_dotenv(env_file or PROJECT_ROOT / ".env")
        password = os.environ.get("NEO4J_PASSWORD", "")
        if not password:
            raise GraphError("NEO4J_PASSWORD is not set (add NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD to .env)")
        return cls(os.environ.get("NEO4J_URI", "bolt://localhost:7688"),
                   os.environ.get("NEO4J_USER", "neo4j"), password)


class GraphStore:
    def __init__(self, config: Neo4jConfig):
        self.config = config
        self._driver = GraphDatabase.driver(config.uri, auth=(config.user, config.password))

    def run(self, cypher: str, **params) -> list[dict]:
        try:
            with self._driver.session() as session:
                return [record.data() for record in session.run(cypher, **params)]
        except AuthError:
            raise GraphError("Neo4j rejected the credentials - check NEO4J_USER / NEO4J_PASSWORD") from None
        except ServiceUnavailable:
            raise GraphError(
                f"Cannot reach Neo4j at {self.config.uri}. Start it with 'docker compose up -d neo4j' "
                "and check NEO4J_URI.") from None
        except Neo4jError as exc:
            raise GraphError(f"Neo4j error: {exc.message}") from None

    def close(self) -> None:
        self._driver.close()
