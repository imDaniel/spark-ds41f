# Deployment repository CPU suite

Command, from this deployment worktree:

`/workspace/shared/q03-development/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`

These tests exercise deployment host logic, mocks and loopback fake services. Their synthetic throughput output is not model, GPU or cluster performance evidence.

- Attempt 01: 485 tests, 31 errors, all missing ignored test environment files (26 `config/nodes.json`, five `config/nodes-ring4.local.json`).
- Setup: copied tracked `config/nodes.example.json` and `config/examples/nodes-ring4.json` into those ignored local paths. No site credentials or actual node queries.
- Attempt 02: 485 tests, three failures: public ring example head address differs from the fixed historical servebench profiles' `distributed.master_addr`.
- Setup: adjusted only the ignored ring test map's head `management_ip` to the historical profile's existing `distributed.master_addr`. Production profiles, historical evidence and test assertions were not changed. This is a test fixture, not a verified live site map.
- Attempt 03: 485 tests in 180.064 seconds, OK; zero failures/errors/skips.

All attempts are retained. Tests ran serially. No GPU, remote node or production service operation was performed.

Exact cloud-only setup before attempts 02 and 03 (fresh worktree with no existing maps):

```sh
cp -n config/nodes.example.json config/nodes.json
cp -n config/examples/nodes-ring4.json config/nodes-ring4.local.json
python - <<'PY'
import json
from pathlib import Path
p = Path('config/nodes-ring4.local.json')
d = json.loads(p.read_text())
master = json.loads(Path('experiments/2026-10-14-servebench/engine-r6k.json').read_text())['distributed']['master_addr']
for node in d['nodes']:
    if node.get('head'):
        node['management_ip'] = master
p.write_text(json.dumps(d, indent=2) + '\n')
PY
```

Do not apply this synthetic setup over an existing real site map. The local verifier should use an isolated worktree and retain its own node configuration separately.
