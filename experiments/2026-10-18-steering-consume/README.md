# Steering 关闭路径与普通 writer 消费融合候选

<!-- Keep tables and pinned paths on single lines. -->
<!-- markdownlint-disable MD013 -->

本轮从 `homelab/main` 的 `38e3b4f6ea0beb8625289394b347ab8f74f7b868` 开始。该部署分支已集成当时的上游更新；vLLM 验证仍使用本轮固定的 r6v 来源及已有 steering 系列。这是当前实验的可复现基线，后续继续随原项目集成上游开发。

状态：待审查、未合并候选。真实四机验证已完成 loaded/unloaded 显式 KV 预算启动、FULL/DSpark 图重放、API/生命周期、父版/官方位一致性、关闭路径 trace、TPS 与单请求精确 1M。独立重启后的 scale=0 吞吐与官方差异约 −0.02% / +0.11%（C1/C4），本组固定步耗时约 +0.08%，但 C4 首 token 均值 +6.30%、p90 +36.15% 的短样本差异未解释，尚未完成全指标或 ±1% 等价验收。非零路径的结果与测量限制见 [硬件结果](../2026-10-19-steering-gpu/RESULTS.md)，不能把短窗口成绩扩展为全负载零损耗。PR 继续 Draft，候选未推广；现有配置、权重、profile 保留。CPU 与重放历史见 [VALIDATION.md](VALIDATION.md)。

## 本轮变量

只改变 steering 的执行路径。请求契约、profile 格式、`heretic-1017-attn-v1`、attention-only 层范围、profile alpha=2.5、请求 scale 范围 `[0,4]`、精度、采样、KV 与 1M 容量配置均沿用父版本。

| 批次/输出形态 | 候选行为 |
| --- | --- |
| 未加载 profile | 原版模型输入和 kernel 路径 |
| 已加载、实际调度请求均为 STOCK | 不展开控制行、不调用 projection/dot kernel；保留 Tensor/RankOrderParts 及 stock mHC ABI |
| 含非零请求，普通 BF16 writer | 一个 compensated dot kernel 产生每行两个 FP32 分量；投影更新并入已有 mHC register fold，不改写完整 writer |
| 含非零请求，SP RankOrderParts | 保留原 projector 和既有 BF16 中间缓冲，避免再次读取四份 partials |
| 混合批次中的零强度行 | 消费器在读取方向和 dot 之前走 stock 算术分支 |

STOCK 根据已经校验和 FP32 规范化的请求选择判定，使用实际调度的 request IDs；其他驻留请求不影响本批路由，也不根据 SP 某个 rank 的局部 rows 判定。未知 profile、非零但缺少名字、越界/非数值等 admission 错误继续报错。

FULL CUDA graph 增加两个有界输入拓扑：0=stock，1=active。强度存放在固定地址张量中，不按 scale 建图。捕获和图内存采样均覆盖两条路径；实际调度与 ModelState 使用同一规范化身份。dummy 的捕获拓扑显式指定，不能凭零填充强度猜测。非零 PIECEWISE 输入暂时执行 eager，避免复用 stock 的分段缓存；stock PIECEWISE 保留原行为。当前四机测试配方使用 FULL。

非零普通 writer 仍有一个额外的 dot launch。此候选没有实现每层零新增 launch；GPU 数值、图用例和整模型 TPS 已测量，完整数据见硬件实验。SP 维持既有实现：4H 读取加 H 写回/读取，比两个阶段各读 4H 更节省大批次流量。

## 补丁边界

完整 [vLLM series](vllm/series) 保留原来的 172 个来源补丁与 8 个 steering 补丁，再追加四项；0189/0190 保留原内容，启动修复单独追加，此前实验的 series 和身份不改写。

| 补丁 | 文件与目的 |
| --- | --- |
| [0189](vllm/0189-stock-route-and-consumer-fusion.patch) | `steering/runtime.py`、`steering/ops.py`：关闭路径、生命周期、dot producer、SP 路由；新增 `steering/consume.py`：消费端补偿投影公式 |
| 0189 | `nvidia/model.py`、`model_state.py`、`vl_model.py`：显式输入拓扑、固定缓冲、模型/图准备钩子与 warmup 声明 |
| 0189 | `tilelang/mhc.py`：复用同一 kernel body macro，保留 stock ABI，新增普通 writer 的 consume specialization；TileLang 运行期张量声明单独排除 Python 的 `valid-type` 误判，其余 host 代码继续类型检查 |
| 0189 | `gpu/cudagraph_utils.py`、`model_runner.py`；新增 `gpu/model_input_variant.py`：FULL 身份、捕获、每拓扑内存抽样和 eager fallback；runner 只增加两处薄接点 |
| [0190](vllm/0190-stock-and-consume-validation.patch) | CPU 生命周期与真实 host 钩子、图路由、FP64 oracle；新增实际 TileLang 的 101 次强度变化 CUDA graph 回归用例 |
| [0191](vllm/0191-preserve-manager-less-profiling.patch) | 保留首次 profiling 的 manager-less eager/stock descriptor；运行完整源码 `profile_run → _dummy_run → execute_model` host 方法体，覆盖已加载/未加载 profile 两条 runner 路径；KV 预算参数不构成额外 worker 分支覆盖 |

| [0192](vllm/0192-consumer-region-stores.patch) | `steering/consume.py`：在各条件分支内直接写 BF16 fragment，修复真实 TileLang 编译时 immutable value 逃逸区域；不增加 kernel、缓冲或运算边界 |

普通 writer 的更新保留父版本 compensated FP32 dot、显式 FP32 运算和 BF16 writer 舍入边界。SP 不改变 rank-order 求和顺序。静态 alpha=0 层继续不绑定方向。无新增每步 CPU/GPU 往返或跨 rank 协商。

8192 token 容量下，dot 缓冲为 64 KiB。开启 unreduced SP 时保留原来的 80 MiB BF16 缓冲；关闭该 SP 模式时不分配它。两套 FULL 图的实测 allocated/reserved、捕获日志差值与单请求 1M 结果见硬件实验；不同内存口径不可混用，单请求通过不代表四并发容量。

## 验证与后续边界

新增 CI job 保留旧 steering 重放 job，独立重放本候选，并使用 [hash-pinned Linux CPU 依赖](../../requirements/steering-cpu-linux.txt) 在精确 prepared tree 上运行 [CPU gate](../../scripts/check_steering_cpu.py)。gate 清除外部 `PYTEST_ADDOPTS`/插件、关闭插件自动加载并覆盖配置的 `addopts`，按独立提交的 [108 项完整用例清单](cpu-cases.json) 核对 JUnit 身份与计数。缺失、重复、替换、失败、错误、跳过或零执行均不能通过；不在运行时从筛选后的收集结果生成预期集合。

首次 profiling 发生在 `initialize_kv_cache` 创建图管理器之前。runner 仅在 manager 已存在时选择额外图拓扑；manager 为空时沿用既有 dispatch 的 eager 契约，不调用空对象，也不移除非 profiling dispatch 对 manager 的要求。CPU 生命周期回归替换设备/分配器/连接器边界与独立 attention-cache profiling，不能等同于真实 GPU 初始化链验收。

0191 的四个参数 case 只覆盖已加载/未加载 profile 两种不同 runner 路径。`explicit_kv_bytes` 没有被被测方法读取；默认/显式 KV 预算的实际分支属于 worker 内存探测，当前回归不覆盖该分支。保留原补丁作为审查记录，并在此收窄其覆盖声明。gate 的失败/错误/跳过负例则使用完整 108 项身份清单，只改变结果状态，独立验证状态拒绝逻辑。

前期已运行本地 CPU 与源码检查。授权 GPU 窗口继续比较未修改父版、候选无 profile、候选已加载但 scale=0、非零普通 writer 与 SP；固定同一权重、工作量、DSpark、精度、容量和采样，检查 stock 位一致性、FP64 数值门槛、图反复重放、launch/读写/同步/内存和 TTFT/TPOT/TPS。先确认关闭路径，再量化普通 writer 融合；SP 的进一步融合需要同时减少 partials 访问并保持舍入边界。

本轮沿用 DSpark 调度策略；候选使用独立缓存目录，后续须在候选中生成自己的成本曲线。双路径成本归属、实际图内存以及完整 1M/并发容量仍需与 GPU 性能验收一并核对。本地成果只在自己的 fork 中维护，不向原作者或来源组件提交。

## 初始验收计划（历史）

以下保留前期安排；实际完成项、未覆盖的默认 KV 分支与性能边界以硬件结果为准。

1. 启动与 gate 回归先在 CPU 通过；真实硬件启动仍需覆盖未加载/已加载 profile、默认/显式 KV 预算。
2. 使用独立父版 180 补丁的 stock mHC/模型作为基准，对照候选 stock 位一致性与真实 CUDA graph 重放。候选内的独立 projector 对照不能检测两侧共享的 mHC macro 回归。
3. 关闭路径 trace 分别证明：无控制行展开、无额外 projection/dot launch、SP 无新增中间张量物化；同时覆盖真实 CED/SP/DSpark 集成。CPU fixture 未覆盖这些组合，真实 OpenAI 请求类目前也仍需完整 serving 检查。
4. 单独记录双拓扑图的常驻内存、捕获时间及容量代价，再在相同负载下测 TTFT/TPOT/TPS。保留 1M 配方不等于通过 1M 验收。
5. 上述证据齐备后，再评估复用单次准备中已校验的身份，减少重复 CPU 扫描；必须保留请求/槽位一致性检查，当前不把它认定为主要瓶颈。

上述计划对应最初冻结时点；现已完成四机测试范围见硬件实验，PR 保持 Draft。

## GPU 验收的冻结条件

父版固定为 180 补丁的 `eddd508799a2`（tree `5de1eabc3f19`），初始候选 183 补丁的 `75a3773b56a3`（tree `c035b638c729`）在真实 GPU 发现 TileLang 编译错误。保留其失败记录，单独追加 0192 后，当前候选为 184 补丁的 `43608a373a5d`（tree `efcbb1ef861a`）。两侧镜像分别记录 OCI digest 与完整输入身份，不用候选自己的 stock macro 代替父版 oracle。

| 验收阶段 | 最小对照与证据 | 通过条件 |
| --- | --- | --- |
| stock 内核及真实启动 | 两侧独立 mHC 实现；普通 BF16/RankOrderParts，代表性 decode/verify/prefill rows；已加载/未加载 profile；真实 worker 默认/显式 KV 分支 | 编译与启动成功，stock 输出位一致；先通过此项再做性能测量 |
| 图与关闭路径 | 固定输入重放；scale 从非零切回 0，再切回非零；记录实际图选择、launch 和张量物化 | 不误用 stock/active 图；全 STOCK 批次没有控制行展开、dot/project launch 或新增 SP 中间张量 |
| 内存和容量 | 记录捕获前后 allocated/reserved、图池常驻内存、捕获耗时与实际 KV 容量；单拓扑/双拓扑同配方对照 | 单独报告双拓扑代价；完整 1M 请求未通过时不宣称容量验收 |
| 模型与 TPS | 同权重/profile、采样、DSpark、CED/SP、容量和负载；先短 decode 与一段 8192-token prefill，再按结果扩展 | 保留 stock 位一致性证据；TTFT/TPOT/TPS 同条件配对并报告离散程度，不能用短样本宣称稳定恢复 |

每阶段保存原始结果及失败记录；不以 TPS 覆盖正确性或容量失败。已完成的整模型范围与未覆盖项见硬件实验结果。


## 2026-10-19 硬件窗口进展

初始 c035 候选在 SM121 运行 29 个 GPU 用例，25 通过、4 个消费融合图用例编译失败。0192 修复区域内变量写入后，精确 efc 候选 **29 passed**；101 次强度变化图重放包含在这组用例中。独立父版与候选镜像各自编译实际 stock mHC，12 组 pre/broadcast/post-pre、world=1/4、rows=1/96/8192 的输入、输出逐位一致。该微基准的时间只作诊断，不能代表模型 TPS。

隔离的四机测试配方、镜像身份和验收清单见 [steering-gpu](../2026-10-19-steering-gpu/README.md)。原有配置、权重与 profile 不改写；原服务恢复及指纹核验见硬件结果。CPU/重放历史与新增硬件证据分别记录，任何未完成项不报告为通过。

当前性能结论与可复核证据以 [硬件结果](../2026-10-19-steering-gpu/RESULTS.md) 为准：API 观测指纹不等于整模型隐藏张量位一致；命名 steering 事件检查不等于通用 copy/SP 物化证明；C4 首 token 延迟差异仍未解释。
