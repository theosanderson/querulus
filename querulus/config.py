"""Configuration loading and management"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_settings import BaseSettings


class ReferenceSequence(BaseModel):
    name: str
    sequence: str


class ReferenceGenome(BaseModel):
    nucleotideSequences: list[ReferenceSequence]
    genes: list[ReferenceSequence]

    def get_nucleotide_sequence(self, segment_name: str) -> str | None:
        """Get nucleotide reference sequence by segment name"""
        for seq in self.nucleotideSequences:
            if seq.name == segment_name:
                return seq.sequence
        return None

    def get_gene_sequence(self, gene_name: str) -> str | None:
        """Get amino acid reference sequence by gene name"""
        for gene in self.genes:
            if gene.name == gene_name:
                return gene.sequence
        return None


class OrganismConfig(BaseModel):
    referenceGenome: ReferenceGenome
    schema: dict[str, Any]  # Full schema config
    backend_config: dict[str, Any] | None = None  # Reference to backend config (will be set after loading)


class BackendConfig(BaseModel):
    organisms: dict[str, OrganismConfig]
    accessionPrefix: str
    websiteUrl: str
    backendUrl: str
    dataUseTerms: dict[str, Any] | None = None


class Settings(BaseSettings):
    """Application settings from environment variables"""

    database_url: str = "postgresql+asyncpg://postgres:unsecure@localhost:5432/loculus"
    database_pool_size: int = 20
    database_max_overflow: int = 10
    config_path: str = "config/querulus_config.json"

    # Database env vars (matching backend: DB_URL, DB_USERNAME, DB_PASSWORD)
    db_url: str | None = None
    db_username: str | None = None
    db_password: str | None = None

    model_config = {"case_sensitive": False, "extra": "ignore"}

    def __init__(self, **data) -> None:
        super().__init__(**data)

        # If DB_URL is provided (Kubernetes), convert from JDBC to asyncpg format
        if self.db_url:
            # Convert jdbc:postgresql://... to postgresql+asyncpg://...
            url = self.db_url.replace("jdbc:postgresql://", "postgresql+asyncpg://")
            # If username and password are separate, inject them
            if self.db_username and self.db_password:
                # Parse URL to inject credentials
                if "://" in url:
                    protocol, rest = url.split("://", 1)
                    if "@" not in rest:  # No credentials in URL
                        host_and_db = rest
                        url = f"{protocol}://{self.db_username}:{self.db_password}@{host_and_db}"
            self.database_url = url


class Config:
    """Global configuration singleton"""

    def __init__(self):
        self.settings = Settings()
        self.backend_config: BackendConfig | None = None

    def load_backend_config(self) -> None:
        """Load backend configuration with reference genomes"""
        config_path = Path(self.settings.config_path)

        if not config_path.exists():
            raise FileNotFoundError(
                f"Config file not found: {config_path}. "
                "Mount querulus-config ConfigMap in production."
            )

        with open(config_path) as f:
            config_data = json.load(f)

        self.backend_config = BackendConfig(**config_data)

        # Set backend_config reference in each OrganismConfig
        for organism_config in self.backend_config.organisms.values():
            organism_config.backend_config = {
                'dataUseTerms': config_data.get('dataUseTerms', {})
            }

    def get_organism_config(self, organism: str) -> OrganismConfig:
        """Get configuration for a specific organism"""
        if self.backend_config is None:
            raise RuntimeError("Backend config not loaded. Call load_backend_config() first.")

        if organism not in self.backend_config.organisms:
            available = ", ".join(self.backend_config.organisms.keys())
            raise ValueError(
                f"Organism '{organism}' not found in config. "
                f"Available organisms: {available}"
            )

        return self.backend_config.organisms[organism]


# Global config instance
config = Config()
