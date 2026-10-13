"""Static validation of official/generated and project skill metadata."""
from pathlib import Path
import yaml

root = Path(__file__).resolve().parents[1]
names = set()
paths = sorted((root / '.agents/skills').glob('*/SKILL.md'))
assert len(paths) == 11, f'Expected six OpenSpec and five project skills; got {len(paths)}'
for path in paths:
    text = path.read_text(encoding='utf-8')
    assert text.startswith('---\n'), f'Missing frontmatter: {path}'
    metadata = yaml.safe_load(text.split('---', 2)[1])
    name = metadata.get('name')
    assert isinstance(name, str) and name == path.parent.name, path
    assert isinstance(metadata.get('description'), str) and metadata['description'].strip(), path
    assert name not in names, name
    names.add(name)
    print(f'PASS {name}')
print(f'Validated {len(paths)} skills: YAML, required metadata, folder names and uniqueness.')
