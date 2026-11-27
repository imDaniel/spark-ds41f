# 1126 Attention on the four-node site ring

The integration parent is `825450d51480d920068165d100b527fe4e610859`. This arm enables the reviewed 1126 attention profile for on-hardware qualification. The comparison is the site's recorded 1022 RC3 release. Use the same official 1017 checkpoint, TP4 ring, 1M context, 16 sequences, 8,192 batched tokens and 10.5 GiB KV per rank. Use the original matched performance client, cases, prompt corpus, prefix priming and thermal gate. Baseline receipts are under `experiments/2026-10-24-1022-matched-performance`.

Record native image identity, four-rank identity, validated profile and kernel results. Run the functional, capacity and serving tests, including named/auto tool parsing, mixed scales, cache isolation and vision. Report throughput, TTFT, TPOT and end-to-end tails for the six matched load runs. Keep all attempts. The rollback target during the qualification window is the exact 1022 production checkout and image recorded in its baseline manifest.

## Qualification requirements from the current agent guide

The owner supplied an updated agent guide after this harness was written. Release measurements must use Sonar at the revision in `config/sonar.json`, through `spark_ds41f/sonar.py`, and preserve two candidate boots and their confidence interval. Keep the recorded 1022 runs as references. Preserve the original suites. A changed workload needs a separate suite and its comparison limits recorded.

The `qualify.py performance` entry uses the deployment's verified Sonar modules with the historical workload controller. Prompts, document input digests, seed, output length, concurrency, priming, thermal gate and six measured subwindows retain their historical settings. Run it on two candidate starts, preserving separate output directories and Sonar identities. Compare both boots and their batch-mean intervals with the archived 1022 runs, retaining the client revision difference as a comparison limit. The capacity entry runs the five historical cold C1 input lengths, 32,768 / 131,072 / 262,144 / 524,288 / 1,000,000, before the C16 and C4 batches.

Select Sonar's `text-chunks` stream collector for the historical timing convention. Its current default also counts token-ID and usage-only chunks, which can change the first-event and inter-token timestamps. The controller sets and records this selection. The prompt functions, statistical definitions and summary function match the frozen client's definitions. Sonar adds per-position draft metrics and an explicit stream-cancellation API.

Before using this controller, verify the global reservation and every participating node's reservation under the updated pool rules. Acquisition checks the head's global reservation files and the three workers' hold/request files. The ingress drain checks the workers again. Remote errors stop admission.

The initial [component test manifest](component-tests.json) selects 97 files from the candidate's exact source head, including the 73 available historical gate files, the replacement vision file and additional Attention, model and parser contracts. A coverage review found this insufficient for 1126. The [RC3 final manifest](../2026-11-28-1126-attention-rc3/component-tests.json) adds the remaining active-profile files: 179 canonical gate files and 16 retained historical files, 195 total. Three GLP files are outside the owner's current window. Keep every pass, failure, skip and collection error, including the earlier 97-file and supplemental runs; earlier successes do not replace the final image's gate.

Passing offline checks does not qualify this candidate for promotion. Native image build, four-rank execution, functional and capacity acceptance, the required performance measurements and physical service-control adoption all remain pending.

## Offline review receipt

The [review receipt](runs/offline-review-05a0a5a1/result.json) records 1,048 repository tests, 13 window tests and 45 recovery fault tests passing on Spark01 at `05a0a5a11a7f241569542f7b092f7f0db4e15a7e`. Three shape doctors and the generated documentation check passed. Tests ran in a detached worktree with example site maps. Timings printed by fake test services are fixture output.

GitHub Actions was initially disabled for the fork even though the permissions API and workflow reported enabled. Re-enabling the fork's workflows resolved the HTTP 422 dispatch failure. The 19-job validation at `8db4e91a9ef507771da0139042e01d6dc11ad568` [passed](https://github.com/imDaniel/spark-ds41f/actions/runs/46055199932). Every later release commit still needs its own CI receipts.

The service-control handoff at `baa1c3afc86ee5fbe877cdb2f3f7899846ebcd9e` passed seven site-control tests, 19 lease/watchdog tests and ten temporary-file adoption fault tests. The fixed installer journals the original nine host files and three shared dependencies, rejects subsequent unknown edits, and rolls back a partial or unsuccessful installation. The watchdog restores matching control files after restoring the accepted 1022 engine and before reopening ingress. Restart the verified watchdog on the published helper revision before creating the adoption journal. Prepare, render and install the controls only after the final image passes its hardware and performance qualification; these offline tests perform no host installation.
