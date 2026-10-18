# r6x 上的下游 steering 重放

状态：源码迁移、镜像构建、29 项 GPU 检查、12 项独立 stock mHC 对照及本轮四机功能验证已完成；候选保留 Draft，性能限制见[隔离硬件实验](../2026-10-19-r6x-steering-gpu/README.md)。此前 r6v/efc 的 GPU 和性能记录保留在原实验中，不能转记为本候选的成绩。

以已集成的上游部署版本 `a48dc123` 为起点。外部 vLLM revision 仍是 `19f2c20ed4d64ae22bd009ceacadd85e2a81e354`；先完整应用 r6x 的 176 项作者补丁，再应用本目录独立的 12 项下游补丁。它们对应旧版的 0181–0192，新编号为 **0500–0511**。上游 patch 内容和命名保持原样，旧实验及其 source/series/结果不改写。

独立目录划定维护边界，0500 编号减少名称碰撞；后续上游也可能使用同样的数字，仍需按内容、依赖和适用源码核对。[patch-map.json](patch-map.json) 记录每个旧/新文件的 SHA 和重放 commit。新 mailbox 明确写入 r6x replay baseline，原 Base/Validation 段落保留为原改动的历史来源，本轮 GPU 重验和范围以新的硬件实验记录为准。

| 范围 | 内容 |
| --- | --- |
| 0500–0502 | profile、请求与缓存身份、V2 槽位和行控制 |
| 0503–0507 | attention 投影、profiling/输入校验、API 错误与顶层字段、artifact |
| 0508–0511 | stock/active 图分离、mHC consumer、对应检查、manager-less profiling 与分支内 BF16 写入 |

本次迁移没有再调整投影算法：profile `heretic-1017-attn-v1` 的 alpha=2.5、attention 层 2–39、scale [0,4]，数学及数值边界保持原样。未改权重、profile、缓存 namespace 或请求生命周期。r6x 的 routed SwiGLU、无损 expert scale 表示和固定线性 DSpark budget 来自上游；本站的新镜像吞吐、延迟和单请求容量已有独立四机记录；部分性能指标仍待验收。

源身份见 [base.json](base.json)、[source.json](source.json) 和 [lock](upstreams.lock.json)：188 项完整 series 的目标 head 为 `ebb44766d6f04d3a9272b712778d7f2fa2cec1fd`，tree 为 `ac79557915e3ee2778f8f6353a3d35f848f564e4`。12 项 source commits 无内容冲突地重放；重新导出并规范化 Git committer 后的源码 tree 与直接 rebase 结果相同。初步在该源码 tree 执行原六模块的 108 项 CPU 合约通过；完整构建链已从固定 base 重放 188 项，精确 head/tree/patchset、最终 diff、全部原生组件和 wheel 输入检查通过；正式 CPU gate 为 108 passed、0 skipped。隔离导出的完整仓库测试修复后为 464 项通过，测试分类为 124 selected / 67 gate / 57 excluded / 0 problems。最终提交的远端 CI 结果以对应 PR 检查为准。

[cluster.json](cluster.json) 是独立 TP4 测试配方，继承 r6x 通用 recipe，保留 1M、并发及兼容能力；新增 profile 挂载和启动参数。候选移除了 benchmark reference，通用示例 tag 保持未启用；实际构建的不可变镜像与站点测试配方另记在硬件实验中。真实四机配方须继续使用本站保留的模型路径和资源预算，只新增隔离配置与缓存，不用通用 recipe 覆盖现有服务。

复现命令（不启动服务）：

```sh
scripts/example_site.py
cp config/examples/nodes-ring4.json config/nodes-steering-r6x-example.local.json
bin/spark --shape tp4-ring --cluster-config experiments/2026-10-19-r6x-steering/cluster.json doctor
bin/spark --shape tp4-ring --cluster-config experiments/2026-10-19-r6x-steering/cluster.json build prepare --only vllm
.work/steering-cpu-venv/bin/python scripts/check_steering_cpu.py --upstreams experiments/2026-10-19-r6x-steering/upstreams.lock.json --cases experiments/2026-10-19-r6x-steering/cpu-cases.json --report .work/steering-r6x-cpu.xml
```

四机阶段先比较 r6x stock 与相同基线的 loaded scale=0，再比较同 profile/scale 的旧/新实现；固定工作量的 step/kernel 成本与 DSpark 接受率、真实吞吐和 TTFT 分开报告。按独立启动交错顺序保留全部窗口。只有实测支持的优化进入候选，新的图、精度、SP/CED、混合请求与容量门槛须在当前源码树重新执行。

首次本地 doctor 发现新示例配方沿用了通用 master 地址，与独立 ring4 示例节点图不一致；已把新候选固定到相同的示例 head，并使用新的 ignored 节点映射与缓存目录。没有改写已有节点图、配置或服务。完整重放此前已成功，配方修正后重新执行 doctor。

首次完整仓库检查记录为 464 项、2 项失败：新实验未登记在明确的 TileLang 列表，旧 hardware official 配方又隐式依赖了已切到 r6x 的根 lock。已补列表登记，把历史 official 配方显式固定到保留的 r6v lock，并在 [history](history/README.txt) 保留其修改前的完整字节与 SHA；历史实测结果和节点上的配方未改写。

新候选的部署来源显式使用自有 fork、当前开发分支与独立 checkout 路径，并与原实验一样保留 `launch_enabled=false`；站点测试配方另行明确启用。自查修正了从通用 recipe 继承的上游仓库地址和启用状态，源码迁移阶段没有对节点执行部署；后续已获授权的隔离硬件实验单独记录。
