# Frozen Mac source delivery (opt-in)

This adapter prepares a **new independent four-node site/session** without private-origin Git authentication on the Sparks. It does not alter the default online `cluster sync`/publication contract, deploy an image, change repository visibility, or upgrade an active recovery capsule. Native197/test-source bindings, including their previously approved shallow history, are unchanged. OCI preparation/import is a separate existing build/artifact stage; using this source mode does not itself require rebuilding an already accepted image.

## Authority and evidence

The existing authenticated Mac-to-operator/site handoff of the complete frozen manifest is the authority. A bundle and adjacent receipt do **not** authenticate themselves. Do not accept a replacement manifest merely because its own adjacent files contain matching hashes. There is no new signing key, credential, token, or persistent access grant.

On the already authenticated Mac, preparation observes exactly the allowed fork URL and `refs/heads/...` advertised SHA, then exports a v2 bundle containing only `HEAD` and the complete reachable object closure. Verification uses empty, template-free object storage, not objects already present on a receiver. The receipt records the exact observation, UTC time, commit, computed tree, bundle bytes/hash, and a digest of the planned manifest. This is evidence of publication **at preparation time**, not a claim that GitHub still advertises the same tip when a Spark later runs.

A fresh `preparation_id` and the exact planned `session_dir` bind the receipt to one new immutable site/session. The manifest digest omits only the known publication slots' receipt hashes and the three existing runtime-assigned preflight fields `window_id`, `holder`, and `glm_identity`. The actual session path must match, and normal session creation remains exclusive. Do not recycle or rewrite an old session/capsule.

No `.git/config`, keychain contents, deploy keys, tokens, or credential-helper data are exported. The Spark path makes no private-origin/network Git call. This is not an audit or deletion of pre-existing machine credentials.

## Preparation inputs

Keep the existing manifest's URL/ref/SHA, original and protected roots, four-node inventory receipts, build/artifact bindings, and other preflight guards. A GitHub branch name such as `freeze/example` is not the publication ref: `publication.ref` must be `refs/heads/freeze/example`. The producer rejects a short branch name before creating output or running Git. Add:

- Top-level `source_delivery`: `mode: "mac-git-bundle-v1"`, a new 32-lowercase-hex `preparation_id`, exact future Spark `session_dir`, and `sync`.
- `sync`: selected repository-relative `cluster_config`, its `cluster_config_sha256`, the exact selected `shape` (or null), and `site_files`, a map of the actual copied relative nodes/site paths to their SHA-256 hashes. These freeze the selected profile and site map for the source update; changing that configuration is a separate site preparation, not an implicit effect of source sync.
- A `source_bundle` on each existing publication used by this manifest: controller `preflight.publication`, optional candidate `build_publication`, and optional G1 artifact `publication`. Its exact fields are `tree`, future Spark `checkout`, local `git_config_sha256`, all-four `node_git_config_sha256` map, `bundle_path`, `bundle_sha256`, `receipt_path`, and `receipt_sha256`.

Bundle/receipt hashes may be 64 zeroes in the preparation input; preparation fills them before producing the final manifest. Other pins must already be exact. Target paths are lexical on the Mac, then canonical/no-alias/protected-root checked on the actual Sparks. Evidence must be under the independent root, outside source checkouts, session and writable candidate/build outputs. Bundle and receipt paths must be different. Each consuming checkout keeps its own pinned safe configuration and exact original origin URL; Mac Git configuration is never copied.

Provide a JSON map of the selected publication names (`controller`, plus `build`/`g1` only when present) to the corresponding clean independent Mac checkout paths. Each must already be at its publication SHA/tree and have complete self-contained Git objects. Mac-local credential helpers remain local and may serve the exact authenticated observation. Transport rewrites and disabled TLS verification are refused rather than silently observing a different origin.

Future invocation on the authorized Mac:

```sh
python3 scripts/q03_source_delivery.py prepare \
  --manifest planned-manifest.json --checkouts mac-checkouts.json \
  --output /absolute/new/source-export
```

The new output directory must be owned, safe, and disjoint from all source inputs. It contains `prepared-manifest.json` plus each selected `<name>.bundle` and `<name>.receipt.json`. Deliver the final manifest through the existing authenticated handoff. Place its controller bundle/receipt at the frozen head-node paths; place any additional selected publication evidence at the already-bound consuming paths. No generic archive extraction, credential setup, or arbitrary Git-import interface is introduced.

## Existing four-node sync integration

From the independent head checkout, select the same frozen profile/site map:

```sh
bin/spark --cluster-config PATH cluster sync \
  --source-manifest /absolute/prepared-manifest.json --apply
```

Without `--apply`, this only prints a plan and performs local evidence/input checks. With the explicit source manifest, the existing sync loop accepts the head's clean old commit, verifies all four expected inventory hostnames, origins, configuration pins, clean tracked bytes, rank/head mapping and safe site destinations **before any checkout**. It stages/verifies the same controller bundle on all four nodes, without fetching the origin. Helper bytes are frozen before the first mutation, including for rollback.

The existing checkout, site-file copy and repository doctor sequence remains in force. Bundle-mode site writes use pinned bytes and checked regular destinations, never shell redirection through a symlink. Final success requires all four commit/tree/profile/site identities. A checkout that succeeds but fails its post-check is included in the existing source rollback attempt. Unknown/dirty state is reported as rollback failure, not falsely marked restored. This is source rollback, not a new transactional rollback of all ignored site files or other external state.

The prepared profile bytes must be unchanged through this operation. A target commit changing the selected tracked profile is deliberately not treated as a transparent source-only update; prepare that changed site explicitly instead of reusing this site's receipt. The source sync does not add an image-rebuild requirement or bypass existing artifact identity checks.

## Verification scope

`tests/test_q03_source_delivery.py` contains bounded CPU-only real-Git fixtures for receipt/bundle integrity, empty-store closure, Mac-observation generation (platform/network observation mocked), metadata/alias/environment failures, old-head admission, four-node ordering, post-checkout failure rollback, and the actual controller/watchdog source-set compatibility. Run with:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests \
  -p test_q03_source_delivery.py
```

`Q03_SOURCE_TEST_BASE_ROOT` is only an optional dependency path for a sparse local source overlay; a normal complete checkout needs no override. Passing these CPU fixtures does not demonstrate real Mac authentication, Apple Container execution, Spark SSH/Git behavior, or field recovery. Those require the separately authorized preparation/validation window. Existing active capsules and the separate recovery-compatibility path are not modified by this adapter.
