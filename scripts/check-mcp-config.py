"""Read-only effective MCP inventory using documented CLI overrides.

Does not register servers globally, authenticate, or start another Codex agent.
"""
import json
from pathlib import Path
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
config = tomllib.loads((ROOT / '.codex/config.toml').read_text(encoding='utf-8'))
args = ['codex', 'mcp', 'list', '--json']
for name, server in config['mcp_servers'].items():
    for key, value in server.items():
        if isinstance(value, dict):
            for nested, nested_value in value.items():
                if isinstance(nested_value, dict):
                    for field, setting in nested_value.items():
                        args.extend(['-c', f'mcp_servers.{name}.{key}.{nested}.{field}={json.dumps(setting)}'])
                else:
                    args.extend(['-c', f'mcp_servers.{name}.{key}.{nested}={json.dumps(nested_value)}'])
        else:
            args.extend(['-c', f'mcp_servers.{name}.{key}={json.dumps(value)}'])
result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True)
inventory = json.loads(result.stdout)
for item in inventory:
    if item['name'] in config['mcp_servers']:
        print(json.dumps(item, ensure_ascii=False))
