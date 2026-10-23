# Third release qualification attempt

Window `0a986749-faef-4bcb-a0aa-05130a0bfe91` ran commit `00f63bbf` and the
RC2 image `a15b2d0c…`. The 73 component files completed with 1,995 passed,
zero failures/errors and 158 documented skips. Critical steering, encoder and
parser cases did not skip. The two intentional CUDA fault cases retain the
preceding exact-runtime passing receipts; they were not reinjected.

The first scale-1, named, non-streaming tool call still returned an empty nested
value. The model generated 91 tokens and finished normally. Its raw IDs contain
the requested literal as ordinary character/byte tokens plus a final real EOS.
All subsequent request stages were withheld. The actual response reproduces in
the registered parser offline; reconstruction of UTF-8 byte spans fixes it.

The runner automatically restored r6x on all four nodes. Source/image/config/
profile/mount/command identities match the preceding inventory, private and
gateway probes passed, gateway configuration is unchanged and the hold was
removed. The watchdog was stopped after recovery. No driver/Docker restart was
needed. `summary.json` records duration from opening to the verified restore
event; later status polling is excluded.

The first new scanner-unit command omitted the parser-only conftest boundary
and stopped at an unavailable optional `tblib` dependency. Its log is retained.
The corrected CPU command reports two failures before repair and all four
passing after; the complete parser collection reports 4,379 passing cases.

`checksums.json` covers these receipts. No credentials, model weights, tokenizer
files, or component compilation caches are included.
