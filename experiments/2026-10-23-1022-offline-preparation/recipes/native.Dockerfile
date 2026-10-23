FROM ds41f-1022-native-parent:8ad0da0366af
USER root
LABEL local.spark3.sparknet.revision="ed6af327a8953eb71de2ed925a5e605cb5eb6e20" local.spark3.sparknet.patch-head="ed6af327a8953eb71de2ed925a5e605cb5eb6e20" local.spark3.sparknet.tree="f997a594f03a696897e6ad8e50bb027ed877ebe9" local.spark3.sparknet.ref="refs/tags/v0.5.0"
COPY sparknet /tmp/sparknet-source
ENV SPARKNET_ROCE_CACHE_DIR=/opt/sparknet/roce-1022
RUN python3 -m pip install --no-index --no-build-isolation --no-deps /tmp/sparknet-source && python3 -c 'import importlib.metadata as m; assert m.version("dgx-spark-networking")=="0.5.0"; from sparknet.oneshot._proxy import load; print("proxy ABI", load().roce_abi_version())'
