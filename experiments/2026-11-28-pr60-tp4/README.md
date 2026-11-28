# TP4 qualification for the 1126 Attention maintenance candidate

This experiment qualifies the maintenance candidate from deployment commit
`932a3c13b95e5143614b91bbda37487f690635a2` with the RC3 component tree
`50402286dbe6d2f5975211809be8ecada25c8f69`. The launch overlay inherits
the candidate's source lock, official 1017 checkpoint, Attention operation,
GPU budgets and isolated cache, profile and checkout. The four-node TP4 run
tests the encoder, KV prefix isolation and CUDA graph corrections together.

The comparator reads both accepted engine starts of the 1126 Attention release.
Each candidate start runs the pinned Sonar client with the six historical load
cells and explicit scales 1 and 4. Compare throughput, first-output latency and
request tails against the variation on both sides. Neutral performance with
preserved functionality is acceptable for this correctness change. Keep
all attempts, account for external requests and retain individual stream tails.

## Window

On the head, use a published clean candidate checkout. Prepare and check the
locked input trees, the two profile digests and the four site checkouts. From
the experiment directory, run the offline window, gateway, native audit,
telemetry and comparison unit tests, then run `qualify.py inputs` with system
Python. From the Mac, run `gateway.py --catalog-only` with its existing local
test credential. Retain the production commit, image digest and both public
gateway aliases for rollback.

From Spark01, open a new UUID lease with `window.py open UUID`. Start
`window.py watchdog UUID` as an independent system service running as admin.
After its process identity and heartbeat are visible, start `run.py execute UUID`
as a separate system service under the same Unix account. The controller drains
ingress, coordinates the four-rank stop, builds and distributes one image, runs
the exact-image component gate, then tests targeted API behavior, the historical
functional suite, capacity and two performance boots. Phase logs and telemetry
live under `/srv/ai-cluster/operations/pr60-tp4-qualification`. Every window
uses a fresh result directory and distinct systemd unit names. Preserve the raw
files and verify their digests when collecting results.

Both completion and failure enter `window.py recover UUID`. This restores the
retained 1126 checkout, image and profile across all ranks, checks the real
backend, then opens ingress for the gateway test. Use the recovery challenge
with `gateway.py --window UUID --challenge NONCE` on the Mac. Stage its
credential-free JSON receipt as `gateway-UUID.json` in the operation directory
and run `window.py finalize UUID` on Spark01. Check the successful recovery
receipt and released hold before reporting service availability. Run the
two-boot comparison after the service is restored.
