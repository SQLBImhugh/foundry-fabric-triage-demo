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
    foundry_agent_model: str = "gpt-5.6-luna"
    # Which agent-to-agent handoff shape the runtime uses.
    # 'responses'  = invoke the other agent over /openai/v1/responses. Our code
    #                makes the call, so the PolicyLedger charges it. Verified.
    # 'a2a'        = Foundry's a2a_preview tool. Requires the callee to be a
    #                HOSTED agent speaking the 'a2a' protocol; a prompt agent
    #                publishes no agent card and the call fails at card fetch.
    foundry_handoff_mode: Literal["responses", "a2a"] = "responses"
    foundry_guardrail_name: str = "bi-triage-guardrail"

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
    # A mailbox the agent must NOT be able to read. `watch` probes it at startup
    # and refuses to run if the read succeeds, because that means the app
    # registration is not scoped and can read the whole tenant.
    graph_canary_mailbox: str = ""
    # Set once an Exchange ApplicationAccessPolicy scopes the app registration
    # to `graph_mailbox`. Preflight warns loudly while this is False, because an
    # unscoped app-only Mail.Read grant can read EVERY mailbox in the tenant.
    graph_mailbox_scoped: bool = False

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
