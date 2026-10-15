"""Central application settings for DependencyGuard.

Keeping settings in one place means later phases (Gemma 4 access,
sandbox limits, frontend origins) only need to be configured here.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = "DependencyGuard"
    version: str = "0.1.0"
    description: str = (
        "Scans Python repositories for risky dependencies, recommends "
        "replacements with Gemma 4, verifies them with tests, and produces "
        "a Dependency Exit Capsule."
    )


settings = Settings()
