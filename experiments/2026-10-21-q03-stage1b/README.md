# Bounded Stage 1b token screen

Base: PR21 `4d3f478e` / tree `b4b6d918f8b0c6d40f808f1a330c5a72aa584749`.
One intended variable: additive, opt-in HTTP schema 3 completes the preregistered
c1/c8/c16 × unloaded/loaded-zero/active1 16-token screen using the same real
corpus, pinned PR8 generation and normal HTTP lifecycle.

See [the execution contract](../../docs/q03-quality-matrix.md). Schema 2 code
and frozen historical references are preserved. This is not Stage 2 performance,
full quality, capacity, a GPU/tokenizer run or a promotion. Actual Stage 1a reuse
is pending until its full passing PR21 evidence exists. Other actual source
versions are explicitly unsupported rather than silently accepted.

Standalone CPU preparation must use the separately frozen PR25 successor
`fb4d6e8e8e6ec81a767c1163e4bab4385fbd1266`, not the uncorrected exporter inherited
from this base. Its three-file compatibility delta is intentionally not mixed
into this reviewed quality-controller change.

CPU checks on the source candidate:

- 16 new finite inventory/short-circuit/source-loader/configuration-binding tests.
- 16 unchanged schema-2 qualification tests.
- 11 normal HTTP, 31 HTTP fault/source-closure, 27 held-window, and 18 Git/source
  inventory regressions. Total: 119 focused tests passed together in one interpreter (5.084s).
- The first held-window-only command lacked the existing scripts PYTHONPATH;
  the first Git-toolchain-only command lacked one sparse fixture. Both original
  failed logs were retained. Restoring the ordinary import path and that unchanged
  fixture yielded complete passing reruns; no test or guard was weakened.
- bin/spark doctor was attempted and blocked by missing ignored site map
  config/nodes-ring4.local.json. No site-private input was fabricated, copied,
  or read from a device. No upstream patch changed, replay or image build ran.
- Changed Python files compile and git diff --check passes.

Independent final review passed 29/29 additional CPU checks: nine grouped-client,
15 historical-chain and five full six-attempt aggregate cases. The previously
accepting unequal effective-state probe now rejects at the pinned owner input.
The final reviewed matrix SHA-256 is
0192cef0b2cf1765824231998976708893b968c64c5f18a12cf3f3de90722959;
HTTP controller SHA-256 is
ad05312029cde03e186cb962c8cc0a6b2258e91bf59a8cf252d07363f19e2655.
Historical-source tests hash the genuine archived PR21 source bytes and use
explicit synthetic model artifacts/ID records, not field results.

Hosted exact-head CI remains pending publication. No local full-repository suite
or build replay is claimed for this sparse, source-only candidate.
