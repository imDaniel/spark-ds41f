# Standalone U176 / S188 CPU runtime evidence

This source slice removes a capture/bootstrap dependency, not an admission gate.
It does not modify `q03_baseline_identity.py`, service/window controllers, profiles,
model requests or the five pending runtime/profile bindings. Those controllers
continue to reject incomplete freezes. The result is `captured-unreviewed` with
`runtime_qualification: false`, never authorization to run a service or benchmark.

## Frozen capture inputs

`scripts/q03_runtime_capture.py` is a standalone Python 3 standard-library entry.
Its fixed inventories and original `STATIC_CAPTURE` body come from
`q03_baseline_identity.py` at admission commit
`eb775f13d4a61ece739003c5fca860864d91ea63`. The successor changes only U source-state observation to distinguish a nonexistent
path from an actually empty directory. It does not import that controller,
consult a profile, require a runtime manifest, or accept any image/argv override.

| Selection | Image ID | Full source inventory | Required absence |
| --- | --- | --- | --- |
| `r6x-unpatched` (U176) | `sha256:fc6a9ef83dd1a741a32c2724263e9c05a2635dc8154784ffe0f9c36709018082` | 19 modules; head `7316c7f9fae1bc4a0b7ab1848de0db4143eec5d2`; tree `4d6851c8bb1397a55254ba88905a87d9785b98dc` | No source entries at `models/deepseek_v4_1/steering`; see the versioned witness below |
| `r6x-steer` (S188) | `sha256:8ad0da0366af0560678e09dbccefedc6688a58d79758b4f11fc63ef69ede4a0a` | 31 modules; head `ebb44766d6f04d3a9272b712778d7f2fa2cec1fd`; tree `ac79557915e3ee2778f8f6353a3d35f848f564e4` | none |

Both exact RootFS projections, component labels, and native parent
`sha256:a63259a196d82dae51016d487ed43093f2045261adcb41045cfe9c1be29f70d2`
are frozen and checked before collection. The collector checks the full module
hashes, truthful source-path state, exactly one non-aliased ELF per native name, package
metadata, package origins/hashes and the literal Torch version source/hash.
It does not execute Torch, vLLM, native ELF, model, tokenizer or generation code.

## Minimal invocation on the authorized image host

First publish/review this source slice and select its exact commit. The following
commands are for the independently authorized host holding the retained images,
not the cloud test environment. No command changes or restores the original
containers. Run from a clean checkout of the reviewed commit. Capture outputs
must be outside that checkout in a private, owned mode-0700 parent directory.
Do not copy an old A188/B197 manifest or edit an image label to manufacture U.

```sh
# Plan only: emits the exact fixed-image argv but executes nothing.
python3 -I -B scripts/q03_runtime_capture.py plan r6x-unpatched \
  --capture-id 11111111111111111111111111111111

# Set this to a NEW evidence parent outside the reviewed checkout.
EVIDENCE_PARENT="$HOME/r6x-runtime-evidence"
mkdir -m 700 "$EVIDENCE_PARENT"

python3 -I -B scripts/q03_runtime_capture.py capture r6x-unpatched \
  --capture-id 11111111111111111111111111111111 \
  --output "$EVIDENCE_PARENT/u176-11111111111111111111111111111111"

python3 -I -B scripts/q03_runtime_capture.py capture r6x-steer \
  --capture-id 22222222222222222222222222222222 \
  --output "$EVIDENCE_PARENT/s188-22222222222222222222222222222222"
```

Use fresh 32-character lowercase hexadecimal capture IDs for a new attempt.
An already-existing output or exact container name is a refusal, not permission
to overwrite or remove it. No arbitrary command, model path, mount, image tag,
Docker host or GPU runtime is accepted. Docker selection is a cleared environment
and `unix:///var/run/docker.sock`. The command uses `--pull never`, `runc`,
`--network=none`, `--read-only`, dropped capabilities, no new privileges, no host
mounts/devices, one CPU, 1 GiB RAM without swap, 64 PIDs, and a 120-second timeout.
Python uses `-I -S -B`; site hooks, `.pth` execution and bytecode writes are skipped.
The output directory is not mounted into the container.

## Evidence and failure semantics

Each attempt creates only its fresh private directory. Files are exclusive
mode-0600 creations, checked without following symlinks. Original stdout/stderr
are retained byte-for-byte, including failures, alongside execution return codes,
plan, script/collector hashes, raw image inspect and pre/post name-absence checks.
Each subprocess has a 1 MiB output-file ceiling. There is no shell evaluation.

The first real PR45 U capture stopped on the old strict path-absence condition.
Its retained original failure and read-only TAR witness identify an actual empty
0755 directory in the unchanged `fc6a9ef...` image, not executable steering code.
The prior failure is not erased or relabelled as a successful capture.

On success, `runtime-manifest.json` uses these closed versions:

- Version 1 is unchanged for S and for U whose path truly does not exist. U's
  `absent_source_paths` contains the steering path only in that nonexistent case.
- Version 2 is permitted only for the exact fixed U identity. It requires
  `absent_source_paths: []` and
  `empty_source_directories: ["models/deepseek_v4_1/steering"]`.
  This witnesses a real canonical, nonsymlink directory, successfully opened
  with no-follow and scanned to completion with zero entries. It does not claim
  the directory is nonexistent. Any child, including empty files, hidden files,
  bytecode, child directories or links, fails. Symlinks, unknown path types,
  read errors and observed directory changes also fail. No mode broadens S.

Both versions use canonical JSON bytes (`sort_keys=True`, compact separators, `allow_nan=False`,
no newline). Its exact-byte SHA256 is in `receipt.json`; raw collector output
remains `capture.stdout`. The separate image observation retains verified current
image/config facts. Capture evidence does not prove four-rank execution, imported
runtime equivalence, model mounts or GPU qualification.

Any collector, validation, timeout, output or postabsence failure is non-success;
raw evidence stays in place and no successful receipt is emitted. A timeout or
uncertain Docker result can leave this capture's CPU container behind. The tool
never guesses that it stopped, never deletes an unknown container, and never
retries automatically. Inspect the exact recorded name and reconcile ownership
before a separately authorized retry. Original service lifecycle is untouched.

Review the actual U/S outputs before separately binding their exact manifest
hashes and common runtime projection in the admission source. A newly captured
manifest cannot fill its own admission pin. U/S matching metadata/native bytes
must be established from the actual results, not this CPU test fixture.

## CPU verification

```sh
python3 -B -m unittest discover -s tests -p test_q03_runtime_capture.py -v
```

Tests execute the unchanged collector body against real synthetic package files,
metadata and non-loadable ELF fixtures under isolated Python. They exercise real
JSON serialization and private file creation, v1/v2 nonexistent-versus-empty source witnesses, fail-closed aliases/children/read errors,
module/native bytes, duplicate metadata, ELF ambiguity and literal version
parsing, plus nonzero/timeout/malformed-output/busy-container failure paths.
Docker calls are inert fakes. No Docker image, device, model or network execution
has been performed by these tests; no native/GPU result is claimed.

The CPU entry remains usable before final U runtime hashes are bound. Matching
admission, worker observation and poststart validation must consume the same
versioned witness and compare it to the frozen manifest before service use.
This standalone capture does not enable that later admission by itself.
