# 四机 steering 性能与正确性验证

<!-- markdownlint-disable MD013 -->

2026-10-19，TP4 ring、官方 1017 权重、显式 10.5 GiB KV、1,048,576 上下文、16 sequences、8192 batch、DSpark 5/adaptive。候选为 184 补丁的 `43608a373a5d`，tree `efcbb1ef861a`；原版为固定的 172 补丁，旧 steering 父版为 180 补丁。镜像、配方和完整 source identity 见 [README](README.md)。

本轮**关闭路径吞吐接近原版，尚未完成全部延迟指标或 ±1% 等价验收**：独立重启后的 C1/C4 为 **68.52 / 134.70 TPS**，官方为 **68.53 / 134.56 TPS**（−0.02% / +0.11%）。十一形状 rank0 步时几何平均为 +0.08%；按四 rank 最慢值汇总约 +0.084%，最差单形状约 +1.10%。均值不能当作上界。

C4 首 token 延迟仍有未解释差异：均值 **393.4 对 370.1 ms（+6.30%）**，p90 **542.9 对 398.7 ms（+36.15%）**，TPOT 均值略好（−0.29%）。各仅 48/49 个请求，尚不足以判断是否存在稳定代码回退，但不能用 TPS 接近代替延迟验收。PR 保持 Draft，候选未推广。

## 正确性与 trace

- 独立父版/候选各自编译的 stock mHC：12 组输入输出位一致，真实 CUDA graph replay 通过；不是拿候选自己的 stock macro 作唯一 oracle。
- 当前候选 SM121 GPU suite：29 passed，包含同一捕获图中的 101 次强度变化。首次 c035 的 25 pass/4 编译失败保留；0192 只修复条件区域内的 BF16 写入。
- 四个 arm 的三组 stock 请求，每组 64 输出 token，token IDs、文本、API top-5 logprobs 与 finish_reason 的规范 JSON 指纹一致；候选/父版的 1→0→4→0→1 序列也一致。这里只比较 API 观测，不宣称整模型内部激活张量位一致。独立 mHC 字节 SHA 对照是另一层证据。
- 已加载/未加载的 API 合约、隐式 scale=1、未知 profile、缺少 profile 的非零请求、零强度恢复、混合、取消和槽位复用、实际 8K CED/SP 混合 prefill 通过。
- 四 rank 的关闭路径 prefill/decode trace：采样中命名的 steering custom op、row、dot、project kernel 均为 0；不能据此独立排除通用 copy/launch 或 SP 中间物化。旧父版在相同零强度 8K prefill 仍有每 rank 2 个 row kernel、76 个 projector kernel。
- 候选非零普通 writer 的更新已融入 mHC，decode 中仍有每层一次 compensated dot，绝不宣称零新增 launch；SP 保留原 projector。prefill/decode 的 kernel 总数受实际步数影响，不能直接当作单步成本比。
- 1,048,568 输入 token +8 输出 token 的实际请求在 scale=1 完成，随后 stock 输出位一致。全部 rank 无 OOM/分配重试。此项只验证单请求恰好 1M，不代表四个并发 1M。

## 吞吐与固定工作量

实测表格由 results.json 生成，所有窗口保留。每项先检查四机温度低于 55°C，固定 16 条合成默认 prompts、4096-token 文档、256 输出、temperature=0、seed=2048；C4 前缀缓存命中率约 94%，这是暖文档负载，不能描述为每请求冷 4K prefill；15 秒 warmup，90 秒/6 个窗口，C1/C4。所有负载无失败、外来流量或抢占。首尾零强度窗口都保留，不选择最好结果。

| 实现 / 请求 scale | C1 TPS（相对 CI95） | C4 TPS（相对 CI95） |
| --- | ---: | ---: |
| 官方 172 / 0 | 68.53（±2.99%） | 134.56（±2.18%） |
| 旧 steering 180 / 0 | 68.27（±0.81%） | 135.80（±1.32%） |
| 旧 steering 180 / 1 | 67.91（±1.77%） | 133.03（±1.05%） |
| 旧 steering 180 / 4 | 67.44（±6.02%） | 131.63（±2.39%） |
| 候选无 profile / 0 | 68.59（±2.36%） | 132.64（±2.01%） |
| 候选 loaded 首轮 / 0（开头） | 68.59（±2.74%） | 134.96（±1.74%） |
| 候选 loaded 首轮 / 1 | 67.84（±1.76%） | 134.81（±1.15%） |
| 候选 loaded 首轮 / 4 | 67.54（±6.03%） | 132.32（±2.12%） |
| 候选 loaded 首轮 / 0（末尾） | 68.54（±2.07%） | 134.92（±1.42%） |
| 候选 loaded 独立重启 / 0 | 68.52（±2.04%） | 134.70（±1.25%） |

scale=1/4 改变激活和生成轨迹，因此与原版比较 TPS 时包含投机接受率变化。候选第一次测量 C4 从 scale=0 的约 8.94 tokens/step 到 scale=4 的约 8.81；约 2% 的 TPS 降幅不能全部归给 kernel 开销。同一进程固定形状的 scale=1/4 相对首次 0，整体 step 中位数几何平均分别约 −0.37% / −0.21%；末尾 0 约 +0.52%。这说明本组固定工作量未测到随非零强度增加的明显 step 开销；不能据此保证所有实际请求 TPS 损失都小于 1%。

跨启动的历史十一形状 grid 曾出现 loaded 相对 official 约 +1.4%～+1.9%，unloaded 约 +0.03%，父版约 +0.55%；候选独立重启后为 +0.08%。补充的 context=0、每请求仅当前一个 token 的控制见下表。KV 历史、MoE 路由与启动状态都可能影响比较，现有数据没有单独确定早期偏移的原因；保留全部结果，不能只报告最后一组。

| 当前 token 数 / 并发 | 官方 step 中位数 ms | loaded 候选 scale=0 ms | 差异 |
| --- | ---: | ---: | ---: |
| 1 / C1 | 16.4214 | 16.4639 | +0.26% |
| 4 / C4 | 18.0916 | 18.1448 | +0.29% |
| 16 / C16 | 23.8812 | 23.8454 | −0.15% |

每形状 100 个测量回放、5 warmup、1 settle，三形状按同一轮交替；该控制减少历史 KV 影响，不替代上面的真实吞吐与 trace。

原始四 rank 每次 step/forward/drafter 样本及图模式在 [reference-engine-samples.jsonl](reference-engine-samples.jsonl)，首轮候选另有 [candidate-engine-samples.jsonl](candidate-engine-samples.jsonl)。[results.json](results.json) 提供 TTFT/TPOT、TPS 分布、tokens/step、接受率、prefill、控制缓冲、trace SHA 和每份原始回执的 SHA。`candidate-results.json` 保留早期候选窗口，不覆盖为最终数据。

同 profile/scale 的独立父版对照：候选 scale=1 的 C1/C4 吞吐差异为约 −0.10% / +1.34%，scale=4 为 +0.15% / +0.52%。未见明显新增吞吐下降，也不把这些短窗口变化解释成稳定加速。

## 图内存、启动与容量

目标 FULL 图：官方/无 profile 候选/旧父版各 30，loaded 候选 60（stock/active 各 30）；draft/context 图数量不变。allocated/reserved 是 PyTorch 口径，启动日志的捕获前后差值是另一口径，不能相互替代，也不是独立测得的 driver 图池常驻内存。

| rank | loaded 对旧父版 allocated | 首轮 reserved 差值 | 独立重启 reserved 差值 | loaded 对 unloaded allocated |
| --- | ---: | ---: | ---: | ---: |
| 0 | +94 KiB | +6 MiB | -2 MiB | +80.87 MiB |
| 1 | +94 KiB | +2 MiB | -2 MiB | +80.87 MiB |
| 2 | +94 KiB | +4 MiB | +2 MiB | +80.87 MiB |
| 3 | +94 KiB | +0 MiB | +2 MiB | +80.87 MiB |

以上统一使用 smoke 开始前的 state RPC：首轮 loaded 对父版 reserved 多 0–6 MiB/卡，独立重启后为 −2～+2 MiB/卡；保留两次值，不能把不同采样时点或 allocator 波动说成固定成本。

首轮日志：旧父版图捕获 32 秒、报告 1.16 GiB；loaded 候选 31 秒、报告 1.48 GiB；unloaded 候选 31 秒、报告 0.15 GiB。前两项日志差约 0.32 GiB；loaded 候选独立热重启又为 10 秒、0.90 GiB。存在冷/热缓存及采样基点差异，不把这些值说成双图拓扑的纯常驻成本。冷启动包含首次编译约 344–346 秒，热重启约 94–127 秒，不能拿冷热启动时间作性能收益。实际 1M 请求及随后正常请求已验证当前显式 KV 配方的单请求容量。

## 验收工具补强与可复核证据

[证据包与复核命令](evidence/README.md) 提供 12 份 stock API 观测、独立 mHC driver/逐例输入输出 SHA、当前 29 项 GPU JUnit/原始日志、初始编译失败日志，以及 16 份脱敏 trace 的全部非身份元数据事件。原始 trace SHA、公开文件 SHA、rank 与阶段均在 manifest 中。只移除主机/UUID/身份 metadata，不删 CPU args/shapes、CUDA runtime、copy、内存或流事件。

- 指纹前强制验证实际 token 数、usage、token IDs、完整 top-5 结构和 length 结束；79 个保留成功请求重新验证，33 个原存指纹不变。缺失/null/截短不能共同“通过”。
- collector 的 HTTP 成功只记 `capture_succeeded`，`trace_acceptance=not_evaluated`；独立解析器验证全部十六个 node/stage/scale、rank/world、SHA、非空 CPU/GPU 事件、decode 图回放和命名的禁止事件。它不把通用 copy/SP 物化记为已验收。
- 每次运行使用唯一目录；指定旧 run ID 在发网络请求前失败。温度 gate 核对四个不同预期节点、非未来且小于 20 秒的记录及有限数值。
- 本次没有新 GPU 执行或修改 184 项运行时补丁。没有补造缺失的 official/unloaded 匹配 trace 或跨 arm 长输入 oracle；这些仍未验证。六个相邻窗口不是六次独立启动，现有 CI 不能证明 ±1% 等价。

## 验证链路与边界

- 精确 efc tree：macOS/ARM CPU gate 各 108 passed，完整身份清单一致、零 skip；实际 OpenAI 请求类 CPU 检查通过。
- 完整 184 补丁重放、head/tree/patchset、最终 diff 和 build inputs check 通过。保留 0189/0190/0191，追加 0192。
- 部署仓库 438 tests 通过；e428679 的 Actions 10 jobs 全成功，实际日志确认 108 CPU/438 部署测试及 efc tree。后续仅整理证据和说明，最终 PR SHA 的 CI 另由 PR 检查状态确认。
- 新旧源码镜像分开编译和缓存；四 rank 使用相同 OCI ID，候选镜像 7699 个跟踪源码文件与 source manifest 一致，原生二进制 SHA 未变。
- 官方 arm 的完整 serving 日志未保留，只保留启动协调结果、state RPC 和请求/benchmark 回执，因此不报告其捕获耗时/日志内存差值。首次 profiling 的默认 KV 预算 worker 分支没有单独 GPU 验证；本次使用显式预算。非零 PIECEWISE fallback 未作整模型性能验收。没有长时间 agent 工作负载或四并发 1M 验收。既有 mypy/旧 CUDA API 提示保留，不声称全套 pre-commit 全绿。
- 初始缺目录、诊断 PYTHONPATH、旧 InputBatch 无 marker 的失败全部保留并明确为仪器/测试设置问题。旧版 marker 兼容只修改诊断扩展，未改变 184 项 vLLM 系列。

## 原服务恢复

四机原服务 `steering-ds41f-test` 已按原仓库、原配方和原镜像 a632 恢复，8000 端口健康检查通过，API 仍报告 1,048,576 上下文；live doctor 与配置匹配，22 项原有警告未扩展处理。全部 **236 份保留配置 SHA**、**16 个保留仓库的 HEAD/status** 均与窗口前一致；容器参数、环境键值和挂载一致，四个测试容器均已退出。没有改写原配置、权重、profile 或驱动/内核/网络/固件；维护 lease 和临时采样器已关闭。

实验跨两个维护窗口。第一次在候选任务完成后曾因本地心跳中断触发看门狗恢复原服务；确认恢复后重新取得独立窗口，完成参考与复测。两次启动及操作回执保留，不混用 rank 或把恢复操作当作模型故障。候选保持 Draft/未推广，原镜像和全部回滚资产保留。
