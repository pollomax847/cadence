#!/usr/bin/env python3
import json
from pathlib import Path

a = json.loads(Path('/tmp/mojito_matched.json').read_text())
b = json.loads(Path('/tmp/mojito_matched_autofill.json').read_text())
set_ids = set(a.get('matched_ids',[])) | set(b.get('matched_ids',[]))
ids = sorted(set_ids)
out = {'union_count': len(ids), 'ids': ids}
Path('/tmp/mojito_ids_union.json').write_text(json.dumps(out, indent=2))
print('Wrote /tmp/mojito_ids_union.json')
