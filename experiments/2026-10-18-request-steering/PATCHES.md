# 补丁拆分与文件清单

以下是待实现的提交边界，不是已经存在的补丁。本轮以当前稳定版本 r6v 进行修改与验证，路径以其应用完整作者 series 后的 vLLM 源树为根；部署仓库路径另列。长期开发持续跟进原项目，r6v 不构成永久版本约束。编号暂定 0181–0187，集成上游更新时重新核对调用点、依赖和编号，以内容与依赖关系识别补丁。

## P0 基线复现和修改预算

本轮已用全新 macOS 和本地 Linux CPU 构建树重放原 172 个补丁，核对 [base.json](base.json) 的 `patch_head`、`expected_tree`、`patchset_sha256` 及导出摘要，结果一致；完整构建输入准备和 `build check` 也通过。原始 helper 在 macOS 的 0154/0103 缺失对象错误与自动 repack 并发诊断均保留，构建工具隔离修复见 [独立 PR #2](https://github.com/imDaniel/spark-ds41f/pull/2)（`9635927`，已合并至 `homelab/main`）；实现分支需包含它。没有修改基线补丁或旧 manifest，不能把构建工具修复混入 steering 数值补丁。

检查 `config/cluster.json` 的 `VLLM_USE_V2_MODEL_RUNNER=1`，并沿 `gpu/model_runner.py` → `DeepseekV41ModelState` → 模型 forward 核对路径。V1 的 `vllm/v1/worker/gpu_model_runner.py` 不是首版接入目标。

现有文件的主要修改预算为以下四个：

| 既有文件 | 接入点 | 目的 |
| --- | --- | --- |
| `vllm/v1/engine/input_processor.py` | `InputProcessor.__init__`、`process_inputs()` 中生成 `EngineCoreRequest` 前 | 严格验证请求，合成首次 hash 使用的 cache salt |
| `vllm/models/deepseek_v4_1/nvidia/model_state.py` | `DeepseekV41ModelState.__init__`、`add_request()`、`remove_request()`、`prepare_inputs()`、`prepare_dummy_inputs()`、必要时 `finalize_cudagraph_inputs()` | 捕获尚未丢弃的 extra_args、生命周期、按最终 batch 布局产生 scales |
| `vllm/models/deepseek_v4_1/nvidia/model.py` | target 初始化、`DeepseekV41LLMForCausalLM.forward()`、`DeepseekV4Model.forward()`、`DeepseekV4DecoderLayer.forward()` 与 kernel 声明 | 绑定已验证 profile、转发 scales、处理 CED/SP、在 WO-B → mHC 边界调用 |
| `vllm/models/deepseek_v4_1/nvidia/vl_model.py` | `DeepseekV41ForCausalLM.forward()` 的参数转发 | 当前 wrapper 接收 `**kwargs` 但未传给 language model，必须显式透传新 scale 参数 |

融合和 DSpark 审计可能增加下面明确列出的文件；每一处必须由测试或剖析证明必要，不以“只有三个挂钩”限制正确性。

## P1 0181 steering profile contracts

**新增：**

- `vllm/models/deepseek_v4_1/steering/__init__.py`
- `vllm/models/deepseek_v4_1/steering/profile.py`：schema、规范化身份、只读本地 artifact 验证、算子版本、加载合约。
- `vllm/models/deepseek_v4_1/steering/request.py`：纯 CPU 的字段校验、规范 FP32 scale bits、stock 判定和 namespace 编码。
- `tests/models/test_deepseek_v4_1_steering_profile.py`
- `tests/models/test_deepseek_v4_1_steering_request.py`

**部署仓库新增：** `experiments/2026-10-18-request-steering/tools/convert-profile.py`，只在离线转换时使用源方向包。profile 数据不提交到 Git。

本补丁不激活任何模型路径。请求范围 `[0,1]`，源方向的 alpha 2.5 与请求 scale 分开；记录 artifact/model/template/算子完整身份。验证损坏、长度、形状、重复层、NaN/Infinity、范数、摘要、错误 revision 和类型边界，覆盖零强度的所有合法/非法组合。单位范数的初始绝对容差为 `1e-5`，校验逻辑不能擅自归一化或修复坏 artifact。

## P2 0182 request identity and cache isolation

依赖 P1。修改 `vllm/v1/engine/input_processor.py`，在 clone 后的 SamplingParams 和 EngineCoreRequest 构造之间调用纯 CPU helper；不能修改调用者原 SamplingParams。配置通过既有 `additional_config.ds41_steering` 取得并验证。

**新增：**

- `vllm/models/deepseek_v4_1/steering/cache.py`：版本化、无歧义的 salt 合成。
- `tests/v1/engine/test_deepseek_v4_1_steering_admission.py`
- `tests/v1/core/test_deepseek_v4_1_steering_cache.py`

namespace 必须包含模型/模板身份、算子版本、profile 内容与 schedule 摘要及规范 FP32 scale；与原用户 salt 组合，不能覆盖其隔离作用。stock 保留原 salt，不改 block hash 算法；两种等价的 FP32 scale 产生同一身份。覆写或恢复请求时，变化的身份不能继承私有 KV/CED 页。

需要测试相同 token 的 stock/非 stock/不同 scale、原用户 salt、streaming update、prefix hit、取消与恢复。新请求可带新强度；原请求中途换强度明确失败。未支持的 KV 传输/持久化模式在选中 profile 时拒绝。

暂不修改 OpenAI `protocol.py`、`SamplingParams`、`EngineCoreRequest`、scheduler 或 `kv_cache_utils.py`。若 cache salt 不能覆盖某种实际缓存，补充独立身份传播补丁和测试，不能仅凭 salt 存在就宣称隔离成立。

## P3 0183 model state and fixed row controls

依赖 P1–P2。修改 `nvidia/model_state.py`、`nvidia/model.py` 和 `nvidia/vl_model.py` 的可选参数转发。利用现有 V2 ModelState 接口，不预先扩展通用 runner 或 ForwardContext。

**新增：**

- `vllm/models/deepseek_v4_1/steering/runtime.py`：每 worker 固定地址的方向、请求 scale、token-row scale 和 scratch；无进程全局“当前请求”状态。
- `vllm/models/deepseek_v4_1/steering/rows.py`：请求索引到真实 token 布局的映射及 CED/SP 辅助。
- `tests/models/test_deepseek_v4_1_steering_state.py`
- `tests/models/test_deepseek_v4_1_steering_rows.py`

`add_request()` 在 V2 分解丢弃 SamplingParams 前登记选择，`remove_request()` 清理；`prepare_inputs()` 依据最终 `InputBatch.req_ids`、`idx_mapping`、query offsets/真实 token 数填写。不能使用 scheduler 字典顺序推测 decode-first 排序。处理暂停、恢复、同 ID 更新、取消、槽位复用、不同 verify 长度、零 token、dummy/profile/capture 和 padding。

显式 tensor 参数随模型 forward 传递；CED gather 与 SP 分片须同步转换同一 scale 行。profile 未加载时走原分支，不分配 steering device buffer。profile 加载后方向/scratch 在 preparation 与 graph capture 前完成分配，后续只更新内容，不随新 scale 扩大 vector bank。控制传输需合并到已有 staging，若原型额外拷贝，记录并纳入 P6 剖析。

## P4 0184 attention writer projection reference path

依赖 P3。在 `DeepseekV4DecoderLayer.forward()` 的 attention 返回到 `mhc.post_pre()` 之间接入；层索引限制 2–39，target layer count 与配置必须一致。

**新增：**

- `vllm/models/deepseek_v4_1/steering/ops.py`：有 fake 实现与明确 mutation/alias 规则的 opaque custom op。
- `vllm/models/deepseek_v4_1/tilelang/steering.py`：FP32 dot/projection，普通 writer 和 rank-order partials 的明确入口。
- `tests/kernels/test_deepseek_v4_1_steering_projection.py`
- `tests/models/test_deepseek_v4_1_steering_attention.py`

先建立可独立校验的数值参考，允许原型额外 launch；不把原型作为推广候选。普通 writer 在其既有 BF16 边界后以 FP32 投影并转回 BF16；SP 的 `RankOrderParts` 先按原 rank 顺序 FP32 求和、在既有位置舍入，再应用相同算子。不得为了套用普通 Tensor kernel 改变求和顺序。

固定 dot reduction 顺序，跨 row bucket/批次保持一致；对近共线、零、极小/极大有限输入及非有限输入建立明确测试。不用代数相消公式推断投影后范数，不夹紧 NaN。scale 0 writer 必须逐位相同；其他输出按 [验证矩阵](VALIDATION.md) 的精度标准验收。

新 kernel 按既有 `declare_kernels()` / preparation registry 声明、编译和 warm-up，先于 freeze/capture；禁止首次 steered 请求触发 JIT。

## P5 0185 speculative target and graph lifecycle

依赖 P4。主要增加端到端边界测试并审计实际 V2 DSpark 路径：

- `vllm/models/deepseek_v4_1/nvidia/dspark.py`：draft block 复用 target layer 类时，不绑定 target profile、不额外投影。
- `vllm/v1/worker/gpu/spec_decode/dspark/speculator.py`：target verify 行、拒绝 suffix、bonus token 和 draft scope 生命周期。
- `vllm/models/deepseek_v4_1/nvidia/model_state.py`：dummy/capture、CED 边界重建和后续 target 步的 control refresh。

前两项只有发现既有接口无法保证隔离时才修改，禁止复制或重写整个 speculator。

**新增：** `tests/v1/spec_decode/test_deepseek_v4_1_steering.py`、`tests/kernels/test_deepseek_v4_1_steering_graph.py`。

目标预填充、decode 和每个实际 verify token 都使用请求 scale；padding、未提交 suffix 的持久状态、draft block 均遵守各自作用域。drafter 接收改变后的 target 状态是预期行为，“不投影草稿”不等于草稿输出或接受率不变。FULL/PIECEWISE 图中混合 scale、交替顺序、取消后复用都要覆盖；不能通过 eager 模式宣称图兼容。

## P6 0186 optional consume fusion

仅在 P4/P5 数值参考稳定并有 GPU 剖析后实现，作为独立可回退补丁。主要候选文件是 `vllm/models/deepseek_v4_1/tilelang/mhc.py` 中 `post_pre/project_streams` 的 writer 消费路径。`tilelang/wo.py` 或 `attention.py` 只有需要传递明确绑定参数时才改；不改 sparknet/NCCL 算法。

融合必须沿用 P4 定义的求和、舍入和投影语义，证明与独立 op 的等价性。因为 mHC 以 hidden block 分工，完整 `dot(d,y)` 需要额外 reduction/scratch 和发布顺序，不能直接把一个局部 dot 塞进现有 block。若前移到 rank-local WO-B 会改变数值边界，另立算子版本及质量实验，不作为透明优化混入。

新增同名 fusion fixture，覆盖 world=1/3/4 的合成 partials、各 row bucket、scratch 重用、publication/guard、stock 与 mixed rows。只有实测达到性能 gate 才接受；“没有新增 launch”本身不是性能证明。

## P7 0187 candidate packaging and release gates

实现阶段才在部署仓库增加：

- 本实验的 `vllm/series`、0181…补丁、`compose-series.py`；按原顺序解析并引用作者文件，正确重算相对路径，追加我们的补丁。不使用当前 reader 不支持的嵌套 include 语法。
- `upstreams.lock.json`、`source-manifest.json`：候选完整输入，保留外部 revision，记录新 patchset/head/tree；不复制旧结果冒充新身份。
- `cluster.json`：本轮从 r6v TP4 recipe 派生并继承私有 site overlay 的已登记参数；后续轮次基于相应上游版本重新派生并验证。指定候选 lock、独立缓存/镜像名、profile 只读挂载与 additional config，设置 `deployment.launch_enabled=false`，启用须另经评审。公共模板只使用占位信息，不发布实际站点配置。
- `tools/qualify-steering.py`：冻结输入和采样、直连已有后端的匹配验收；使用时仍须设备授权。
- `runs/`、`results.json`、`decision.md`：仅在实际执行后记录，不提前写“通过”。

修改 `config/release-tests.json`，逐个分类所有新增 vLLM test；在 `.github/workflows/validate.yml` 增加候选 patch replay、CPU 合约/行映射测试。GPU 检查使用单独显式调度，不启用任何生产 self-hosted runner。现有正式 recipe、lock、Dockerfile 和历史 baseline 保持原样；仅在现有 build context 无法承载时另评审 Docker 改动。

每个 `.patch` 的说明都列明外部 base、作者 stack 的 parent、目的、数值/能力影响、测试与上游状态；本站补丁默认标记 `downstream-only; not submitted upstream`，不向原作者提交。新增文件及其必要调用一并进入可重放补丁，不能只提交 `.work` 中的修改。
