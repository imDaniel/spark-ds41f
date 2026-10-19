# r6x steering 四机实测记录

本轮完成了隔离硬件功能检查；候选继续保持 Draft，未推广到原服务。关闭路径的实测接近持平，默认 scale 1 的 C1/C4 吞吐与官方版约差半个百分点。最大 scale 4 的 C4 吞吐有可见下降，scale 1 暖缓存 C4 的尾延迟仍需保留验收意见，不能据此宣布所有性能目标已经解决。

测试部署 revision 为 `8181e0cc9595f6370291bd8274c41878f8ada56b`；本次提交只补记录，不改变其 188 项补丁、`ebb44766` source head、`ac795579` tree 或实际镜像。官方是 `7316c7f9`／`4d6851c8` 的 176 项补丁。完整数字、原始请求计时及来源在 [results.json](results.json) 和 [evidence/hardware](evidence/hardware/) 中。API／冷请求／操作回执 JSON 按原字节 gzip 保存，含完整 1M 输入 token ID；`compressed-raw-files.json` 记录压缩前后 SHA，避免把百万行数组混入代码 review diff。

## 持续输出吞吐

TP4 ring，同一官方 1017 权重、10.5 GiB KV/rank、1M 上限、16 sequences、8192 batch tokens、DSpark 5 / row cost 0.035。closed C1/C4，每路约 4K 缓存文档、16 个固定问题、temperature 0、seed 2048、固定 256 输出 token。请求没有显式覆盖 reasoning/chat-template 模式；不把这些数字称作 thinking-off 成绩。每组预热 15 秒、计时 90 秒／6 个窗口，前后排空，失败与外来请求均为 0。该负载约 93–94% prefix hit。

| 测试组 | C1 tok/s | C4 总 tok/s | 相对官方 C1 / C4 |
|---|---:|---:|---:|
| 官方 176 | 70.689 | 139.356 | +0.00% / +0.00% |
| 候选，未加载 profile | 70.389 | 140.178 | -0.42% / +0.59% |
| 候选，loaded scale 0（前） | 70.433 | 140.122 | -0.36% / +0.55% |
| 候选，scale 1 | 70.333 | 138.711 | -0.50% / -0.46% |
| 候选，scale 4 | 70.367 | 135.722 | -0.46% / -2.61% |
| 候选，loaded scale 0（后） | 70.389 | 140.122 | -0.42% / +0.55% |

loaded 顺序为 0→1→4→0；C1 前后 0 相差约 0.06%，C4 均值相同。未加载与 loaded 关闭的对照约在 0.04% 以内。每个 arm 只有一次服务启动；6 个计时窗口不是 6 次独立启动，也不是严格的 ±1% 统计等价性证明。原始记录保留每个窗口的区间、每个请求和全部 server counters。

scale 4 C4 相对官方：steps/s 15.378→15.300（−0.51%），tokens/step 9.065→8.872（−2.12%），accepted/draft 1.377→1.323。吞吐下降与每步提交 token 和草稿接受情况的变化一致。投影会改变激活、输出与专家路由；这些因果贡献尚未被整机实验完全分离，不能把全部差值归入新增 kernel，也不能把该档吞吐下降忽略。

## 延迟

独立冷请求测试使用相同 seeded 4K 文档、每条不同 cache salt、每个 burst 前重置缓存；C1 12 条、C4 48 条，每条生成 1 token。官方 C1/C4 TTFT 均值 798.94/2000.75 ms，loaded scale 0 为 803.21/2024.95，scale 1 为 801.47/2002.76。完整 payload、波次及计时见 `evidence/hardware/cold/`；它与持续负载的暖缓存排队延迟是不同实验。

暖缓存 C4：

| 测试组 | 计时请求数 | TTFT mean ms | p50 ms | p90 ms |
|---|---:|---:|---:|---:|
| 官方 176 | 48 | 360.13 | 353.94 | 376.98 |
| 候选，未加载 profile | 49 | 364.61 | 351.89 | 384.53 |
| 候选，loaded scale 0（前） | 48 | 365.20 | 354.73 | 380.55 |
| 候选，scale 1 | 48 | 373.90 | 357.01 | 520.72 |
| 候选，scale 4 | 47 | 362.76 | 357.67 | 372.80 |
| 候选，loaded scale 0（后） | 48 | 366.68 | 355.33 | 386.44 |

原始请求按 `15 <= sent < 105` 计入窗口。官方有 1/48 条超过 450 ms，首组 loaded 0 为 3/48，scale 1 为 5/48，scale 4 为 2/47；nearest-rank p90 在 scale 1 跨入较慢请求组。此说明用于理解估计量，不用于消除尾延迟问题；均值、p99、TPOT 及每条时间均保留，尚不能宣称该尾部差异已解决。

## 固定形状与局部算子

固定 grid 为 11 个形状、每形状 60 次 replay、4 ranks，0→1→4→0。以每次最慢 rank 的 step time 取中位数、再对形状取几何平均：相对官方约 +0.30% / +1.40% / +2.01% / +0.50%；相对前后 0 的均值，1 和 4 约 +0.99% / +1.60%，最差形状约 +2.38% / +3.36%。分布式 rank 不是独立样本。该诊断排除了请求控制行准备；相同 token/shape 也仍可能因激活改变而选中不同专家，不能当作纯投影成本。

同进程、共享 scratch/输出 buffer 的局部 CUDA Graph 对照，用预先投影输入作为**实验参照**，要求 active 与参照的完整输出 byte 相同。H=5120、alpha 2.5、scale 1，11 个交替顺序窗口。普通 1/4/6/16 行额外约 3.91/4.02/3.85/4.12 us；96 行约 11.19 us。按 38 层估算普通小行约 0.15 ms，只是热缓存局部观测，不是整步可保证回收的收益。

SP 的最终参照保持 WORLD=4，并用四份可精确相加的投影后输入保留 stock 的读取／归约工作；64/256/2048 行额外约 3.75/23.67/187.77 us，完整输出 byte 相同。初版参照 WORLD=1，已经提前完成 stock 本来需要的 rank reduction，其 SP 差值包含这部分工作，作为初始上界记录保留，未当作最终增量。两版驱动、所有窗口、源文件 SHA 与原始结果均归档；预投影参照不是可部署算法。

既有 dot 布局 screen 的 8 warps 在常见小行最好，其他布局未提供稳定的完整 dot+consumer 收益，因此没有追加运行时补丁。当前消费者寄存器更新已经融合进 mHC；普通路径仍每层调用独立 dot，SP 使用 parts projector。本轮没有因小型计时差异改动数学、精度、profile 或通信 ABI。

## GPU、图、容量和关闭路径

- 新镜像实际 29 GPU tests / 0 skipped；12 项独立官方／候选 stock mHC 输入输出 SHA 及图回放相同。
- loaded/unloaded 实际启动、三组 64-token 全量 token/text/top-5-logprob 指纹、请求错误契约、1/0/4/0/1、混合 batch、取消复用、8K 混合 CED/SP 均通过；1M 后完整 smoke 再次通过。
- 未加载：30 target graphs，四 rank 常驻分配与官方完全相同。loaded：60 target graphs，增加 80.8657 MiB/rank；1M 后常驻分配未增长、四类控制 buffer 地址稳定。OOM 和 allocation retry 均为 0。runner capture 日志约 11→14 秒；日志的瞬时 memory-took 数字不等于常驻图内存。
- 单请求实际输入 1,048,568、输出 8、total 1,048,576，约 241.02 秒完成。它验证 loaded scale 1 的单请求容量，不代表 16 路各 1M 或 1M 性能对照。
- 首次独立 JIT 缓存启动的完整控制器用时：官方 326.19 秒，loaded 332.03 秒；缓存已热的 unloaded 为 97.45 秒。不能把这些直接与上游不同缓存条件的启动成绩相比。
- 32 份 trace 全部从归属明确的 capture manifest 导出，核对 SHA、rank/world=4、CPU/GPU 事件、decode 图回放以及 scale-1 正对照。发布时仅移除身份元数据与设备 UUID，保留全部非 metadata 事件及其参数；发布副本又逐份通过原 checker。
- 16 组官方对 loaded-0／unloaded 的 rank/stage 对照中，GPU kernel 总数、复制计数及 GPU launch 等非轮询 runtime 计数相同。每份有一处 float fill 向量宽度 2→4 的名称差异，保留原始名称；runtime 差异限于 EventQuery / ThreadExchangeStreamCaptureMode。关闭 trace 无 row-scale/dot/project 或 steering custom-op 事件。源路由与 stock mHC 机器码一致性支持该 8K/decode 样本没有额外 SP writer 物化；未直接观测任意 kernel 内部的全局内存事务，不外推到所有负载。
- rank 0 的 6 个 stock mHC CUDA kernel：机器码、代码 section 属性、target flags、常量和静态 shared allocation 与官方一致；候选另有 active variant。原始 CUDA／CUBIN 及 CPU-only 重验驱动在 `stock-mhc-artifacts.*` 和 `verify-machine-code.py.txt`，可以独立解析。host shared-object／整个 CUBIN 文件包含构建元数据，不能用其整体 SHA 不同推断机器码不同。

## 恢复与证据边界

已恢复原四机服务、原镜像、8000 端口与 1M 上限。236 份配置 SHA、16 个保留仓库的 HEAD/status，以及启动参数、环境、挂载源／目的／权限、网络／设备／资源限制核对一致。一个节点的 bind 列表顺序变化；仅在精确 multiset 相同、目的唯一且不重叠时接受该顺序差异。维护锁与自身心跳、监控、watchdog 均已退出。

整个窗口的主机遥测另存原始 gzip 与摘要：head 累计 1 页 swap-in、16 页 swap-out（64 KiB 页），其他三台均无计数增加；不能称为零 swap。最低可用主机内存约 17.9 GiB，主机温区峰值最高 88.9°C。窗口内含恢复阶段的四机内核日志未发现新增 Xid、GPU 掉线或 OOM。未调整 kernel、driver、固件、风扇、NVMe 策略、权重或现有配置。

首次 trace collector 把 head API trace 也计入 worker 清单而被完整性检查拒绝；改为明确的 `dp0` worker capture 后重新生成独立 run，失败记录未替代为通过。编译证据采集先尝试 host `.so`、再遇缺失 disassembler，最终直接解析已归档 CUDA ELF；这些是 CPU 侧证据采集尝试，未运行失败 GPU kernel 或安装宿主工具。其他早期准备失败及未完成的系统 Python gate 见 README；完整 dependency 环境和 CI 的 465/108 测试成绩分别有独立来源。

保留 Draft：本记录完成本次受控验证及恢复；默认强度和关闭路径的观测接近基线，但最大强度 C4 与暖缓存尾延迟仍需作为真实限制接受复核，不能以 CPU 绿灯、局部上界或几何平均宣布所有性能目标达成。
