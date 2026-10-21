# Bind closure markers to immutable disposition

Base 2b361c27838bf6b6f43264885d3d6631fed0231e is preserved. Cloud review
identified unconditional acceptance of restored=true in a keep-stopped capsule.
The shared closure validator now rejects contradictory terminal markers before
watchdog completion, recovery or cleanup effects. A legacy restore capsule
also rejects a stopped terminal marker. No native engine, image or fixture change.

Added fault injection verifies contradictory bool/int/phase markers cannot cause
START, recovery launch or terminal success. Cloud CPU/integration review pending;
local AST/diff checks only while original services run. No field execution of2b.
