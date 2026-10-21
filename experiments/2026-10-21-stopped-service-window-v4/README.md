# Complete owner recovery test session binding

Base1bf860e36f2fcd750cd8c7184c1212adfc705611 is preserved. Cloud full
327-case run had no assertion failures and three errors rooted in one synthetic
owner test session lacking the immutable capsule now required by core recovery.
Bind that fixture to its explicit keep-stopped manifest/capsule. Preserve both
UNKNOWN owner/GPU cases, no-START/no-release and cleanup ordering assertions.
No production code, native source/image or original asset changes in this revision.
Cloud full-suite and integrated review remain required before field execution.
