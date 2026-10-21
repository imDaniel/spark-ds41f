# Owner OOM default normalization

Base b8b6886b2a21d198b2be6f9a9fd6d8a35f8b1a55; tree
574cd6a85e0c1b1eb57c5f48c5223fa0b360eeb6. This is a new controller
experiment; old published checkouts, capsules and recorded hashes stay intact.

One variable: normalize a present null HostConfig.OomKillDisable to false in
held container projections. Four real inert/post-start pairs differed only in
this nullable default. Both leave OOM killing enabled. True, missing, numeric
and every unrelated field stay distinct; initial owner admission additionally
requires an explicit false or null value. Projection normalization copies the
host object and never alters raw inspect evidence. Engine, OCI digest, test
fixture, Docker argv and model configuration are unchanged.

The original failure reached four started candidates but produced no owner
result. Its shared run/cleanup config check refused and automatic recovery also
failed. A separately frozen compatibility recovery preserves the original
capsule and inert witnesses; this new controller must run only in a new window.

Validation: 336 related CPU tests pass, including all four ranks changing the
nullable default after start, full owner barriers and final drain. Real retained
pre/post inspections pass the full initial and running validators on all four
ranks with their original hashes; 32 non-alias mutations reject. Raw objects are
unchanged. Doctor and diff checks pass. Complete raw logs and commands remain
in private study results; no model correctness or performance result is claimed.
