# Fourth release qualification attempt

Window `18793c73-06bb-4100-b0d8-8b836cacd869` used repository `8c79b93e`,
RC3 image `14d5bb2c…` and the unchanged TP4 steering configuration. The original
failing tool request now preserves the nested literal control spelling. Its
generated token IDs are exactly identical to the preceding failed RC2 response,
isolating the parser repair from generation and steering arithmetic.

All 12 scale-1/4 × named/required/auto × complete/stream tool calls passed, as did
two followups and 20 quality smoke responses. Arithmetic, JSON sorting/fields,
Python addition AST and Chinese TCP/application-retry responses were checked;
the full Chinese text was manually read. This small set is not broad model-quality
or rejection-rate qualification. Return-to-zero output hashes were identical.
All 13 32K cache checks passed.

Cold C1 inputs 32,768 / 131,072 / 262,144 / 524,288 / 1,000,000 all returned
exact prompt usage, 32 generated tokens and zero cached tokens. Their elapsed
times were 4.969 / 19.755 / 41.612 / 92.290 / 209.910 seconds. These are capacity
observations, not comparisons against previous model versions.

The C16/8192 batch returned 16 successful responses in 20.443 seconds, but its
peak running count was five. The first request finished around six seconds,
before the last was admitted. The 32-token output budget was too short to prove
C16 residency. The unchanged assertion rejected the run. C4/262K, C4/1M and
stability were withheld. The candidate's later SIGTERM was controlled teardown.

Automatic recovery completed in 12.338 minutes from opening. All four retained
source/image/config/profile/mount/command identities matched, private/gateway
checks passed, gateway configuration was unchanged, ingress reopened and the hold
was removed. The watchdog was stopped after recovery. No Docker/driver restart
or new GPU fault occurred. Minimum observed available RAM was 21.10–22.55 GiB;
peak temperature was 78–79°C, with no observed swap growth. Full samples and all
unfavorable results are retained. `checksums.json` covers this evidence.
