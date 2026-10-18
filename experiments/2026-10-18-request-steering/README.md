# DeepSeek 请求级 steering 实施计划

状态：**规划草案，尚未实现、构建或部署候选运行时**。本次变更增加计划、实验基线身份与下游维护约定。首个目标是在 1017 官方权重上加载一个独立 attention profile，按请求调节强度；保留现有推理精度、图执行、上下文与多模态能力。

- [补丁拆分与文件清单](PATCHES.md)
- [验证矩阵与交付条件](VALIDATION.md)
- [本次实验基线身份](base.json)

## 1. 基线与边界

长期开发以原项目 `spark-ds41f` 为基础，持续跟进并集成其演进，在自己的 fork 中维护增量补丁。r6v 是本轮修改与验证采用的当前稳定版本，不是 fork 长期固定的开发版本。每轮实验记录明确的源码、补丁和配置身份以便复现；后续集成上游新版本时，重新选择基线、适配补丁并完成相应验证，保留既有实验记录。

本轮实验基线为 r6v `11d31712a5821948f3cbfe14c0ae8419efb22a81`。该版本的 vLLM 输入为 Local Inference Lab 的 `19f2c20ed4d64ae22bd009ceacadd85e2a81e354` 加作者当时的 172 个补丁；本轮沿用原项目选定的 vLLM 集成链。准确身份以 [本轮基线的 lock](../../upstreams.lock.json) 和 [source manifest](../../manifests/sources/2026-10-17-r6v-source.json) 为准。下文具体文件、调用点与支持范围均基于本轮基线核对，上游更新后需重新核对。

这些是公开源基线，不代表本站完整部署配置。未来候选必须继承私有 site overlay 中已登记的上下文、KV、并发、思考/采样、图片预算、主机及网络策略，只对 steering 作明确增量；不能用作者通用 recipe 覆盖本站配置。实际路径、地址、节点资料及访问凭据不进入本计划或 Git。

首版支持范围：DeepSeek V4.1 Flash 1017、TileLang、r6v 的 **V2 runner**、TP4 ring。原配置明确设置 `VLLM_USE_V2_MODEL_RUNNER=1`。未加载 profile 的其他模型、后端与拓扑须保持原路径；选中 profile 的 V1 runner、TP3、PP、DP、DBO 或 KV connector/offload 等未验收组合必须明确拒绝，不能静默降级或通过关闭功能规避问题。TP4 内部预填充 SP 属于首版必须支持的路径。

首个 profile 名拟为 `heretic-1017-attn-v1`：层号为零起算的 2–39，方向宽度 5120，候选 layer alpha 为 2.5。仅对 attention WO-B writer 投影；FFN、shared/routed experts、LM head 和 draft 层无额外投影。旧版 checkpoint、dealign、nohmm、多 profile 组合与运行中热加载另立任务。

**本计划不授权接触线上四机。** 不执行 SSH、sync、模型调用、GPU 实验、启停、重启、镜像加载或主机配置更改。未来单 GPU 和 TP4 验收是独立阶段，需要明确的设备与维护窗口授权；文档里的验证要求不是执行授权。

## 2. 接入架构

沿用现有补丁构建链：

```text
本轮基线的 vLLM 源提交 + 作者 series
                  + 本实验新增补丁
  -> git am -> 校验 patch head / tree / patchset
  -> 现有源码导出与 Docker 构建
  -> 一个不可变候选镜像
```

新增逻辑集中在 `vllm/models/deepseek_v4_1/steering/`；现有文件只增加有界的配置、请求身份、model-state 和 writer 调用。采用显式源码接入，不注册启动时方法替换，不在运行节点手工覆盖 Python 文件。修改后的代码随已有 `vllm-source` build context 进入镜像。

优先利用已存在的接口，避免为了少量控制数据修改通用结构：

| 数据 | 既有接口 | 计划用法 |
| --- | --- | --- |
| 启动配置与编译身份 | `VllmConfig.additional_config` | 提供版本、profile 路径、声明的内容摘要与算子版本；既有 `compute_hash()` 已包含该字典 |
| 请求选择 | `vllm_xargs` → `SamplingParams.extra_args` | 携带 profile 和 scale，不新增 OpenAI 顶层字段或 EngineCoreRequest 字段 |
| KV 前缀身份 | `EngineCoreRequest.cache_salt` | 在首次 block hash 前合成 steering namespace，并保留调用者原 salt |
| 请求生命周期与行映射 | `DeepseekV41ModelState` | 在 `add_request/remove_request/prepare_inputs/prepare_dummy_inputs` 中维护状态 |
| 模型参数 | `model_state.prepare_inputs()` 返回字典 | 通过显式可选 tensor 参数传递固定地址的 row scales，按 CED/SP 同步变换 |

`additional_config` 的具体对象使用 `ds41_steering` key，拟包含 `schema_version`、`operator_version`、`profile_path`、`profile_sha256`。初始化时读取并验证实际文件与声明摘要，失败则停止启动；不能只信任路径或调用方填写的摘要。profile 关闭时不添加这组启动配置。固定路径只作为加载位置，算子身份由内容摘要决定。

通用 V2 runner、`ForwardContext`、`SamplingParams`、scheduler 与 block-hash 算法首选不改。若实现证明现有接口不足，应在对应补丁的评审中说明新增调用点和替代方案，不能以 monkey patch 绕过。

## 3. 请求与数值契约

拟采用已有请求扩展字段：

```json
{
  "model": "deepseek-v4.1-flash",
  "messages": [{"role": "user", "content": "示例问题"}],
  "vllm_xargs": {
    "steering_profile": "heretic-1017-attn-v1",
    "steering_scale": 0.6
  }
}
```

- 省略两字段为 stock；命名 profile 省略 scale 时为 1；有效 profile 配 scale 0 为 stock。
- 未命名的 scale 仅允许 0；未知 profile 即使 scale 0 也拒绝。未加载 profile 却命名请求时拒绝。
- 首版 scale 范围为 `[0,1]`，先检查原始数值范围，再转 FP32；规范化负零，拒绝 bool、字符串、NaN、Infinity、溢出与不匹配身份。错误返回明确的请求错误。
- 请求的有效 profile/scale 在其整个生成生命周期中固定；同一流式会话中改变选择应拒绝。新请求可以变更，但必须使用新 namespace 重算受影响的上下文。
- 不在首版加入网关或 GLM 风格顶层字段适配；保留该适配为独立小变更。

单位方向 `d`、WO-B writer `y` 的目标算子为：

```text
y' = y - (FP32(request_scale) * layer_alpha) * d * dot(d, y)
```

作用点是 attention writer 求和后、进入 mHC 前；不是整层残差加法。TP/SP 的求和顺序和 BF16 舍入边界必须写入算子版本。`RankOrderParts` 不得被误当普通 `[tokens, hidden]` 张量处理。scale 0 不读取方向、不做投影运算，并保留 stock writer 位模式。

理想方向投影与预先重编码的 FP8 checkpoint 是不同数值路径；不能把 scale 1 标记为现有 heretic 的逐位或逐 token 复现。完整 heretic 仅作为独立行为对照臂，保留原有副本直至另行决定。

## 4. Profile 与状态身份

用离线转换工具从固定来源的 `projected[2:40]` 生成单个 safetensors 文件，载荷为 `[38,5120]` FP32（778240 字节）。转换须验证源摘要、键、形状、有限性和单位范数；辅助均值不进入运行载荷。转换在隔离的本地环境进行，不在服务请求路径反序列化源文件。

profile manifest 绑定基础模型 revision、可信权重清单摘要、tokenizer/实际模板身份、层计划、alpha、算子版本、方向摘要和来源/许可。加载器只接受已验证的本地 artifact，校验长度、类型、层序、重复层、有限值及 unit norm；启动不隐式联网下载。每 rank 在模型绑定、kernel preparation 和图捕获前加载同一身份。

每 worker 的方向和 scratch 在启动时分配；使用固定容量的请求强度及 token-row scale buffer。方向按层存储，scale 按请求/行更新，改变连续强度不分配新的方向槽位。不使用全局可变的“当前强度”。

区分两种缓存：编译缓存绑定启动时的实现/profile 身份；KV/CED/前缀状态绑定请求的完整有效身份。stock 和有效零强度保持原 stock namespace。静态权重与由 token 决定的 Engram 表无需复制；受激活影响的缓存状态不能跨身份复用。

## 5. 实施顺序和决策点

先完成基线复现，再按 [P1–P7](PATCHES.md) 逐项实现。P1–P3 只建立合约、身份与控制数据；P4 用独立 GPU op 建立数值参考；P5 完成 DSpark/图路径；P6 只在剖析证明必要时融合；P7 封装候选配置、构建身份与 release gates。P4 原型不是可推广版本。

任何阶段都不能靠关闭 prefix cache、DSpark、图执行、SP 或降低上下文/精度得到“通过”。功能关闭、profile 加载但零强度、有效 steering 三种状态分别验收。未通过四机阶段前只允许标记“本地/单 GPU 已验证”，不进入正式配置。

上游升级时，在同步分支合并部署仓库更新，先重建作者的新基线，再 rebase 我们的独立 vLLM 提交并重新导出补丁。比较 `git range-diff`、最终源码 tree、所有受影响 gates；已共享维护分支不强制改写历史。作者已有补丁和不可变证据文件保持原样。

本项目采用独立的下游维护策略：本站定制在自己的 fork 中开发、评审和发布，上游更新经验证后纳入，使本站需求与上游的功能规划、维护范围及发布节奏保持独立。原作者仓库作为只读集成来源，默认不向其推送分支或发起补丁 PR；未来如有上游贡献需求，应另行明确范围并获得授权。保持小补丁是为了便于下游维护；若上游自行加入等价能力，可验证后采用并移除冗余本地补丁。

## 6. 本次规划交付

本次新增本目录的计划与 `base.json`，并在 `AGENTS.md`、`CONTRIBUTING.md` 中明确下游维护约定；不创建空补丁、不可用的 `series` 或伪造候选 manifest，不修改正式 lock、recipe、Dockerfile、源码或 release gate。

P0 的本地 CPU/源码复现已通过，构建工具修复独立见 [PR #2](https://github.com/imDaniel/spark-ds41f/pull/2)（提交 `9635927`，已合并至 `homelab/main`）。macOS 和 Linux 从空源码目录完整重放 172 个 vLLM 补丁，head/tree/patchset 与本轮身份完全一致，导出 context 摘要相同；macOS 完整构建输入准备、`build check`、静态 `doctor` 及 418 项 CPU 测试（5 项跳过）通过。原 0154 失败及本轮 0103 复现均保留；Git trace 显示后台自动 repack 的锁冲突和 pack 缺失，修复只在临时源码仓库内关闭自动维护。后续实现分支须包含该修复；这些结果不代表 steering、镜像、GPU 或 TP4 已通过验收。
