# Release window attempt 1

Window `650fedd7-db18-42a1-a94a-45131385c5e8`, published source
`6f3970b5808da55234ff21f18775091451cffa06`, stopped the retained four ranks
and then refused the component command: `Command executable must be pinned`.
The new qualification harness had pinned Python but omitted the Docker CLI
and used a relative executable name. No component tests or candidate serving
requests ran. This is a qualification harness failure, not a model result.

The automatic recovery completed with `phase=closed`. All four retained heads,
image IDs, source trees, config/node/profile hashes and effective command/mount
identities match their before-window values. The ingress is open, hold removed,
and DeepSeek gateway configuration identity unchanged. The watchdog was stopped
after closure. The runner exited 1 as expected for failed qualification.

The follow-up harness uses `/usr/bin/docker`, pins both host executables, and
validates all pins and the component command before opening the window. Three
new offline tests cover required executable pins and side-effect-free planning.
The prior failed window and authorization remain preserved here; retry requires
a fresh UUID, authorization, observations and published source revision.
