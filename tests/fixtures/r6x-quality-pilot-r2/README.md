# Public quality test material subset

These seven files are unchanged bytes from the reviewed, authored R2 pilot.
`SHA256SUMS` is the original full 34-member manifest, retained unchanged as the
material identity. This directory is intentionally not that complete archive.
The scorer validates every consumed file against its exact size and SHA-256.

The subset contains prompts and evaluator-only gold/contracts, not real answers,
token vectors, site inputs, runtime metadata or model evidence. Tests read the
files as data; no reference function, verifier or material generator is executed.
Only prompts.jsonl can enter the fake generation request fixture.

Four prompts retain exact historical texts from imDaniel/spark-ds41f commit
223a3bb3498d12f470e876db3b9e1f5bdc8772b6, servebench/prompts.py SHA-256
9e8510326546e862c35ee286a10589470b2423b70e4ab10e36f5b2cbbc3da0c6.
The accompanying LICENSE preserves that source's Apache-2.0 terms. The new gold
is authored pilot material, not recovered historical semantic-quality evidence.

The complete R2 archive is required by the portable producer. This minimal test
subset is not sufficient for preparation or site binding and must not replace
the private unchanged genuine preparation/provenance handoff.
