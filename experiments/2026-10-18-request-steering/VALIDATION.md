# 验证矩阵与交付条件

所有者已授权独立测试配置的 GPU、模型及四机验收；现有配置和官方权重保持原样。以下矩阵是通过要求，不是完成声明；当前成绩见 [IMPLEMENTATION.md](IMPLEMENTATION.md)。测试报告须区分 CPU 合约、单 GPU 数值/图检查、TP4 实机验收和持续运行观察。

## G0 基线和文档检查

| 检查 | 本次状态 | 含义 |
| --- | --- | --- |
| 本轮 r6v 实验基线的配置与源码调用点核对 | 已核对 | V2 runner、ModelState 扩展点、现有 additional_config hash 与请求 extra_args 路径；上游更新后重新核对 |
| 隔离副本使用仓库示例节点，`bin/spark --shape tp3-triangle doctor` | 通过 | 本地静态配置检查；不是 TP3/TP4 设备验收 |
| 全新构建树 `build prepare --only vllm` | 修复后通过 | macOS/Linux 各自抓取并重放全部 172 补丁，head/tree/patchset 与原 manifest 一致，导出摘要相同 |
| 完整 `build prepare` / `build check` | macOS 通过 | 全部源码、递归 submodules、锁定 wheels 和 context 摘要核对；未构建镜像 |
| 计划链接、base 身份、`git diff --check` | 通过 | 只证明本次文档交付的链接、身份和差异检查 |
| CPU suite（含 P0 新增回归）| 通过：418 项，5 项跳过 | 隔离副本、完整运行；不代表 steering 已实现或 GPU 路径通过 |
| steering patch replay / CUDA / TP4 | 持续验证中 | 各候选分别记录；当前成绩见 IMPLEMENTATION.md，不能沿用前一镜像结果 |

G0 的本地基线重放阻断已解决，构建工具修复见 [PR #2](https://github.com/imDaniel/spark-ds41f/pull/2)（`9635927`，已合并至 `homelab/main`）；后续实现分支须包含该修复。macOS 原 helper 再次于 0103 失败，Linux 原 helper 通过；trace 捕获重复后台 repack、锁冲突和 pack 缺失，修复在新建临时源码仓库中设置 `maintenance.auto=false`、`gc.auto=0`。修复后的 macOS/Linux 完整重放与导出一致，macOS 无自动 maintenance 子进程，Git connectivity 检查通过。未证明 Git 内部具体缺陷，不宣称所有 macOS 版本有问题。Linux 检查使用本地 ARM64 容器，不是 GitHub CI。原始失败及诊断日志随独立修复保留；未改作者补丁、lock 或旧 manifest。未来 steering 候选仍须在干净树上重放并校验完整身份。

CPU suite 的首次运行有 1 个 failure 和 5 个 error：临时修改 recipe 的 master 地址破坏 release-arm 相等断言，且缺少忽略的 ring4 节点文件。仅在隔离副本恢复原 recipe、用 `config/nodes.example.json` 与 `config/examples/nodes-ring4.json` 建立忽略的节点文件，并将示例 head 地址与 recipe 对齐后，相关 98 项及完整 417 项均重跑通过。没有改测试或正式配置；两次日志均保留在本地检查目录。

实现后复用已有命令入口，候选配置文件实际存在且只读检查通过后才能使用；候选入口如下：

```sh
bin/spark --cluster-config experiments/2026-10-18-request-steering/cluster.json doctor
bin/spark --cluster-config experiments/2026-10-18-request-steering/cluster.json build prepare --only vllm
bin/spark --cluster-config experiments/2026-10-18-request-steering/cluster.json build check
python3 -m unittest discover -s tests
git diff --check
```

`build check` 只有完整构建输入准备好才可算通过；`--only vllm` 成功不能代表其他输入完整。release test 分类复用 `scripts/release_tests.py check VLLM_REPO BASE HEAD`，每个新增测试必须纳入 gate 或明确说明分类原因。

## G1 CPU 合约与路由

| 主题 | 必测用例 | 通过标准 |
| --- | --- | --- |
| Artifact | 正常/损坏摘要、截断/尾随数据、错 dtype/shape、重复/越界层、错误模型/模板、非有限/非单位方向 | 错误明确失败，不修改源文件，不尝试联网 |
| 请求 | 缺省、命名默认强度、正负零、边界、越界、bool/string/null、NaN/Inf、未知 profile | 合法值规范化一致；非法值为明确请求错误，不静默 stock |
| KV 身份 | 相同 token 下 stock/scale0、不同 scale、等价 FP32 scale、不同 profile bytes、原用户 salt | stock 身份原样；异身份不匹配；等价身份可匹配；用户隔离保留 |
| 生命周期 | 插入、取消、重用、preempt/resume、同 ID 更新、streaming update | scale 无残留，中途改变身份拒绝或强制新请求重算 |
| V2 行布局 | decode-first 重排、混合 prefill/decode、ragged verify、空批、padding、CED gather、SP slice | 每个真实 target row 精确对应其请求；dummy/draft/padding 为零 |
| 配置与编译缓存 | 同路径变内容、错误声明摘要、算子版本改变、profile on/off | 内容错配阻止启动；不同图语义不会复用旧 compile/AOT cache |
| 支持边界 | 选中 profile 的 V1、未验收拓扑/并行/connector 配置 | 启动或 admission 明确拒绝；未选 steering 的原支持路径不变 |

测试用预置小数据和固定随机种子，不下载模型。namespace 测试必须实际调用 block hashing/prefix match 和相关 CED 状态路径，不能只比较两个字符串不同。

## G2 单 GPU 数值与图

需明确授权的隔离 GPU，合成张量不等于真实模型验收。比较未改动 parent、独立 projector 和融合候选三条路径。

- 数值：FP64 scalar oracle 定义投影；普通 BF16 writer 与 rank-order partials 各自建模既有舍入边界。测所有 shape bucket、层边界、scale 0/0.25/0.5/0.75/1、near-collinear、极值和 NaN 传播。
- 初始 FP32 dot/未舍入投影阈值为 `atol=1e-4, rtol=1e-4`；BF16 比较按 oracle 舍入后不超过两个本地 ULP。所有 case 记录最大误差/输入尺度；若极值需调整容差，必须在独立数值评审中说明，不能为绿灯随意放宽。融合与独立实现要求同一数值契约，stock writer 逐位相同。
- 确定性：同一 token 在不同 batch size、row bucket、stock/steered 邻居和重复运行下保持相同投影结果；核对固定 reduction 顺序。
- Graph：实际启用 FULL 和 PIECEWISE，至少 100 次重放；每次改变有效 rows、scale、取消/复用顺序。核对 buffer `data_ptr`、输出、guard/scratch 和 graph capture/compile 计数。
- 启动：profile 在 preparation 前绑定，所有新 kernel 声明、warm-up 后 freeze；首次非零请求和首次新强度不能 JIT、分配或重新捕获。读取算子实际输出，不能只验证“请求返回成功”。
- Trace：分别记录无 profile、profile 已加载但全零、mixed、all-steered 的 launch、copy、allocation、同步和耗时。原型额外开销如实记录；融合消除 launch 后仍测 occupancy、寄存器、scratch 和同步成本。

## G3 TP4 协调验收

仅在获得四机维护窗口后，通过现有协调器执行。先保存可回退的固定配置/镜像身份、四 rank 日志和资源基线；四 rank 使用同一候选 digest、配置/算子版本和 profile 摘要。比较身份复用现有控制面/启动 rendezvous；不得新增每 token 跨机同步。任何 rank 不一致都必须在 readiness 前失败。

冻结以下对照臂，使用同一 1017 官方权重、模板、采样和容量：

| 臂 | 用途 |
| --- | --- |
| A 未修改的本轮上游基线（本轮为 r6v）+ 官方 1017 | 功能与性能 parent |
| B 候选、无 profile | 检查关闭功能的原路径 |
| C 候选、已加载 profile、scale 0 | 检查 idle、stock 分支与缓存 |
| D 候选、非零强度及混合批次 | steering 数值、行为与性能 |
| E 原有完整 heretic | 单独行为对照，不要求与 D 的输出 hash 相等 |

E 的运行需单独协调模型切换，不把其结果当 A/D 之间的同权重性能差异。基线与候选不同时占用同一 GPU。后续上游升级时，A 随本轮选定的上游基线更新，B/C/D 使用相同 parent 重新比较，不长期沿用 r6v 的成绩。

| 类别 | 覆盖 | 通过标准 |
| --- | --- | --- |
| 功能/质量 | 思考、中文流式/usage、工具往返、程序执行、严格输出、图片、长输入、多轮；独立冻结的行为集 | A/B/C 的确定性用例一致；D 的预注册评分单独报告，不能只用“输出改变”判定有效 |
| Prefix/CED | 同 prompt 交替 stock/不同强度，冷/热缓存，短/长输入，流式续接、取消/恢复 | 跨身份零错误复用；同身份命中且等于冷算结果；无旧 CED/私有页泄漏 |
| DSpark | on/off、各 draft/verify 长度、接受/拒绝 suffix、bonus token、greedy 和正常采样、C1/C4/16 | greedy committed token 与关闭推测的同 profile 目标一致；非零温度验证冻结 logits/分布与 sampler 合约，不要求随机文本相同 |
| 图/准备 | FULL/PIECEWISE、warm/cold boot、单 rank/all-rank 空 JIT cache | 正确完成 prepare；serving 无 JIT/recapture；重复改变 scale 输出正确 |
| 资源/故障 | 四 rank mismatch、错误 artifact、缺文件、超时；available memory、swap、KV、Xid/OOM、错误率 | readiness 前拒绝坏配置；无新增 Xid/OOM/错误；内存余量与容量不退化 |

first-token logprobs/固定序列 KL、代码正确率、工具成功率和行为指标分别记录，D 不要求与 A 文本一致。D 的能力保持与行为有效阈值须在看结果前冻结，使用独立于方向提取的集合；该质量 gate 未定义/未通过则不推广。少量 smoke 不能代替此 gate。

## G4 性能与持续观察

同硬件、同原生精度/采样、同 DSpark 参数和 cost policy、同上下文/KV/图片预算、同 OEM 主机策略；不修改风扇、功耗、内核、网络来掩盖差异。使用同一热起点，交错 A/B/C/D 顺序，至少两个独立 boot，每点至少三次正式运行，保留原始成功/失败样本及区间。候选独立生成/归属成本曲线；为算子 A/B 另冻结调度选择，避免混淆收益。

矩阵含 code/prose C1、C2/C4/16、短输出/长输出、8K/32K/64K/128K 预填充、prefix hit/miss、混合强度及 step 延迟；在宣称保留完整容量前补齐该配置已有的 1M/长并发边界验收。分别报告 TTFT、纯 decode TPOT/TPS、端到端完成吞吐、接受长度和 host/device memory。输出 token 包含思考，不用 SSE chunk 数作为吞吐。

拟定推广门槛：B/C 相对 A、D 相对 C 的主要点均无可重复的 >3% 退化；性能区间有歧义则补测并保持候选，不能以噪声解释已复现的回退。profile 行为导致的输出长度差异另用 teacher-forced/固定工作量隔离，正常生成仍报告用户可见端到端成本。若保不住此目标，只交付可研究原型或重新讨论取舍，不自动放宽。

有限窗口验收通过后，另行授权持续观察：至少 24 小时的代表性混合负载，记录控制状态、缓存、接受率和资源趋势；这只证明该观察窗口，不宣称长期稳定。持续观察的实际起止时间和负载另行记录；不以短样本代替。

## G5 推广、回退与上游同步

推广需要完整 identity、各 gate 结果、owner 接受以及一个提交更新候选决策、正式配置和新不可变 baseline。所有未覆盖项必须显式保留，不能用测试跳过计作通过。

回退使用已保留的固定 parent image/config，通过同一四机协调器恢复；不得混跑 ranks。隔离或丢弃候选的激活相关缓存，保留失败日志；stock/steering 不能靠沿用原 KV 直接互换。权重与共享 inode 始终只读，不在此任务删除完整 heretic 或其他模型。

上游更新时先验证新作者基线，再按依赖 rebase P1–P7，导出补丁、比较 range-diff、重建 source manifest 和一个新 digest。改了 kernel/graph/startup 必重做 G2/G3/G4；仅文件搬迁也要重放和路由测试，不以“补丁能应用”代替验收。
