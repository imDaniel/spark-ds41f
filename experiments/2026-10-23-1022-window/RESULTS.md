# 1022 GPU / TP4 有界验证结果

结论：本轮有界硬件验证通过；原 r6x 已恢复。未晋级候选，PR #56 保持草稿且未合并。

主硬件窗口从 09:42:55 UTC 开始，最终在 10:20:45 UTC 完成恢复验收，历时 37.84 分钟（包括中间恢复及续测准备）。原预约截止为 11:12:55 UTC。

## 验证结果

| 项目 | 结果 |
| --- | --- |
| GPU 组件 | 29 通过，0 失败，0 跳过；数值 oracle、关闭路径、行控制、CUDA Graph 与融合 consumer |
| u | 19 条检查完成 |
| e | 19 条检查完成 |
| s-unloaded | 37 条检查完成 |
| s-loaded | 50 条检查完成 |
| 32K 前缀缓存隔离 | 13 条检查完成；三个 scale 首次缓存命中均为 0，再次均为 32,512 tokens |

U、E、S 未加载、S 加载 scale 0 的五项普通功能输出逐一相同；S 的 0→1→4→0 返回 stock 后也逐一相同。S 组另覆盖 C4 混批、取消后 stock、错误 profile、未加载 profile、负值、布尔、超范围及 NaN/±Infinity 拒绝。非流式 HTTP 400 与流式 HTTP/SSE code 400 分别验证，任何错误前已生成内容或服务端 500 均不算通过。

长前缀检查使用带随机 nonce 的相同 32,768-token 输入。scale 0/1/4 各自首次无命中、同 scale 再次命中，随后混批与取消后 stock 仍匹配各自独立输出。短功能请求本身不被当作缓存隔离证据。

## 性能记录的范围

每个 arm 均记录 1K/32K 输入、C1/C4、128 输出 token；S 加载组的性能请求为 scale 1。每格只有一个 batch，且重复前缀会命中缓存，不足以判断整体性能提升/回退或替代 release comparison。原始 TTFT、decode TPS、usage 和输出摘要见 results.json 与 evidence 中的请求收据。

| 节点 | 最低采样可用内存 GiB | 最高采样 GPU 温度 °C | 最大 swap 增量 MiB |
| --- | ---: | ---: | ---: |
| spark01 | 21.471 | 62 | 0.0 |
| spark02 | 22.692 | 60 | 0.0 |
| spark03 | 22.591 | 60 | 0.0 |
| spark04 | 22.611 | 60 | 0.0 |

未观察到 GPU/驱动故障、OOM、内存保护触发或 rank 丢失。采样值不是连续峰值；内存保护仍按原配置工作。1M 上下文配置被保留，本轮没有提交完整 1M 请求，也没有验证 16 路满容量或广泛质量/拒答表现。

## 恢复与现场修正

实际恢复了两次四机 r6x。最终 HEAD、镜像、配置、节点映射、profile、挂载及启动命令摘要均与测试前一致；新容器 restart count 均为 0。`doctor --live` 通过并保留原有主机警告；未调整驱动、内核、风扇、NVMe 或权重布局。私有 API 和 CPA 的 stock/steered 请求通过，DeepSeek provider 与 payload overrides 字节语义未变。占用锁和临时入口规则已移除，独立 watchdog 已停止。

首轮入口检查将 loopback 健康长连接误算为外部请求，又因另一路新增 office-qwen3.8-iq3 触发过宽的全站别名检查；该轮没有引擎启停，现场复核后闭环。第一次硬件运行的 S 未加载组返回了正确的 SSE code 400，但原脚本只接受 HTTP 400，因而触发恢复。修正测试工具后仅重跑两个 S 组，复用已完成且哈希固定的 U/E 结果。没有改动本轮镜像中的 vLLM 补丁。失败记录与所有恢复日志均保留。

本机/已安装执行器故障模拟 44 项通过；现场适配与请求/缓存检查 19 项通过。独立 watchdog 在 01 完成安装、身份和心跳检查；本轮没有故意制造物理 GPU、01 断电或真实控制器失联故障。

仅删除本轮传输用的重复镜像归档与未完成副本，共释放约 118.72 GiB 分配空间；四机测试镜像、缓存、独立源码、profiles 和记录保留。临时复制的网关凭据已删除。

## 可复核材料

- [结构化结果](results.json)
- [最终身份核对](evidence/final-identity-checks.json)
- [缓存收据](evidence/continuation/cache-isolation.json)
- [命令和失败记录归档](evidence/command-records.tar.gz)
- [四机候选日志清单](evidence/rank-logs/log-manifest.json)
- [临时文件清理收据](evidence/transfer-cleanup.json)

主运行固定在 `1f9bd3f5`，修正测试工具后的续测固定在 `0c186e22`。镜像 U/E/S 的 config digest、vLLM tree、profile 和配置哈希见三个 window manifest。保留 r6x 为 `223a3bb3` / `8ad0da03`，旧 profile 为 `d7f0b997`，候选 profile 为 `e06b8cb6`。
