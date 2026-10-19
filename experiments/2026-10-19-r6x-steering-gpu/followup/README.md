# r6x steering 日常验收与尾延迟复核

本记录补充前一轮硬件报告，不改写其原始数据。本轮运行时、镜像和 profile 均保持不变；只新增独立日常验收配方，移除测试专用 worker、profiler 及 dev mode，使用正式入口的 8000 端口。完整 1M 容量、图重放、混合请求和关闭路径 trace 的证据继续引用[原报告](../RESULTS.md)。

## 固定闭环复测

同一额外服务启动内，C4 按 0→1→1→0→0→1 运行；每组独立冷却、重置 prefix cache，使用原测试的 seeded 4K 文档、16 个固定问题、temperature 0、seed 2048、256 输出 token、15 秒预热和 90 秒计时。没有设置 reasoning/chat-template override。所有组零失败、零外来请求。每组不是独立服务启动。

| 顺序 | scale | 计时请求 | output tok/s | TTFT mean ms | TTFT P90 ms |
|---|---:|---:|---:|---:|---:|
| 1 | 0 | 48 | 140.656 | 366.31 | 384.86 |
| 2 | 1 | 48 | 138.600 | 374.03 | 519.52 |
| 3 | 1 | 48 | 138.822 | 374.71 | 527.86 |
| 4 | 0 | 49 | 138.900 | 358.96 | 373.09 |
| 5 | 0 | 48 | 139.644 | 363.99 | 376.42 |
| 6 | 1 | 48 | 137.600 | 368.02 | 414.15 |

三个 scale=0 组的吞吐均值 139.733 tok/s，scale=1 为 138.341 tok/s，约低 1.00%。这是同一启动内的组均值，不是严格统计等价性结论。闭环 P90 差异确实复现，未删除、修剪或用其他负载替代。

按保存的请求时间线合并：0/1 的 TTFT 均值约 363.06/372.26 ms，P90 约 376.1/414.1 ms。全部超过 450 ms 的请求，发出时都有此前请求尚未收到首 token。没有此前首 token 等待的组分别有 137/126 条，均值 355.08/355.66 ms，P90 372.7/372.1 ms；有这种重叠的组有 8/18 条。

这种分组观察到客户端到达节奏与慢请求的关系。闭环会在前一回答完成后立即补发；steering 改变输出、专家路由与 DSpark 接受情况，也会改变补发时刻。它不是内部 scheduler queue 的直接测量，不能证明闭环差值的全部因果来源，也不能据此宣布尾延迟已经消失。

## 同步到达补充对照

另做八对 C4 burst，交替 0→1 和 1→0；每次四条请求同时发出，等全部完成才开始下一组，保持相同四份约 4K 文档、问题映射、seed 与 256 输出预算。两种 profile 身份分别预热，保留全部请求与 prime 记录。这个对照去除了持续补发对上一条完成时间的反馈；HTTP 同时到达仍不保证服务端完全相同的 admission/batch 划分，也不代替上面的实际闭环结果。

同步 burst 的 0/1 合并 TTFT 均值为 **583.86/572.62 ms**，P90 **698.33/698.11 ms**；八对均值变化平均约 −1.92%，四请求发送时间跨度最大 1.73 ms。没有复现闭环的 P90 上升，但首批 1+3 或 2+2 的 admission 划分会影响均值，不能把这个负差值宣传成 steering 提速，也不能用它删除闭环差异。

结论：stock 关闭路径、常见强度及完整功能证据足以进入独立日常验收，不继续追加算子补丁。保留两项端到端限制：本轮 closed C4 的 scale=1 吞吐约低 1.00%、平均 TTFT 约高 9.2 ms；前轮最大 scale=4 的 C4 吞吐相对官方低 2.61%。请求完成时间改变所带来的到达/接受率影响与算子成本仍未被完全因果分离。这是有范围的使用验收，不是全强度、全负载无性能损失的保证。

## 独立验收配置

[acceptance.json](acceptance.json) 使用镜像 `sha256:8ad0da0366af0560678e09dbccefedc6688a58d79758b4f11fc63ef69ede4a0a`、188 项补丁的 vLLM tree `ac79557915e3ee2778f8f6353a3d35f848f564e4`。原生依赖、数学、精度及 profile 不变。source baseline 字段继续匹配 r6x steering lock，站点日常验收决定另记在新增的[验收记录](../../../manifests/baselines/2026-10-19-homelab-r6x-steering-acceptance.json)；不替换上游通用 recipe 和 benchmark reference。

- TP4 ring；1,048,576 context、16 sequences、8192 batch tokens、10.5 GiB KV/rank；DSpark 5 / fixed row cost 0.035。
- 服务端从 bundled `hf:default` 加载；请求名称为 `heretic-1017-attn-v1`，attention 层 2–39，alpha=2.5，scale 范围 [0,4]。
- 不带 steering 字段或 scale=0 保持 stock 行为；服务端加载 profile 不会把所有请求默认设成 scale=1。
- 监听 8000，served model 为 `deepseek-v4.1-flash`，CPA 的七个别名沿用原 profile/scale 映射；OMP 继续使用 1M/64K 及既有工具、图片、思考深度兼容参数。
- 新容器 `steering-ds41f-r6x-test`、新 source checkout 与启动项单独登记；旧配置、入口、镜像和权重保留。profile 和模型目录继续只读挂载已有的官方硬链接资产。
- 本配置没有 qualification worker、profiler、dev mode，也没有将测试代码放入 PYTHONPATH。

回退使用保留的 `Deepseek 1017 heretic test` 固定服务入口；由登记的控制器协调四 rank，先停止当前服务，再启动该保留服务。不得混合 rank 或用单机命令覆盖某一 rank。站点节点图保持 ignored，本仓库不保存凭据。

## 证据与复现

`evidence/` 保存六组原始 gzip、同步 burst、驱动原文、输入身份、分析与 SHA 清单。历史硬件记录保持原样。CPU 源码复查未发现新的阻断问题；涉及请求身份、槽位、图初始化、关闭路径、SP/CED、消费者计算与严格 CPU gate。实际部署后的身份、健康、入口与保留资产检查作为站点运行回执独立记录。

原始 `paired-burst.json` 按原字节 gzip 保存，压缩前后 SHA 见 `evidence/compressed-originals.json`。复验分析时先复制 followup 到临时目录，将该 gzip 解压回同名 JSON，再运行归档的分析脚本；避免写入历史证据目录。

准备检查：完整部署仓库 CPU **465 tests、0 skipped**，固定服务控制器 **7 tests**，独立配方 doctor 与完整 build-input check 通过。首次把站点文件放在 `config/cluster*.json` 被唯一通用 recipe 检查拒绝；已移至本实验目录，原检查保持不变。
