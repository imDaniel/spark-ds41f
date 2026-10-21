# Optional allocator observation source

Status: source-only CPU preparation, based on `3a49389f7ba0e93f05bab1c9a52b4425d4dd7fde`.
Not installed, not admitted, not a new current-window helper, and not capacity or
performance evidence. No current HTTP/owner plan, source inventory, capsule,
workload selector, runtime configuration, threshold or parser is changed.

## Narrow interface

`scripts/q03_allocator_observation.py` defines the optional
`q03_allocator_observation.AllocatorObservation` extension. It combines the exact
source-verified existing `HttpObservation` class with one new no-argument named
RPC, `q03_capacity_allocator_snapshot`. The inherited `q03_http_observe` method
is unchanged. The current plan still selects `q03_http_worker.HttpObservation`.
Merely adding this file to a checkout does not activate it.

The optional script bootstrap follows the existing normal CLI/spawn entry path
and compiles verified source bytes instead of trusting an import-path shadow,
preloaded module or stale bytecode. It requires an independently frozen
`Q03_ALLOCATOR_OBSERVER_SHA256`, the existing `Q03_HTTP_WORKER_SHA256`, and
`VLLM_ALLOW_INSECURE_SERIALIZATION=0`. The HTTP dependency is pinned to
`3b1a358ad47051dd3bed3628d6d34d8433241de6271f764a522026534ba381cf`.
This is an integration seam, not a change to the existing bootstrap or launcher.

Before allocator access the new method verifies its own and HTTP-source bytes,
the existing fixed runtime-manifest hash, the raw HTTP spec hash and its
window/attempt handshake against the existing environment, then the actual
`torch.__version__` against the frozen manifest. A mismatch fails before reading
allocator statistics. These checks establish only those specific identities:
the full existing all-rank readiness, actual native-library/package checks,
effective resources, image/container ownership and protected inputs remain
separate retained evidence. The new RPC does not repeat model/owner inspection.

It reads `torch.accelerator.memory_stats(self.device)` once, and the public
allocator-backend name if available. Device and rank come from the worker,
not caller-supplied arguments. No tensors, model access, synchronizations,
counter resets, cache release, garbage collection, subprocesses, shell, scheduler
or arbitrary dispatch are added. Imports alone never load torch or vLLM.

## Exact scope of the returned values

Only six fields are projected: allocated/reserved current and peak bytes,
`num_alloc_retries`, and `num_ooms`. Actual integer zero is retained; missing,
invalid, unavailable, or backend-inapplicable counters are `UNKNOWN` with null
values, never fabricated zero. Native dict subclasses, including OrderedDict,
are supported. For an unverified/non-native allocator backend, an available
integer is retained only as `raw_reported_value`, not an interpreted counter.
Native197's memory helper is not reused because it defaults missing counters to
zero.

These are worker-process allocator lifetime snapshots, not phase deltas or an
all-source health statement. A positive pre-workload lifetime total alone is not
proof of a workload failure. No delta calculation is implemented here.

The fixed reads of `/proc/self/stat`, kernel boot ID, PID namespace and time
namespace retain worker generation and local monotonic-clock provenance. PID
namespace disambiguates identical container-local PID/start-tick pairs on the
same boot. Process generation is raw boot/namespace/PID/start-tick identity, not
a fabricated engine UUID or a count of every possible restart. Raw proc bytes
are retained losslessly as base64 with byte length and SHA-256. Missing process
or clock identity makes the observation inconclusive.

The local bracket uses actual `time.monotonic_ns()` calls; no float-second
rescaling or invented point timestamp is used. Worker clocks remain distinct
from the head clock.

## CPU raw-response envelope, not a transport implementation

`head_response_envelope` takes actual raw request/response bytes and the caller's
head send/receive monotonic-nanosecond bounds. It performs no HTTP request and
cannot verify that the supplied values came from a live endpoint or clock.
The exact bytes, including whitespace, invalid UTF-8, malformed JSON and HTTP
error bodies, are retained as base64/length/SHA-256. It does not substitute a
canonicalized JSON rendering for the response. Failure bodies are inconclusive.

The envelope supports only the fixed method, empty arguments and the existing
15-second RPC timeout ceiling, further bounded by the remaining supplied
deadline. It rejects a reversed, noninteger or over-deadline head bracket. The
future owned transport must also retain its actual endpoint/attempt identity,
apply the existing HTTP timeout ceiling and remaining supervisor deadline, and
capture timeout/no-response outcomes as unknown. An HTTP timeout does not prove
remote work stopped. Existing all-rank cleanup and epoch/descendant sealing
remain required before reuse.

For a four-result response it checks distinct ranks 0–3, local rank zero,
source/runtime/spec/attempt identity fields, observation-only scope and valid
worker-generation fields. Optional rank-ordered expected generations bind a
later fence to retained earlier generations. An initial fence has no earlier
generation pin and explicitly reports `UNKNOWN`. The envelope retains the exact
expected source and worker-generation pins, not just a successful comparison
label. A changed generation cannot
be used for a same-worker counter comparison. Report order need not equal rank.

This is identity-field checking, NOT complete worker-payload validation, actual
runtime/endpoint attestation, or proof that the HTTP transport synchronously
returned those workers. The result explicitly says
`UNPROVEN_BY_THIS_CPU_ENVELOPE`. The head bracket is only a candidate containing
interval until that independent transport/request-response binding is proven;
response-receipt time is never assigned as the measurement point. The CPU seam
cannot certify a clock origin supplied by its caller. This distinction also
applies when every expected identity field matches.

## What stays unknown

Every receipt remains `capacity_status=INCONCLUSIVE` and `field_ready=false`.
Sampled allocator values do not provide continuous health, sampled guard
coverage/minimum, host `MemAvailable`, swap traffic, all-source error counts,
GPU drain, daemon quiescence, full-context residency or full capacity. The
explicit unknown inventory is diagnostic, not an additional protocol gate.

No periodic measurement RPC, workload, threshold, cadence or full-capacity
projection is introduced. The frozen capacity parser's point-timestamp schema
is not silently populated from these remote brackets. Before/after snapshots,
any transport wiring, and any projection require their separate reviewed freeze
and admitted scope. Nothing here authorizes target-device execution.

## CPU checks

Run from the checkout:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p test_q03_allocator_observation.py -v
```

The fixtures supply fake torch, proc and clock providers. They cover exact fixed
calls, inherited readiness, source-safe bootstrap, source/runtime/spec/version
failures before allocator access, absent APIs/counters, OrderedDict, non-native
backend zeros, bad identities, byte-for-byte failed response retention,
malformed/duplicate JSON, timeout/deadline boundaries and changed/duplicate ranks.
These tests do not prove the installed torch API, actual native transport,
source mounting, runtime overhead, live all-rank behavior or target acceptance.
