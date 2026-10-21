# Native-safe owner RPC repair

One variable: replace the full-model fixture's callable RPC transports with named worker-extension methods supported by the unchanged native serializer policy. This is a separate immutable controller/test candidate based on 7403bc6014e959af6e4087b48e05f43e3ccb59e6. It does not advance any currently deployed ref or change an active capsule.

## Exact identities and patch preparation

Use source.json for runtime, image, fixture, test tree and patch identities. Apply this directory's single net0500 mailbox directly after exact native197 (154159dc3e38ca8b2321c01a153a1b2ccaf8663b) in a new independent test checkout. Do not append it after the old owner0500 or G1/retirement0500 namespaces. The one changed component path is the owner test. Full-index sparse one-patch replay reproduced test head141f8647e885812095060ff9e7f8b991316ae538/tree0990e860d8c3c621f2889176cd38ff1600600646; this is not a fresh replay of the inherited197 historical series.

Runtime197/treeae15047 and image90bd remain unchanged and must not be relabeled with the new test identity. No native rebuild, image distribution, repeated P0/G1/G2, merge or promotion is requested.

## Transport and all-rank import contract

The previous fixture sent Python functions through collective_rpc and apply_model, which the actual default MsgpackEncoder rejects before worker dispatch. All three transports now use strings:

- q03_check_runtime(runtime_manifest, fixture_sha256), timeout120 as before.
- q03_owner_snapshot(), default timeoutNone as the original apply_model.
- q03_observe_graph_resources(), timeout120 as before, still dual-only.

The named extension delegates to the unchanged observation functions. Runtime/config/steering/owner/retention assertions, generation batches, capacity and off/dual flow are unchanged. VLLM_ALLOW_INSECURE_SERIALIZATION is not enabled; bootstrap refuses an enabled value.

The controller puts the exact worker_extension_cls in the common engine kwargs and each follower CLI. Leader and all followers share one fixed bootstrap. It verifies the existing baked PYTHONPATH, pinned fixture and tblib bytes, exposes only /validation/tests (not a vLLM source overlay), verifies the imported fixture origin, and runs either pytest or the native CLI. Spawned workers inherit the tests package path and fixed environment.

PYTHONDONTWRITEBYTECODE remains1. A new fixed PYTHONPYCACHEPREFIX=/cache/owner-bytecode points at a fresh absent leaf in each new per-rank cache before fixture import; this avoids consuming ignored source-directory bytecode. It is not a new runtime/model PYTHONPATH or a cache shared with originals. Keep the per-attempt cache fresh and do not replace old loaded sources.

## Validation and next field check

Focused controller command:

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. python3 -m unittest -v test_q03_owner_native_rpc test_q03_owner_model test_q03_owner_oom_default test_q03_image_preparation

67 focused methods passed. Separate fixture checks cover actual pytest import, four multiprocessing-spawn children, unchanged helper/main AST semantics, exact extension rejection and exact-base replay. Native serializer probes use real msgspec and the unchanged exact197 encoder path under bounded CPU type protocols; no full vLLM/Torch module or GPU import is implied. Preserve the receipts and negative controls separately.

Minimum field smoke is the existing first off/off owner attempt on a new pinned controller/capsule and new test tree. It must report all four exact runtime/config/owner identities through named RPC before steady acknowledgment, then execute the unchanged requests and positive drain. Dual continues to exercise the named retention RPC. Any failure remains a failure for its actual phase; model initialization or a Python import is not owner, generation, quality, capacity or performance acceptance. Original services remain stopped.

Private site manifest must bind this controller's exact published ref/SHA, the new test head/tree/fixture and its actual .git/config hash. Existing native/runtime/tblib/profile/effective-config identities remain unchanged. No arbitrary extension, callable transport, unsafe serialization flag or permissive old/new fixture union is accepted.
