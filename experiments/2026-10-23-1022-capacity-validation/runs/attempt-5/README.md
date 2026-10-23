# Fifth release qualification attempt

Window `3ead01df-5424-4f2b-9560-c68abc34a7f8` ran repository `997307fa` with the
unchanged RC3 image `14d5bb2c…`. The same-image request receipts and unchanged
GPU-component receipt passed the offline reuse guards before the window opened.

One literal-tool startup smoke call, all three cold concurrency cases and the
two-minute mixed-scale stability loop passed. The raw request report records
24 capacity responses and 1,888 stability responses, with exact token usage,
complete outputs, metric samples and timestamps. The assertion requiring the
requested running count was retained. All three batches also showed positive
overlap between the latest first-visible output and earliest completion.

Automatic recovery completed in 25.144 minutes from opening. All four retained
source/image/config/profile/mount/command identities match the preceding
inventory, private and gateway checks passed, gateway configuration is unchanged,
ingress reopened and the hold was removed. The runner exited successfully. The
watchdog was stopped after recovery, window authorization was archived and both
temporary gateway-key files were removed. No Docker/driver restart was needed.

`summary.json` gives counts, timings and sampled memory/temperature/swap results.
`checksums.json` covers the complete receipts, including command logs. No model
weights, tokenizer files, credentials or compilation caches are included.
