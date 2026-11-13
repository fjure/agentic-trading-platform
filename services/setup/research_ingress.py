"""Constants shared by the separate setup service and its streaming ingress."""

import re

MAX_BODY = 8192
PRIVATE_SETUP_PATH = "/api/v1/integrations/research-setup/providers"
SETUP_ROUTE = re.compile(
    PRIVATE_SETUP_PATH.encode("ascii") + rb"/(?:OPENAI|ANTHROPIC)(?:/(rotate|revoke))?"
)
