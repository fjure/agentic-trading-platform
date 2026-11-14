"""Read the explicit, private provider installation descriptor."""

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from packages.agent_research.provider_authority import ProviderBinding
from services.setup.llm_credentials import CredentialBinding

from .provider_http import Capability
from .provider_wire import Provider, _reject_nonfinite, _unique_fields

PROFILE_PATH = Path("/run/config/research-provider.json")
DISPATCH_KEY_PATH = Path("/run/secrets/research-provider-dispatch-key")
JOURNAL_PATH = Path("/run/research-dispatch/attempts.sqlite")


@dataclass(frozen=True, slots=True)
class ProviderInstallation:
    binding: ProviderBinding
    capability: Capability
    discovery: dict | None = None

    def accepts_credential(self, candidate: CredentialBinding, version: int, digest: str) -> bool:
        binding = self.binding
        return (
            candidate.provider.value, candidate.profile_id, candidate.profile_generation,
            candidate.model_generation, candidate.credential_generation, version, digest,
        ) == (
            binding.provider, binding.profile_id, binding.profile_generation,
            binding.model_generation, binding.credential_generation,
            binding.credential_version, binding.credential_digest,
        )


def load_installation(path: Path = PROFILE_PATH) -> ProviderInstallation | None:
    try:
        if path.is_symlink():
            raise ValueError
        with path.open("rb") as source:
            raw = source.read(65537)
    except FileNotFoundError:
        return None
    try:
        if len(raw) > 65536:
            raise ValueError
        document = json.loads(
            raw, object_pairs_hook=_unique_fields, parse_constant=_reject_nonfinite,
        )
        required = {"binding", "instructions", "schema"}
        if type(document) is not dict or set(document) not in (required, required | {"discovery"}):
            raise ValueError
        binding = ProviderBinding.from_record(document["binding"])
        instructions = document["instructions"]
        if type(instructions) is not str or type(document["schema"]) is not dict:
            raise ValueError
        schema = json.dumps(
            document["schema"], ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        )
        if (
            sha256(instructions.encode()).hexdigest() != binding.prompt_sha256
            or sha256(schema.encode()).hexdigest() != binding.schema_sha256
            or binding.response_model_id != binding.model_id
            or binding.api_version != ("2026-10-21" if binding.provider == "ANTHROPIC" else "v1")
            or binding.max_response_bytes != 262144
            or not 1 <= binding.timeout_seconds <= 15
        ):
            raise ValueError
        capability = Capability(
            Provider(binding.provider), binding.model_id, binding.profile_id,
            binding.profile_generation, binding.model_generation, binding.credential_generation,
            binding.envelope_version, instructions, schema, binding.max_output_tokens,
            binding.timeout_seconds,
        )
    except (ValueError, TypeError, RecursionError):
        raise ValueError("PROVIDER_INSTALLATION_INVALID") from None
    return ProviderInstallation(binding, capability, document.get("discovery"))
