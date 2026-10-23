FROM ds41f-1022-screen:native-ed6af327-offline1
USER root
LABEL local.spark3.vllm.tree="9d8c8336026722c1746d8a7582d6a603331b0348" local.spark3.b12x.tree="1a647a311ca55d45070ab83d60668d9c8fb8886c" local.spark3.nccl.tree="6af10aa730a64fcee8781d62340a9c9184e79469" local.spark3.tilelang.tree="222b895d04ee6cd75179155d06e0c0d450636f51" local.spark3.tile_kernels.tree="cb2fa41c8af290204c81c547ed678ebf957cca53" local.spark3.sparknet.tree="f997a594f03a696897e6ad8e50bb027ed877ebe9" local.spark3.vllm.patch-head="a13d993616f6d3ff02b49cd5ef969a729a4d2146" local.site.native-parent="sha256:8ad0da0366af0560678e09dbccefedc6688a58d79758b4f11fc63ef69ede4a0a" local.site.qualification="1022-offline-preparation" org.opencontainers.image.source="https://github.com/imDaniel/spark-ds41f" org.opencontainers.image.revision="7252899c6498aae4a9149a6429e9f79d0dfdb538"
COPY verify_image.py source-hashes.json deletions.json /opt/spark3/1022-offline/
RUN python3 /opt/spark3/1022-offline/verify_image.py before
ADD source.tar /opt/spark3/candidate/vllm/
RUN find /opt/spark3/candidate/vllm -type d -name __pycache__ -prune -exec rm -rf '{}' + && python3 /opt/spark3/1022-offline/verify_image.py after
WORKDIR /opt/spark3/candidate/vllm
