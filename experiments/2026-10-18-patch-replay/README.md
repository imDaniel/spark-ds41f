# P0：隔离基线补丁重放的自动 Git 维护

状态：**P0 本地 CPU/源码复现通过**。vLLM 完整补丁重放、跨系统源码导出、完整构建输入准备与 `build check` 均通过。
本轮采用当前稳定版本 r6v 验证，后续开发持续基于原项目演进。

## 问题与范围

部署仓库实验基线为 `11d31712a5821948f3cbfe14c0ae8419efb22a81`。
原 `bin/spark --shape tp3-triangle build prepare --only vllm` 在 macOS 上先后于
0154、0103 报缺失 blob、`upload-pack: not our ref` 和 `git write-tree` 失败。
缺失对象包含由较早补丁新建、并非正在应用的补丁所修改的文件。

本实验只改变临时项目源码仓库的自动维护设置，不修改作者的 172 个 vLLM 补丁、
源码 lock、历史 manifest、模型、正式 recipe 或部署逻辑。不连接线上节点，不运行模型或 GPU。

## 诊断证据

- 默认配置下，独立手动重放曾成功；原始 helper 再次失败且停止位置改变，保留两次失败日志。
- 本地 Linux ARM64、Git 2.47.3、Python 3.13.5 使用原 helper，从空构建树抓取后完整重放成功。
- macOS、Apple Git 2.54.0、Python 3.15.0 的带 trace 诊断重放记录了 172 次 maintenance、
  130 次 repack，出现多个并发对象库操作、锁冲突和 pack 文件消失错误。该诊断复用上游
  promisor packs，仅用于定位；它最终 head 符合预期，不能把它当成独立的全新抓取验证。
- partial clone 遇到缺失对象会尝试向 promisor remote 获取，因而可能把本地新建对象的
  缺失表现为远端 `not our ref`；远端错误本身不能证明上游遗漏了文件。机制参见 [Git partial clone](https://git-scm.com/docs/partial-clone)。

证据支持在批量重放期间隔离自动 repack 的修复。尚未证明 Git 内部的具体缺陷，
也不将此记录解释为所有 macOS/Git 版本都会失败。

## 修复

`bin/spark:prepare_project()` 在 `git init` 后、第一次 fetch 前，在新建临时源码仓库内设置：

```sh
git config --local maintenance.auto false
git config --local gc.auto 0
```

临时仓库随后仍使用原来的 filtered fetch、逐个 `git am`、固定 committer/date、
head/tree 校验和原子目录替换。设置随这个可丢弃的源码 checkout 保留，不修改主仓库或
用户的全局 Git 配置；导出不包含 `.git`，不会改变运行源码或镜像上下文内容。
已经验证可复用的旧源码树不作额外修改；需要重建时自动使用此隔离设置。

## 验证

```sh
# 使用仓库示例节点文件；recipe 本身保持原样。
bin/spark --shape tp3-triangle doctor
bin/spark --shape tp3-triangle build prepare --only vllm
bin/spark --shape tp3-triangle build prepare
bin/spark --shape tp3-triangle build check
python3 -m unittest discover -s tests -p test_build.py
python3 -m unittest discover -s tests
git diff --check
```

修复后从空源码目录分别在 macOS 和本地 Linux 容器抓取并完整应用 172 个补丁：

| 身份 | 两端结果 |
| --- | --- |
| vLLM parent | `19f2c20ed4d64ae22bd009ceacadd85e2a81e354` |
| patchset SHA-256 | `00b1018e0078de4fc986554d9eccc8ed1e7f404e54268ba4e71054e03396c0c4` |
| patch head | `c001cc4ed77bef686ddd006be0dd706b9c15d7b7` |
| Git tree | `d11e9836e61bc2b763d6805d3324ec8fb5376360` |
| vLLM 导出 context SHA-256 | `398e3570245a35cce7d013ff02335dd4467bc78ce4aef764c60b4518dcc20170` |

macOS 修复后的 trace 中自动 maintenance 子进程为 0；`git fsck --connectivity-only --no-dangling`
通过，head/tree/cleanliness 检查通过。新增网络无关的真实 Git 回归测试：在开启自动维护的
模拟 host 配置下抓取两个本地补丁、重放、故意损坏工作树、重建，再检查相同 head/tree、
文件内容、导出无 `.git`、无自动 maintenance/GC 子进程和 host 配置原样保留。

完整构建输入也已在 macOS 准备通过：vLLM、B12X、NCCL、TileLang（含递归 submodules）、
TileKernels、sparknet、CUTLASS 和 hash-locked CuTe DSL wheels。`build check` 重新核对全部源码
身份及导出上下文，退出 0。原有 CPU suite 加新增回归用例共运行 418 项、跳过 5 项，整体通过。
静态 `doctor` 和 `git diff --check` 通过。

[结果](results.json) 与 [日志](runs/) 保留了失败和成功记录；公开日志中的本地绝对路径已移除。
源码准备不等于原生扩展编译、镜像构建、模型运行或 TP4 验收。

## 维护

本修复在自己的 fork 中独立评审，默认不向原作者提交。后续 steering 的 P1–P7 基于通过
本轮校验的源码树继续；未来上游升级须重放新的基线、重新适配并验证，不能沿用本轮结果。
