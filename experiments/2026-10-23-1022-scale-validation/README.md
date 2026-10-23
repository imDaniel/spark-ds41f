# 1022 请求级 scale 验证准备

状态：离线准备完成，尚未运行本轮 GPU/TP4 请求。镜像、模型和 profile 沿用
[首轮通过的制品](../2026-10-23-1022-window/RESULTS.md)，本目录不晋级配置。

根据所有者最新范围，只验证新 S-loaded 候选的显式 scale 请求。U、E 和旧
r6x 不再作为测试对照；历史结果仅作描述性参考，不生成跨窗口的性能优劣结论。
原服务仍是窗口结束时必须恢复的目标，恢复核对不属于比较测试。

## 固定范围与通过标准

所有生成请求都携带 `steering_profile=heretic-1017-attn-v1` 和
`steering_scale`。scale 1 为主，4 检查高剂量路径，0 仅用于开关后的状态检查。
`/tokenize` 只是缓存探针的输入准备，不是生成请求，也不接收 scale 参数。

| 请求组 | 数量 | 检查 |
| --- | ---: | --- |
| 算术、代码、中文解释，按 0→1→4→0 | 12 | 算术正确、非空、完整流、返回 0 的输出一致 |
| scale 1/4 的强制工具调用及回填结果后的继续生成 | 4 | 普通 role/type 字段、嵌套转义键和控制 token 字面文本逐项一致；工具调用 ID 完整 |
| 32K 冷/热前缀、混合 scale C4、取消及返回 0 | 13 | 首次跨 scale 无命中，同 scale 实际命中且输出一致；无串扰 |
| 合计 | 29 | 任何失败均保留回执并结束本轮 |

工具参数是惰性 JSON 数据，测试不会执行工具里的动作。代码和解释输出保存供
人工复核；非空检查不等于完整代码正确性或广泛质量验收。原来的参数拒绝、
29 项 GPU 组件和真实 tokenizer 结果继续作为已有证据，不重复计为本轮通过。

本轮不运行完整 release gate、engine grid、三段性能对照、1M 或 16 路容量
测试，也不声称已经完成它们。现有 engine grid 不带请求级 scale；模型
teacher-forced scoring 也不继承生成时的 scale，均未纳入本计划。

## 时间和恢复

预留 40–45 分钟：排空与协调启动约 5–10 分钟，请求约 10–20 分钟，恢复
预留至少 15 分钟。这是预算；不自动延长窗口。45 分钟上限中，第 30 分钟前
转入恢复，任何未完成项记录为 incomplete，不当作通过。

执行器继续使用已验证的 `2026-10-23-1022-recovery-executor`，不改写历史实现。
原 manifest 的单命令 1200 秒上限足够覆盖本轮请求；长发布测试所需的调整
已不在本轮范围。既有 hold、独立 watchdog、进程组清理和手动恢复状态保持。

开窗前从干净已发布提交同步候选独立 checkout，重新只读核对四机身份、hold、
入口和镜像/profile。绑定全新 UUID、绝对截止时间、远端 Python 的实际哈希，
不能复用已经关闭的上一窗口或其授权 JSON。新 manifest 必须固定本目录四个
执行输入（两个 Python、base.json、plan.json），以及引用的 request/cache/
site adapter 和执行器/worker。`run_window.py` 在开窗前检查这些 pins。

执行器的四个候选记录仍用于确认没有其他候选容器运行；本轮仅调用
`switch('s-loaded')`。沿用原 S-loaded 配方、镜像和 profile，不需要 rebuild。
节点操作仍通过具名 `bin/spark cluster` 计划和四机协调 apply；不直接调用旧
`scripts/lab.py` 开窗，不调整 Docker、风扇、内核或网关配置。

正常完成及请求失败后均恢复 `223a3bb3` / `8ad0da03` / 旧 profile `d7f0b997`。
检查四节点身份与 guard、私有 API 和 CPA，再释放入口和 hold、停止独立
watchdog。管理链路或 GPU 故障进入 manual 时保留 hold，不自动重启试错。

## 命令

本地只打印计划，不联网、不启动服务：

```sh
python3 experiments/2026-10-23-1022-scale-validation/run_window.py
python3 experiments/2026-10-23-1022-scale-validation/scale_requests.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s experiments/2026-10-23-1022-scale-validation -p 'test_*.py' -v
```

未来维护窗口在 01 运行以下入口；两个参数必须是该窗口重新绑定的节点本地文件。
先启动并核验独立 watchdog。请求脚本只在执行器提供的活动 S-loaded 上下文中
接受 `--execute`，输出必须在该窗口独立目录下且不能覆盖旧结果。

```sh
python3 experiments/2026-10-23-1022-scale-validation/run_window.py \
  --manifest /absolute/path/to/fresh-window.json \
  --authorization /absolute/path/to/fresh-authorization.json --execute
```

证据：`base.json` 固定镜像/profile/source 和历史参考；`plan.json` 固定请求数和
边界；`results.json` 只记录离线准备结果；`artifact-manifest.json` 固定文件哈希。
