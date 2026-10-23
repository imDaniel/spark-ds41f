# 1022 恢复执行器与 watchdog：本地实现

这是 [离线准备与恢复评审](../2026-10-23-1022-offline-preparation/README.md) 的后续。
唯一新增变量是维护窗口的执行控制；不改变 U/E/S 镜像、模型、profile、
patch series、原服务配置或已保存的验证记录。

**当前仅完成本地代码与模拟故障验证，不能据此启动真实维护窗口。**
`window.disabled.json` 保持 `runtime_enabled=false`，现场探针及网关适配器尚未绑定，
没有生成真实运行授权文件。代码没有安装到 01，也没有同步到其他节点。
本目录不含自动安装、SSH 登录、镜像分发、模型请求或测试发起命令。
实际结果见 [验证记录](VALIDATION.md) 和 [results.json](results.json)，其中保留失败尝试及修复原因。

## 实现

- `window_executor.py`：窗口创建、排空、四组配方切换、恢复、心跳以及独立 watchdog。
  所有集群启停均构造指定 checkout 内的 `bin/spark --shape tp4-ring
  --cluster-config <固定配方> cluster start/stop`，先读取计划并验证四节点与 shape，
  再允许同一命令追加 `--apply`。不使用默认配置、`origin/main` 或单 rank 启动。
- `command_worker.py`：独立进程组中的命令门卫。只有进程身份已落盘后收到 `GO`
  才执行子命令；控制器在此之前退出，worker 收到 EOF 后直接结束。
- `tests/fake_site.py`：本地四节点 JSON 仿真。以真实可执行文件模拟 `bin/spark`、
  观察、流量入口和 API 验证；完全不调用 SSH、Docker、GPU 或网络服务。
- `tests/test_executor.py`：调用上述实际执行器和子进程，不只测试恢复决策表。

控制器和 watchdog 共用 hold 旁的一个 `flock` 操作锁。hold 独占创建，包含
窗口 UUID、holder、manifest 哈希、心跳和结束时间；不接管他人的过期 hold。
每次实际命令及其输出路径保存在日志，待执行 worker 的 PID、出生身份和阶段
写入原子状态文件。恢复前先终止并确认本窗口记录的进程组退出，避免父进程死后
旧启动命令继续执行。PID 身份不符时拒绝发送信号。

watchdog 应最终独立运行在 01，不能依赖 Mac 会话。它既检查失联心跳，也检查
绝对窗口截止；预算与心跳使用同一开机周期的 monotonic 时钟，墙钟调整不能延长
测试时间。主机重启后拒绝沿用旧窗口自动操作。命令有明确超时，超时后停止进程组。

恢复先确认候选容器、guard、GPU 进程与本窗口组件任务全部停止，再恢复固定的
`223a3bb3… / 8ad0da0366af… / d7f0b997…` r6x 身份。已经完整健康的旧服务不会重启；
部分旧 rank 先全部停止，再协调启动。管理链路丢失、未知 GPU 进程、身份漂移或
GPU/驱动故障进入人工处理，保留 hold，不循环重试。

恢复验收分两次：入口仍阻断时先验证原服务；恢复原入口后再验证网关。
后一次失败会尝试重新阻断入口一次并保留 hold。只有原服务与入口全部通过，
才标记窗口结束并释放本窗口的 hold。

## 本地复现（CPU，不接触节点）

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s experiments/2026-10-23-1022-recovery-executor/tests -v

python3 experiments/2026-10-23-1022-recovery-executor/window_executor.py \
  --manifest experiments/2026-10-23-1022-recovery-executor/window.disabled.json check
```

第二条仅解析本地配置，不运行任何适配器或集群命令。所有其他 CLI 动作在没有
`--execute` 时同样只检查配置；即使带该参数，发布的禁用配置仍拒绝运行。
测试自行在系统临时目录创建合成授权、模拟配置和伪 `bin/spark`，退出后清理。
真实 watchdog 需要独立进程；测试中的进程级用例实际杀死模拟控制器，并由另一个
进程完成模拟恢复，随后确认遗留命令进程组已经消失。

## 现场绑定契约

每个服务的 `identity` 固定源码提交、镜像 config digest、profile、配置、节点映射、
挂载和模型身份。`files` 固定本地执行入口与配置文件的 SHA-256。
实现文件、适配器解释器及脚本也需要纳入 manifest 哈希。现场准备必须发布为干净的
固定提交；启用配方不能通过修改既有禁用配置来绕过发布流程。

五个适配器都是固定 argv/cwd 和文件哈希的可执行程序，无 shell 拼接。它们从
`SPARK_WINDOW_CONTEXT` 读取窗口 UUID、manifest 路径、原入口快照和验收阶段；
stdout 返回 JSON，诊断不得混入 JSON。

| 适配器 | 必须实现的站点契约 |
|---|---|
| `observe` | 每次重新观察全部四节点，返回当前身份、干净/已发布源码、资产校验、guard 配置与活动状态、容器与其进程、GPU/驱动故障、外来任务和本窗口组件任务；包含时间戳、运行/等待队列、旧服务 readiness 和网关状态。缺失数据不能当作健康或 idle。 |
| `block_ingress` | 仅阻止约定入口的新请求，保留已进行的请求；不改变模型别名或推理进程。失败必须非零退出。 |
| `restore_ingress` | 根据本窗口保存的入口快照恢复原状态，操作须幂等；不重建或猜测别名。 |
| `verify_retained` | 在 `private_api_before_ingress` 阶段核对原服务 health/models、普通与 steering 有界请求及别名配置；在 `gateway_after_ingress` 阶段核对原网关路径。返回五个明确布尔结果，不能只凭端口监听通过。 |
| `stop_owned_jobs` | 只停止该窗口已登记且出生身份匹配的组件任务；必须在观察中确认清零。禁止按进程名通杀。 |

观察 JSON 的字段和执行顺序由真实测试夹具完整示范。这些适配器属于现场边界：
本轮只实现并测试了执行器的调用、校验、错误处理和恢复接线，没有把仿真适配器
冒充为生产探针。真实观察/入口/API 命令须在不影响现有服务的准备阶段配置、评审，
其变更动作和推理验收只能在后续明确授权的窗口执行。

## 停服前仍需完成

1. 绑定并审查现场适配器，刷新四节点身份、管理路由、入口与人工应急通道。
2. 把准备目录、执行器及最终配方发布到自有仓库的固定提交，保持 PR #56 未合并。
3. 分发并核对 U/E/S 镜像、候选 profile 与源码副本，保留旧服务恢复资产。
4. 在 01 安装独立 watchdog，并先用仿真模式验证现场进程管理、权限与持久化路径；
   未安装不能把本地测试称为已部署自动恢复。
5. 获得具体维护窗口授权，生成绑定最终 manifest 哈希与窗口 UUID 的授权记录，
   确认无人持有 hold，阻断新流量并排空后才进行协调停服。

维护预算仍为 90 分钟，最迟第 75 分钟进入恢复，至少预留 15 分钟；驱动或节点故障
可能超出该预算。900 秒心跳过期是失联触发条件，不是恢复 SLA。
