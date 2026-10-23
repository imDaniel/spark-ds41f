# 1022 离线准备与恢复流程评审

本轮只准备离线制品并评审恢复流程。U/E/S 三组 ARM64 镜像已在 Mac
构建，候选配置全部保持 `launch_enabled: false`；没有 GPU/TP4 测试、
节点部署、服务启停或 PR 合并。实际摘要、检查回执与制品校验值见
`runs/images.json`、`runs/candidate-archive.json` 和 `results.json`。

**离线制品可供审查，但还不能直接开窗。** [恢复评审](RECOVERY.md)
发现现有两个控制入口都不适用：`scripts/lab.py` 跟随 `origin/main`，
旧站点 watchdog 恢复的是更早的 `2e99502f / a63259…`。本轮已经固定正确
恢复目标并验证离线恢复决策；独立执行包装器/watchdog 的实际接线、安装
和故障演练仍是停服前必须完成的门槛，不能把逻辑测试当成自动恢复已部署。

## 对照制品

运行源码固定于 PR #56 的 `7252899c6498aae4a9149a6429e9f79d0dfdb538`。
本目录是后续本地准备，不改变该 PR 的源码或上游版本。

| 组 | vLLM 补丁数 / head | 镜像 config 摘要前缀 | 比较目的 |
|---|---|---|---|
| U | 184 / `8fc15c18f890` | `7a95695066ca` | 固定的纯 1022 参考 |
| E | 185 / `a13d993616f6` | `79ef9b657129` | 只增加 JSON control-spelling 修复 |
| S | 198 / `dea96d04ea9e` | `bae44d0a1417` | 完整 attention steering；未加载/已加载 profile 共用同一镜像 |

E 保留上游 184 个补丁的原文件引用，新增修复与 S 的代码 diff 相同。
独立 patch map、source manifest、lock 和重放结果都在 `encoder/`。
旧的 197/198 补丁实验、profile 和发布证据保持原样。

三组共用同一个经过核对的原生基础：B12X、NCCL、TileLang、TileKernels、
CUDA/FlashInfer/CUTLASS 输入与保留的 r6x 相同；vLLM 差异只涉及 Python，
原生源码及源码链接未改变。sparknet 从固定源码安装为 0.5.0，C proxy
在 ARM64 Linux 上编译并通过 ABI 10 检查。三组均逐文件核对源码，且
32 个保留原生库的哈希与 r6x 父镜像一致。GPU/JIT/TP4 正确性仍待实测。

Mac 使用的 Docker containerd 存储以 OCI manifest 标识镜像，Spark 使用
的 config ID 表示不同。回执同时保存两者；统一导出归档里的 config
字节必须再次得到上述 config 摘要。不能直接拿 Mac 的 `local_store_id`
与 Spark 上的经典镜像 ID 比较。

## 配置与 profile

`arms/` 固定 U、E、S-unloaded、S-loaded 四个顺序执行配方，使用独立
容器名、缓存、源目录和回环地址 `127.0.0.1:18000`。它们保留同一份
1017 原始权重、TP4、1M 上限、16 sequences、8192 batch 和每 rank
10.5 GiB KV，便于只比较预定变量。端口隔离不能隔离 GPU 或统一内存。

S-loaded 使用审查后的 `e06b8cb60f3f…` profile，其实际容器路径是
`/steering-1022/steering/heretic-1017-attn-v1.safetensors`。重新生成结果
逐字节一致；directions/alpha/layers 未变。保留服务继续使用旧的
`d7f0b997…`，不能交叉加载。11 项 profile 检查包含旧绑定拒绝及错误
输入拒绝。

## 本轮验证

- U/E/S：补丁重放、完整 `build check`；四个配方 `doctor` 通过。
- S 镜像内：126 项 steering CPU 测试，0 失败、0 跳过；真实 OpenAI
  请求类的 boolean dose 拒绝检查通过。没有挂载 GPU。
- E/S：各 66 项 encoder 离线检查通过；U 的三个原始编码参考检查通过
  （选择运行，另外 63 项明确 deselect）。
- profile：11 项通过；恢复决策：35 项通过。后者仅是离线逻辑测试。
- 四机只读前后核对记录在 `runs/service-unchanged.json`。实际启动计划
  的 `live:` 与 `shape:` 已检查，未加 `--apply`。

原始失败尝试保留在 `runs/attempts.json` 及关联日志中，不删除不利结果。
既有 PR 的 492 项仓库检查属于前一次审查；本轮没有把它们重复计为新结果。

## 下一次窗口预算

建议预约 **90 分钟**，正常目标 **70–90 分钟**，其中至少 **15 分钟**
用于恢复。镜像分发、四机摘要核对、执行器/watchdog 接线及其 CPU 故障
演练应在正式停服前完成，不能挤占恢复时间。此处没有预约或启动维护。

| 工作 | 预算 |
|---|---:|
| 阻止新请求、排空、取得 hold、快照、协调停止旧服务 | 5–10 分钟 |
| 有界组件 GPU 检查与必要冷却 | 10–15 分钟 |
| U → E → S-unloaded → S-loaded 的四次启动和有限验证 | 30–40 分钟 |
| 原服务恢复、身份/接口/gateway 核对 | 15 分钟 |
| 机动余量 | 10 分钟 |

首窗只做 `workload.json` 的有界检查：短请求与 32K prefill、C1/C4，
已加载状态下按 0→1→4→0 切换。第 75 分钟前停止安排新工作并进入恢复；
任何正确性、显存/主存、驱动或管理链路故障提前结束测试。首次冷启动
或故障可能超出预算，15 分钟不是恢复 SLA。完整 release comparison、
1M 容量与更高并发另开窗口；首窗通过也不自动合并或更新服务。

## 本地文件与复现

大制品位于仓库的 `.work/1022-offline-preparation/`，不进入 Git：

- `images/1022-u-e-s.docker.tar`：三组镜像共享层的统一归档。
- `images/r6x-8ad0da0366af.docker.tar`：保留镜像的只读导出副本。
- `profile-release/`：S 的已重新绑定 profile；`recovery/`：旧 profile、
  原始站点配置及完整历史的 `r6x-223a3bb3.bundle`。

`recipes/` 保留实际 Dockerfile；`tools/prepare_contexts.py` 用显式的
父镜像源码归档生成三组上下文，重放后检查它们与固定 proof 一致。
`tools/build_images.py` 仅允许 Mac 的 `desktop-linux` ARM64 环境，构建
容器禁用网络且不分配 GPU；拒绝覆盖已有镜像，只有上下文与已有构建
回执完全一致时才可复用。它不是生产节点的构建/部署命令。

恢复决策的可重复离线检查：

```sh
python3 experiments/2026-10-23-1022-offline-preparation/tools/recovery_contract.py \
  --inventory experiments/2026-10-23-1022-offline-preparation/runs/recovery-inventory.json
python3 -m pytest -q -o addopts= \
  experiments/2026-10-23-1022-offline-preparation/tools/test_recovery_contract.py
```
