FROM ds41f-1022-screen:s-dea96d04ea9e-offline1
USER root
LABEL local.spark3.vllm.tree="2ac30bad166054360075fc2def3143e76f3757c2" local.spark3.vllm.patch-head="9dd408b8ee85eec4491ce7176dc50dd7c87afe22" local.site.qualification="2026-10-23-1022-output-parser" local.site.parser-parent="sha256:bae44d0a14175bdc6c7a3fdbc5a8422e7493c2a0872857a83a88b304ab6b82cc"
COPY verify_image.py source-hashes.json deletions.json /opt/spark3/1022-offline/
RUN python3 /opt/spark3/1022-offline/verify_image.py before
ADD source.tar /opt/spark3/candidate/vllm/
RUN find /opt/spark3/candidate/vllm -type d -name __pycache__ -prune -exec rm -rf '{}' + && python3 /opt/spark3/1022-offline/verify_image.py after
WORKDIR /opt/spark3/candidate/vllm
