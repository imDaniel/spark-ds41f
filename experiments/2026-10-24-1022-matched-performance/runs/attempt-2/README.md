# Successful matched measurement and owner-trial handoff

Window `d8edd2b0-d183-42c4-9da4-e5a6ab332f42` ran commit `4ec4bab7` and the
unchanged RC3 image `14d5bb2c…`. All six closed-load cases completed: 294 requests,
241 started in timed windows, zero failed/rejected/foreign/approximately timed
requests. All outputs have 256 tokens. Eighteen document primes have zero cached
tokens. Eight before/after arithmetic and literal-tool checks passed.

The window reached `owner_trial` after 19.769 minutes. Private and existing
gateway scale-1/4 probes passed, ingress reopened, the original r6x remained
stopped and all four candidate ranks and memory guards were verified running.
The hold has no automatic end time; the independent watchdog remains active.
The owner explicitly requested personal scale testing through existing aliases
and superseded automatic restoration after success. Failure recovery remained
enabled before acceptance. No production configuration was promoted or PR merged.

`summary.json`, `comparisons.json` and full native receipts preserve unfavorable
latency observations as well as gains. `owner-trial-inventory.json` captures actual
four-node identities and watchdog state; gateway configuration hashes match.
`credential-state.json` records that the local temporary key was removed and the
remote 0600 recovery copy remains within its 0700 operational directory. No key
values are archived. Raw generated Python-source documents carry the installed
Python copyright/license notice in `PYTHON-COPYRIGHT.txt`.

`checksums.json` covers this snapshot. Live hold timestamps can advance after the
snapshot as the owner continues using the candidate. No benchmark traffic is
generated after handoff.
