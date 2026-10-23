# 1022 显式 scale 短窗口结果

本轮 **未通过并已恢复原服务**。只启动 S-loaded 候选一次；所有测试生成请求
显式携带 `heretic-1017-attn-v1` 和 scale 0/1/4。PR #56 保持草稿，未合并、未晋级。
[离线准备与原定范围](../2026-10-23-1022-scale-validation/README.md) 保持原样。

## 运行与检查

- 执行提交：`c8de3319537dd0ffe842dd99b671656a62fe5837`；开窗前 CI 13/13 通过。
- 镜像 `bae44d0a`，vLLM tree `ab7b6505`，profile `e06b8cb6`；沿用首轮硬件制品，无 rebuild。
- 四机独立候选 checkout 经具名 `cluster sync --apply` 同步至同一已发布提交。
- 窗口：2026-10-23T11:12:51.848800+00:00 至 2026-10-23T11:18:09.373125+00:00，实际 **5.29 分钟**；原预算 45 分钟、恢复预留 15 分钟。
- U/E/旧 r6x 对照、性能套件、1M/16 路容量均未运行。旧服务只做必要的恢复核对。

| 检查 | 本轮结果 |
| --- | --- |
| 算术、代码、中文解释，0→1→4→0 | 12 条完成；算术、非空流和返回 0 一致性通过 |
| scale 1 工具参数逐字检查 | 第 13 条失败 |
| scale 1 工具回填、scale 4 工具调用与回填 | 3 条未执行 |
| 32K 冷热缓存、C4 混合 scale、取消 | 13 条未执行 |

12 条的通过只代表预先定义的有界检查。5 条碰到 128-token 上限，不能据此声称
三句话说明或完整代码示例的质量验收通过。代码主体的 `add` 实现存在，scale 4 的
示例尾部被截断。不同 scale 的回答形式不同，但不足以评价广泛 steering 效果。

## 失败回执与离线定位

scale 1 的工具调用返回合法 JSON，工具名与 ID、普通 `role`/`type` 值和
`quote\"key` 嵌套键正确；预期值 `<｜end▁of▁sentence｜>` 实际为空字符串。
完整 API 回执在 [requests.json](evidence/06ad9132-7d71-4777-81d9-8a776e3a9e98/scale-requests/requests.json)。
执行器按计划立即停止后续请求并恢复，没有放宽断言或重跑掩盖失败。

本机 CPU 复现使用同一候选 tree 的原始 parser/config/scanner/lexer/converter，
读取实际 checkpoint 的特殊 token 表；仅替换包入口和未使用的高层适配器。
字符 tokenizer 是明确的合成夹具，不冒充真实生成 token 序列：

| 合成输入 | 参数值 |
| --- | --- |
| 普通文本，普通字符 token IDs | 保留 |
| 控制 token 拼写，普通字符 token IDs | 保留 |
| 同一拼写，真实特殊 token ID | 空串 |
| 同一拼写，仅文本而无 token IDs | 空串 |
| JSON Unicode 转义拼写，普通字符 token IDs | 保留 |

见 [复现结果](evidence/parser-replay.json) 和 [复现脚本](tools/replay_parser.py)。
这说明输出处理会受 token 来源影响，提供了能产生同类结果的机制；本轮 API 回执
未记录原始生成 token IDs，**仍不能确定线上是模型直接生成空值、生成特殊 token，
还是其他输出路径导致，也不能归因于 attention patch**。之前 JSON encoder 修复
针对输入编码，不能把这次输出失败直接视为该修复回归。没有因此改动运行时补丁。

后续先完善本地诊断回执，让请求保存生成 token IDs/原始输出，并区分工具 roundtrip
与特殊拼写逐字保真。若再开短窗口，先复现这一个 scale 1 用例，再决定是否续跑
未完成的 16 条；不需要重跑 U/E/旧版本对照。本报告不执行下一窗口。

CPU 复现命令（在独立环境安装 `evidence/parser-replay-requirements.txt` 的哈希固定依赖）：

```sh
python tools/replay_parser.py --source /path/to/pinned/vllm --tokenizer /path/to/tokenizer.json
```

## 恢复与证据

四机恢复至 `223a3bb3` / 镜像 `8ad0da03` / profile `d7f0b997`。
源码、配置、节点映射、profile、镜像、挂载、启动命令摘要与开窗前一致；容器均运行，
restart count 为 0，内存 guard 正常。`doctor --live`、私有 API 与 CPA 的 stock/steered
恢复调用通过。DeepSeek provider 与相关 payload 配置摘要未变。
占用锁和本窗口入口规则已移除，独立 watchdog 停止，临时网关 key 已从 Mac/01 移除。
控制器因测试失败返回 1，恢复状态明确为 `closed`；不能把控制器退出码误读为恢复失败。

采样最低可用内存 21.12 GiB，最高 GPU 温度
53°C，swap 最大增量
0.0 MiB；最终四机检查无 GPU fault/foreign job。
没有重启 Docker、调整主机配置或改写权重。准备期间 02 的新操作目录首次创建因权限
不足被拒绝；随后通过 `sudo install -d -o admin -g admin -m 700` 创建目录，并重新通过
四机只读 preflight 后才开窗。没有绕过任何运行时拒绝。

[结构化结果](results.json)、[最终身份检查](evidence/final-identity-checks.json)、
[窗口事件](evidence/06ad9132-7d71-4777-81d9-8a776e3a9e98/events.jsonl)、[运行器退出日志](evidence/runner-journal.txt)
与全部哈希见 [artifact-manifest.json](artifact-manifest.json)。历史准备和首轮证据未改写。
