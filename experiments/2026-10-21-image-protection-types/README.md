# Preserve typed image-preparation container identities

Parent: 2c80fe4fcda42f5ce48808f12eb89dbc9aa163d6.

One intended variable: compare canonical JSON bytes of the existing normalized full Docker inspect objects in image preparation, rather than Python dictionary equality. The parent's present-null/false OomKillDisable alias is retained. Numeric zero, false, absent fields, and unrelated state changes must not be conflated. Known Mounts/Ulimits order normalization remains; duplicates and unknown fields are preserved.

No Docker command, model/runtime source, image, mailbox series, workload, GPU policy, service disposition, or active capsule is changed. This is a new independent candidate, not an update to the parent's deployed freeze ref. Reuse the existing native image; no rebuild is required.

Focused CPU validation:

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. python3 -m unittest -v test_q03_image_preparation test_q03_owner_model test_q03_owner_oom_default
    git diff --check

18 image-preparation methods, 42 owner methods and 3 nullable-OOM methods passed separately on this source tree. New cases cover symmetric null/false aliases without input mutation; numeric, true, missing, string/list/object and other-field changes; unordered lists and retained duplicates. These are CPU contracts, not fresh field execution or owner/model acceptance. No unrelated full suite, native build, repeated P0/G1/G2, or GPU execution was run.

Minimum deployment observation: new independently pinned controller/capsule, already-present exact image branch, unchanged original asset snapshots, then the existing owner attempt and positive all-rank drain. Preserve the single field execution lane. Do not hot-edit or upgrade an active capsule, and do not restart original services. Record any outcome against this candidate's exact commit separately from its parent.
