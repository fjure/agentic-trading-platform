"""Closed v1 research registry. Provider activation is a separate policy decision."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType


class ToolId(StrEnum):
    RESEARCH_READ = "RESEARCH_READ"
    QUANT_READ = "QUANT_READ"
    IDEA_MANAGE = "IDEA_MANAGE"
    QUANT_PREPARE = "QUANT_PREPARE"


class AgentPolicyDenied(ValueError):
    """An unregistered, stale, or revoked authority was requested."""

    code = "AGENT_POLICY_DENIED"


@dataclass(frozen=True, slots=True)
class ToolVersion:
    tool_id: ToolId
    version: int


@dataclass(frozen=True, slots=True)
class AgentLimits:
    max_input_tokens: int
    max_output_tokens: int
    max_cost: Decimal
    currency: str
    max_model_calls: int
    max_steps: int
    max_tool_calls: int
    max_experiments: int
    max_parallelism: int
    max_active_seconds: int
    max_wall_seconds: int


@dataclass(frozen=True, slots=True)
class ModelBinding:
    provider: str
    profile_id: str
    profile_generation: int
    model_id: str
    model_generation: int


@dataclass(frozen=True, slots=True)
class AgentVersion:
    agent_id: str
    version: int
    policy_version: int
    role: str
    description: str
    prompt_version: int
    model_binding: ModelBinding | None
    tools: tuple[ToolVersion, ...]
    output_schema_version: int
    limits: AgentLimits
    autonomy_level: int
    abstention_rules: tuple[str, ...]
    implementation_fingerprint: str

    def require_tool(self, tool_id: str, version: int) -> ToolVersion:
        for tool in self.tools:
            if tool.tool_id == tool_id and type(version) is int and tool.version == version:
                return tool
        raise AgentPolicyDenied(AgentPolicyDenied.code)

    def require_model(self, binding: ModelBinding) -> ModelBinding:
        if self.model_binding is None or self.model_binding != binding:
            raise AgentPolicyDenied(AgentPolicyDenied.code)
        return self.model_binding


QUANT_RESEARCH_V1 = AgentVersion(
    agent_id="quant-research-agent-v1",
    version=1,
    policy_version=1,
    role="QUANT_RESEARCH",
    description="Begrenzte, betreibergebundene quantitative Forschung.",
    prompt_version=1,
    model_binding=None,
    tools=tuple(ToolVersion(tool, 1) for tool in ToolId),
    output_schema_version=1,
    limits=AgentLimits(
        max_input_tokens=4096,
        max_output_tokens=1024,
        max_cost=Decimal("0.10"),
        currency="USD",
        max_model_calls=8,
        max_steps=24,
        max_tool_calls=16,
        max_experiments=1,
        max_parallelism=1,
        max_active_seconds=600,
        max_wall_seconds=3600,
    ),
    autonomy_level=0,
    abstention_rules=("MISSING_EVIDENCE", "UNSUPPORTED_HYPOTHESIS", "POLICY_DENIED"),
    implementation_fingerprint=sha256(
        Path(__file__).read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest(),
)

AGENTS = MappingProxyType({
    (QUANT_RESEARCH_V1.agent_id, QUANT_RESEARCH_V1.version): QUANT_RESEARCH_V1,
})


def require_agent(agent_id: str, version: int, policy_version: int) -> AgentVersion:
    if type(version) is not int or type(policy_version) is not int:
        raise AgentPolicyDenied(AgentPolicyDenied.code)
    agent = AGENTS.get((agent_id, version))
    if agent is None or agent.policy_version != policy_version:
        raise AgentPolicyDenied(AgentPolicyDenied.code)
    return agent
