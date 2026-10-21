# Match stopped admission to verified stopped closure

Base 4aa155ccededd98c63bce87564f69e3d90cc4d1e is preserved. A stopped
registered container may retain a historical failure exit code. Explicit
keep-stopped entry now uses the same positive stopped proof as restore_drained:
all four registered identities valid, no identity/cleanup errors, every guard
inactive and all four GPUs empty. Unknown failed-state types, identity errors,
partial/mixed rank state and requests still reject. A running service still
requires full health, idle and GPU ownership. Historical failures are retained.

The legacy recovery-source test is corrected from obsolete count10 to the exact
22-file set and independently computed hashes; the actual source inventory is
unchanged. Cloud full relevant Q03 tests and integrated review are required.
Only static AST/diff checks ran locally while original services run. No field
execution of2b or4aa occurred. No engine, fixture, image or original asset change.
