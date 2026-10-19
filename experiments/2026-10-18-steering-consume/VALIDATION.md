# 验证记录

<!-- Keep tables and reproducible CLI commands on single lines. -->
<!-- markdownlint-disable MD013 -->

本候选从 2026-10-18 开始整理。前期 CPU/CI 的结果对应 c035 tree；2026-10-19 在授权硬件窗口追加 0192，并开始 efc tree 的 GPU 验收。以下“前期结果”保留原范围，不能作为新 tree 的 GPU 或性能证明。

## 当前硬件验证

- 初始 c035：25 GPU 用例通过，4 个融合图用例因 TileLang 区域变量错误失败；保留失败日志，0192 单独修复。
- 当前 efc：SM121 实际 29 GPU 用例通过；ARM 镜像内 108 CPU 用例通过且完整清单一致，实际 OpenAI Chat/Completion 请求类 CPU 合约检查通过。
- 独立父版 a632 镜像与 efc 候选镜像：12 组实际 stock mHC 结果逐位一致，各自编译缓存隔离，真实 CUDA graph 重放及固定指针通过；微基准计时只作诊断。
- 全新 184 补丁重放：head/tree/patchset 和最终 diff check 通过。
- 镜像内 7699 个源码文件 SHA 核验、原生二进制前后 SHA 核验通过；新镜像继承已验证的固定原生父层。
- 候选 loaded/unloaded 的真实四机启动、FULL/DSpark 图重放、零/非零切换、混合及取消槽位、真实长 prefill 的 CED/SP 检查均通过。
- 四个 rank 的 prefill/decode trace：scale=0 的控制行、dot、projection kernel 和 steering custom op 均为 0；非零普通 writer 进入 mHC 消费融合，SP 仍保留 projector。
- 精确 1M 请求：1,048,568 输入 +8 输出通过；后续 stock 请求 token/logprob 位一致。全 rank 无分配重试或 OOM。
- C1/C4、scale=0/1/4 及末尾 0 复测已完成；末尾零强度 TPS 与开头差异小于 0.1%。独立父版/官方性能对照及候选独立重启复测已完成；后者 scale=0 TPS 与官方差异约 −0.02% / +0.11%，十一形状步耗时约 +0.08%。完整对照、原始样本和未覆盖项见硬件实验 RESULTS.md/results.json；早期 candidate-results.json 保留不覆盖。

当前证据在 HomeLab 的 `ops/steering-gpu-acceptance-20261019/evidence/` 及头节点隔离 runtime 的 evidence 目录；镜像身份与操作入口见 [硬件实验](../2026-10-19-steering-gpu/README.md)。

部署仓库的诊断兼容补强后，e428679 Actions 实际为 438 tests、CPU gate 108 passed、10 jobs 成功；184 项系列及 efc tree 不变。硬件证据是有边界的实验结果，候选尚未推广。

## 前期 c035 结果

| 检查 | 结果 |
| --- | --- |
| macOS CPU 合约与真实 host 钩子 | 修复后精确 prepared tree：108 passed，0 skipped；外部筛选参数与插件隔离后仍执行完整集合 |
| 本地 Linux x86_64 CPU 容器 | 同一 prepared tree：108 passed，0 skipped，且与独立用例清单一致；仅 hash-pinned CPU 依赖，无 vLLM serving/CUDA 安装 |
| 首次 profiling 回归 | 四个参数 case 对应 loaded/unloaded 两条不同 runner 路径；旧版均在新增断言失败，修复后到达 eager model forward；未覆盖 worker 的默认/显式 KV 分支 |
| gate 部分执行反例 | 真实 pytest 只执行 1 项并生成成功 JUnit，完整 gate 拒绝；另有重复替代、缺失模块及外部筛选环境的单元回归 |
| gate 状态负例独立性 | 针对本轮测试补强的 6 项单元检查通过；内存中仅移除状态 guard，完整清单的失败/错误/跳过及零执行共 4 组断言均发现该变异；未改 gate 实现 |
| 新增 CUDA 消费融合与动态图回归 | 已编写，未执行；与既有 CUDA/完整 serving 检查共 29 项在 CPU 上跳过 |
| 父版本类型/旧 API 检查对照 | 现有错误已分离；见下文 |
| 新候选完整 183 补丁、head/tree/patchset | 全新 vLLM checkout 从固定 base 完整重放通过，身份与 [base.json](base.json) 一致，working tree clean，最终 diff check 通过；保留 0189/0190 原内容，追加 0191 |
| 全部 build inputs 与 build check | 通过；未变更的组件和 wheels 从已验证缓存建立独立 APFS 副本，重新核对 head/tree/submodules/SHA，再完整导出 contexts；没有构建镜像 |
| 部署仓库 CPU suite | 隔离仓库与示例节点文件：436 tests，OK，0 skipped，日志里的 servebench 数值来自单元测试模拟服务 |
| 候选 doctor、release test 分类 | configuration OK；122 selected：65 gate、57 excluded、0 problems |
| CI 与 helper | 独立 CPU/replay job；helper ruff 通过。此前 fork 的 validate 工作流为 disabled_fork，已启用；远端结论以 PR 对应 SHA 的 Actions 结果为准，不把本地成绩写作 CI 已通过 |
| 镜像构建、GPU 编译/数值、TP4、TPS、1M 请求 | 未执行 |

CPU fixture 只替换设备/编译器依赖，执行实际 ModelState、mHC Python 消费钩子与 consumer 公式；它不证明 TileLang/Triton 生成代码可编译或 GPU 数值正确。新增 GPU 测试要求实际融合输出与独立 projector 后的 stock mHC 对照，并在一张 active 图中改变 101 次强度。

新增启动回归从源码加载完整 `profile_run`、`_dummy_run`、`execute_model` 方法体及实际 descriptor/dispatch，移除装饰器并替换设备、分配器、连接器及独立 attention-cache profiling 边界；使用真实 DeepSeek ModelState 输入准备，断言 manager 为空时不选择图、不准备 steering 控制行且执行 eager forward。它针对已复现的首次 profiling 阻断，不宣称验证完整 GPU 构造/初始化链。CED/SP fixture、真实 OpenAI 请求类及父版 stock oracle 的覆盖边界仍按 README 的合并前顺序处理。

`explicit_kv_bytes` 在此 fixture 中只设置配置字段，被测 runner 方法不读取它，因此不能将参数笛卡尔积称作四条启动分支。worker 内存探测的两条真实预算分支仍留待单独验证。本轮保留 0191 内容，收窄说明，不为增加覆盖计数扩大运行时代码改动。

gate 的失败/错误/跳过负例现以完整 108 项报告为基准，分别只改变一个结果状态；断言状态检查自己的错误信息，集合核验不能再掩盖状态断言被删除的回归。

原 104 项、182 补丁与 432 项成绩对应审查前的 `a8ac315`，不作为本次新增回归的证据。

## 检查过程中的问题

- 首次干净 Linux CPU 收集因缺少 `regex` 失败；补入 CPU 输入依赖并重新生成 SHA-256 lock 后通过。初次日志保留于本地运维证据目录。
- 本地 CPU fixture 的缺失字段与隔离包导入顺序问题已修正，并分别独立运行与完整收集；没有以跳过替代修复。
- 部署仓库首轮 431 项运行有 1 项失败：新增 TileLang 实验尚未登记在既有明确列表中。补齐列表，保留原断言，并增加候选保持原运行参数、独立名称/镜像/缓存的回归检查；完整 432 项重跑通过。
- 全量 pre-commit 首次报告新增类型问题，均已处理。与精确父版本对照，当前剩余 9 条 mypy 提示全部来自父版本的 Engram/CED/ModelState；父版本原检查为 33 条，含 TileLang callable tensor declarations 和已补齐的 host buffer 前置条件。DSL 仅排除 `valid-type` 误判，其余错误类别保留。
- `model_runner.py` 的 `torch.cuda.synchronize()` 旧 API 提示也在精确父版本存在。本次不修改该成本 profiler。来源提交的本地 commit hook 对 `mypy-3.10` 与该旧 API hook 作了有记录的跳过；其余适用检查继续执行。不能把全套 pre-commit 报为全绿。
- 完整重放收到中间补丁的空行告警；最终从 base 到新 head 的 `git diff --check` 返回 0，完整 head/tree 校验通过。
- 本轮首个重放因开发提交的 committer 与构建器固定的 patch committer 不同，head 核验失败；保留该失败记录，按构建器相同的 deterministic `git am` 流程规范化新增补丁和身份后，从固定 base 再次完整重放通过。没有放宽 head/tree 检查。
- 部署仓库首次使用系统 Python，缺少 jsonschema 导致 5 项跳过；保留初次日志，改用已安装 bench-client 所需依赖的隔离 Python 环境重跑完整集合。

本轮证据保存在本地 HomeLab 的 `ops/steering-optimization-20261018/evidence/`，包括初次失败、修复后的输出、精确父版 lint 对照、JUnit、重放身份、完整 prepare/check 和部署 CPU suite 日志。OMP 的本地配置备份另存于该运维目录的私有子目录，不属于此部署仓库。

## 复现入口

在部署仓库中使用候选专用的忽略示例节点文件，不覆盖实际站点配置：

```sh
bin/spark --shape tp4-ring --cluster-config experiments/2026-10-18-steering-consume/cluster.json doctor
bin/spark --shape tp4-ring --cluster-config experiments/2026-10-18-steering-consume/cluster.json build prepare --only vllm
```

CI 使用 Python 3.12、Linux x86_64：

```sh
python3 -m venv .work/steering-cpu-venv
.work/steering-cpu-venv/bin/python -m pip install --require-hashes -r requirements/steering-cpu-linux.txt
.work/steering-cpu-venv/bin/python scripts/check_steering_cpu.py --upstreams experiments/2026-10-18-steering-consume/upstreams.lock.json
```

完整 inputs 检查是另一项本地检查：

```sh
bin/spark --shape tp4-ring --cluster-config experiments/2026-10-18-steering-consume/cluster.json build prepare
bin/spark --shape tp4-ring --cluster-config experiments/2026-10-18-steering-consume/cluster.json build check
```

这四条命令不构建或启动服务。任何后续节点操作仍按新的维护窗口与协调流程执行。

当前性能结论与可复核证据以 [硬件结果](../2026-10-19-steering-gpu/RESULTS.md) 为准：API 观测指纹不等于整模型隐藏张量位一致；命名 steering 事件检查不等于通用 copy/SP 物化证明；C4 首 token 延迟差异仍未解释。

验收工具补强后：25 项新增回归通过，隔离 staged-tree 导出的完整部署 suite 为 463 tests、OK、0 skipped；严格重验原有成功请求 79 次、原存指纹 33 个均不变，公开 API/mHC/JUnit 与 16 份 trace 的 CPU 解析通过。没有新 GPU 执行，运行时 184 项/efc tree 不变；原始测量不因工具升级而视作新增硬件验收。
