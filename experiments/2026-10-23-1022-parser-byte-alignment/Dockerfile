FROM ds41f-1022-parser:s-4f6461ca14a0-rc2
USER root
LABEL local.spark3.vllm.tree="dc13e8dd17ff4415f0330084c02bb684ad021420" local.spark3.vllm.patch-head="b70fe11a88b34e0987e0e8c1b6796869be7e9f71" local.site.qualification="2026-10-23-1022-parser-byte-alignment" local.site.parser-parent="sha256:a15b2d0c6aca33304d95e2364366ff92ea2fc596edc545332d7ed217ac9b6241"
COPY verify_image.py source-hashes.json deletions.json /opt/spark3/1022-offline/
RUN python3 /opt/spark3/1022-offline/verify_image.py before
ADD source.tar /opt/spark3/candidate/vllm/
RUN find /opt/spark3/candidate/vllm -type d -name __pycache__ -prune -exec rm -rf '{}' + && python3 /opt/spark3/1022-offline/verify_image.py after
WORKDIR /opt/spark3/candidate/vllm
