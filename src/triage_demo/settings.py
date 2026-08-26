"""Runtime configuration.

Every value has a default that keeps the demo runnable offline. Nothing here
is required to execute both scenarios end-to-end with mock tools.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderMode = Literal["mock", "direct", "foundry"]
ToolMode = Literal["mock", "live"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Execution mode ----------------------------------------------------
    triage_provider_mode: ProviderMode = "mock"
    triage_tool_mode: ToolMode = "mock"

    # --- Foundry -----------------------------------------------------------
    foundry_project_endpoint: str = ""
    foundry_triage_agent_name: str = "bi-triage"
    foundry_dq_agent_name: str = "bi-data-quality"
    # Which agent-to-agent handoff shape the runtime uses. See
    # docs/architecture.md - `connected` requires the Data Quality agent's tools
    # to be server-callable, which the demo's local CSV scan is not.
    foundry_handoff_mode: Literal["client", "connected"] = "client"

    # --- Azure OpenAI (direct mode) ---------------------------------------
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = "gpt-4o"
    azure_openai_api_version: str = "2024-10-21"

    # --- Graph -------------------------------------------------------------
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = Field(default="", repr=False)
    graph_mailbox: str = "bi-alerts@contoso.com"
    graph_ingestion_mode: Literal["poll", "subscription"] = "poll"
    graph_poll_seconds: int = 30

    # --- Power BI ----------------------------------------------------------
    powerbi_tenant_id: str = ""
    powerbi_client_id: str = ""
    powerbi_client_secret: str = Field(default="", repr=False)
    powerbi_workspace_id: str = ""
    powerbi_dataset_id: str = ""

    # --- Teams -------------------------------------------------------------
    teams_webhook_url: str = Field(default="", repr=False)
    teams_mode: Literal["webhook", "graph"] = "webhook"

    # --- Observability -----------------------------------------------------
    applicationinsights_connection_string: str = Field(default="", repr=False)

    # --- Policy ------------------------------------------------------------
    # Shared across every agent in a run, not per agent. See TriagePolicy.
    triage_max_llm_turns: int = 14
    triage_max_tool_calls: int = 20
    triage_max_write_actions: int = 1
    triage_max_tokens: int = 80_000
    triage_timeout_seconds: int = 300

    app_version: str = "0.1.0"


settings = Settings()
