# 1022 attention steering 离线集成

后续审查修订见 [1022 attention review](../2026-10-23-1022-attention-review/README.md)。
本目录的 197 项补丁、source、profile 与原始验证记录保留；后续硬件验证应使用修订候选。

状态：本地候选，禁止启动；未构建镜像、未运行 GPU 测试、未同步或更新任何服务。
当前服务的回退点是 `archive/r6x-attention-steering-20261023`，部署提交
`223a3bb3498d12f470e876db3b9e1f5bdc8772b6`，镜像 `sha256:8ad0da0366af0560678e09dbccefedc6688a58d79758b4f11fc63ef69ede4a0a`。
历史 r6x 验收仍引用原始记录，本目录不改写它们。

## 集成身份

部署仓库从 `3ff57762036fc72c2221c3845a39e70850fd2d7e` 合入上游
`26aebf209534a2d43799bf4be7b7e344efe97c7c`（1022），合并提交见 [base.json](base.json)。
上游远端只读，工作分支为 `integrate/1022-attention-steering`，已发布到本站仓库的
[草稿 PR #56](https://github.com/imDaniel/spark-ds41f/pull/56)，尚未合并。

一项集成变量：将既有 attention writer steering 保留在固定 1022 基线上，并将相同方向数据绑定到新编码器。
不加入 Q03、GLP、vLLM 0.32、投影优化、新方向或新强度策略。

- vLLM 基础仍为 `19f2c20ed4d64ae22bd009ceacadd85e2a81e354`。
- 上游 184 项补丁精确重放至 `8fc15c18f8903b97c36f5268ebd648b53d9f9689`。
- 新目录中的 0500–0511 与旧 r6x 补丁逐字节相同；上游原系列不变。
- 0512 仅增加主机端联合测试，并适配 profiling 测试替身的 `layer_progress`，不改运行逻辑。
- 合计 197 项补丁；head `e97617bd559691aac8cbf8239496f06dd87ded6c`，tree
  `fe867f8e203120419f2108c46bcfcbb71fce99d3`。
- [source.json](source.json)、[upstreams.lock.json](upstreams.lock.json)、[patch-map.json](patch-map.json)
  固定完整身份、补丁指纹和旧→新映射。原生依赖沿用 1022，包括 sparknet 0.5.0。

## Profile 绑定

1022 的控制 token 修复改变 tokenizer/renderer 源码。原 profile 在新模板身份下被严格拒绝，
不能直接换镜像后继续使用。新文件的逻辑名称仍为 `heretic-1017-attn-v1`，保留现有请求名称。

[rebind_profile.py](tools/rebind_profile.py) 固定旧文件 SHA、模型身份、旧/新模板身份，
要求源码 head/tree 与候选清单完全一致且干净。它使用本地模型元数据、tokenizer 和可信权重清单，
不打开权重 shard，不加载 pickle，不访问网络，也不改变服务加载器的检查。

| 身份 | SHA-256 |
| --- | --- |
| 旧 artifact | `d7f0b997ea2748b03a353188583d494b2bfe7c40838aff98945941032da6a093` |
| 新 artifact | `25016574ca90478ed62109005234d48d11dd0d343d36cce413db3a4bbefc7475` |
| 相同方向 payload | `f11e3762252448f547183d4b2daaf5b58c2d9023cd5b23365bc026a402c1a8e6` |
| 旧模板身份 | `257e32a70aa22dcb203b6381bb21ee9f969c605c275e9a695740adcf12d97359` |
| 1022 模板身份 | `8e970a61dec1379be9036c19fabbf442da4285d4e5a839210b5b9278ae6359ac` |

层 2–39、alpha 2.5、778240 字节 F32 方向、模型版本、权重身份、schedule 和算子均不变。
只更新模板绑定与转换来源；旧→新转换记录见 [profile-rebind.json](profile-rebind.json)。
数据集来源的未知项仍为 null，不声称重新提取方向或与 baked FP8 Heretic 权重等价。

新 release 使用独立目录；其内部文件仍叫 `steering/heretic-1017-attn-v1.safetensors`，
这是 bundled-index 的严格格式要求。脚本拒绝覆盖已有 release；不能用新文件覆盖旧服务的 bundle。
新 profile 身份自动产生不同的 active prefix-cache namespace；scale=0 仍保留 stock salt。

## 离线检查与复现

完整结果见 [results.json](results.json) 与 `runs/`。检查分开记录，历史 GPU 数字不算本候选通过。

本轮主机是 macOS ARM64、Python 3.12；CPU 环境的完整版本见 `runs/cpu-environment.txt`。
Linux x86_64 的复现依赖是 `requirements/steering-1022-cpu.txt`（带哈希）；
macOS 使用本机 torch 2.14.1 wheel，不安装其中的 Linux CPU wheel。未在本轮执行 Linux 镜像测试。

GitHub Actions 的 `steering-1022-cpu` 单独重放全部 197 项补丁、验证候选 head/tree，
并执行 `cpu-cases.json` 固定的 126 项 CPU 测试；失败、缺项或跳过均不能通过。
候选目录、补丁、锁文件及验证工具发生变化时会触发该检查，并保留 JUnit 报告。
真实 tokenizer 和 profile 文件没有上传到 GitHub，下面的 36 项编码与 9 项真实文件检查
仍是本地离线证据；CI 通过不代表这些文件已在云端重测，也不代表 GPU 或服务验收。

```sh
bin/spark --cluster-config experiments/2026-10-23-1022-attention-steering/cluster.json doctor
bin/spark --cluster-config experiments/2026-10-23-1022-attention-steering/cluster.json build prepare
bin/spark --cluster-config experiments/2026-10-23-1022-attention-steering/cluster.json build check

# 在已安装 CPU 依赖的 Python 3.12 环境执行；只运行固定的 CPU 集合。
python scripts/check_steering_cpu.py \
  --upstreams experiments/2026-10-23-1022-attention-steering/upstreams.lock.json \
  --cases experiments/2026-10-23-1022-attention-steering/cpu-cases.json \
  --report .work/1022-steering-cpu.xml

python scripts/release_tests.py check \
  .work/build/vllm-19f2c20ed4d6-b12x-236ddff04a5f-deda0e56/src/vllm \
  19f2c20ed4d64ae22bd009ceacadd85e2a81e354 \
  e97617bd559691aac8cbf8239496f06dd87ded6c \
  experiments/2026-10-23-1022-attention-steering/release-tests.json
python -m unittest discover -s tests -v
```

重绑时指定本地旧文件、模型元数据快照和一个不存在的新目录：

```sh
python experiments/2026-10-23-1022-attention-steering/tools/rebind_profile.py \
  --input "$STEERING_OLD_PROFILE" \
  --model-identity "$STEERING_MODEL_IDENTITY" \
  --vllm-source "$STEERING_VLLM_SOURCE" \
  --source-manifest experiments/2026-10-23-1022-attention-steering/source.json \
  --output "$STEERING_NEW_RELEASE"

# 上述四个路径变量须明确指定；source 是完整、干净的候选 prepared tree。
# 新 release 已生成后，使用相同路径运行：
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 CUDA_VISIBLE_DEVICES='' \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 \
python -m pytest -p no:cacheprovider \
  --confcutdir=experiments/2026-10-23-1022-attention-steering/tools -o addopts= -q \
  experiments/2026-10-23-1022-attention-steering/tools/test_encoder_offline.py \
  experiments/2026-10-23-1022-attention-steering/tools/test_profile_offline.py
```

变量须 export 给测试进程。编码检查执行真实源码函数体及真实 Rust/Transformers tokenizer，
只隔离 CUDA 包导入边界；renderer 检查其 tokenization 入口，不是在线图像处理链。
Profile 检查实际文件、确定性重打包、file/hf:default/hf:NAME 三种真实启动加载路径，
以及错误输入、模板、模型、源码和已有目标的拒绝。

候选 [cluster.json](cluster.json) 从保留的日常验收配置派生：保留本站 GID 5、网络映射、
1M/16seq/8192 batch/10.5 GiB KV、DSpark 5 与 0.035，以及 prompt-token 明细开关。
显式保留 1022 的 late-admission layer 30 与 sink rejection。容器、镜像、缓存、profile 挂载、
部署目录和 18000 端口独立，`launch_enabled=false`；节点映射仍为 ignored 本地文件。
这些目录与文件没有被复制到服务器，镜像标签是待构建身份，不表示镜像已存在。

## GPU 和晋级边界

后续需要单独授权硬件窗口，先读 hold 文件，再使用协调控制器。恢复目标须是上述当前 r6x 服务，
不能直接沿用历史验收记录中更早、可能已清理的 fallback 路径。

1. 构建新镜像并验证 sparknet 原生组件、GPU 导入、投影数值与 stock mHC 对照；四 rank 使用同一镜像身份。
2. 比较 A 纯 1022、B 补丁未加载 profile、C 加载但 scale=0、D scale=1/4。
   A/B/C 在相同确定性条件下保持输出一致；D 对 C 隔离 steering 的效果和成本。
3. 实际验证 stock/active 两套 FULL graph 的 late-admission 事件，以及 SP/CED、长 prefill、
   draft-zero 接受/拒绝、KV 回滚、grammar、prefix cache、取消/槽位复用和流式断开。
   CPU 主机行布局检查没有执行真实 sink sampler、GPU graph replay 或 KV 回滚。
4. 重点观察 residual column 2455、阈值 85000 在 steering=1/4 下的分布和误拒绝。
   需要时做 sink 开/关 × steering 开/关诊断；不为使测试通过而直接关掉 1022 默认策略。
   覆盖 greedy 与非零温度采样，后者的采样路径会受 sink rejection 影响。
5. 覆盖 C1/C4/C16、闭环与同步 burst、TTFT 尾部、吞吐、草稿接受率和内存，
   然后递进至长上下文及有界的近 1M 配对检查。单请求 1M 不等于 16 路同时 1M。
6. 完整 release gate、功能/质量/稳定性/性能验收及 owner 接受后，才考虑发布和协调部署。
   当前没有新的 GPU、性能、容量或服务验收结论。

## PR #56 审查补充

修正了候选启动路径：release 根目录挂载到 `/steering-1022` 后，文件路径为
`/steering-1022/steering/heretic-1017-attn-v1.safetensors`。新增配置与实际加载器回归，
本目录复测为 487 项仓库测试、126 项 steering CPU 测试、36 项编码器和 10 项 profile 检查；
结果单独保留在 `runs/review-20261023/`，不改写原始 `results.json`。
编码器 JSON 边界修复及新的模板/profile 身份在上述修订候选中记录。
