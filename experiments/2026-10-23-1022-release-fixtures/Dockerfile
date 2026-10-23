FROM ds41f-1022-parser:s-9dd408b8ee85-rc1
USER root
LABEL local.spark3.vllm.tree="d0ad0209a27395199385cf8cb69de6f9c72edf75" local.spark3.vllm.patch-head="4f6461ca14a0e0771d70924fb5f1312205804226" local.site.qualification="2026-10-23-1022-release-fixtures" local.site.parser-parent="sha256:f17320f38b419efa296a9862f634afb0ef4272e24818f1a6b20943bcca779239"
COPY verify_image.py source-hashes.json deletions.json /opt/spark3/1022-offline/
RUN python3 /opt/spark3/1022-offline/verify_image.py before
ADD source.tar /opt/spark3/candidate/vllm/
RUN find /opt/spark3/candidate/vllm -type d -name __pycache__ -prune -exec rm -rf '{}' + && python3 /opt/spark3/1022-offline/verify_image.py after
WORKDIR /opt/spark3/candidate/vllm
