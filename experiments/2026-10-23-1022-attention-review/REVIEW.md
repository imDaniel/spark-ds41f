# PR #56 代码审查

审查日期：2026-10-23。范围为部署基线 `223a3bb3498d12f470e876db3b9e1f5bdc8772b6`
至审查起点 `a228375400bbf3b090e0921165adfd5dd852ace5` 的全部 PR 变更，
以及实际组合后的 vLLM 源码。不是仅查看 CI 或补丁能否应用。

## 范围

起点共有 488 个变更文件，其中 438 个与固定上游
`26aebf209534a2d43799bf4be7b7e344efe97c7c` 的 Git blob 相同。
逐文件来源清单见 [review-inventory.json](runs/review-inventory.json)。
历史 benchmark、overlay 和基线记录按来源、引用和不可变性核对，未当作本站 GPU 验收。

重点检查了候选配置与模型/profile 挂载、源码及构建输入身份、重绑和拒绝路径、请求参数与
prefix-cache 隔离、请求槽位及 token 行布局、attention writer 到 mHC/SP/CED 的传递、
stock/active graph 分支、late admission、sink draft-zero 与调度/grammar/输出交互、
控制 token 编码、benchmark 错误处理和 CI 覆盖。对新可执行代码结合调用方和已有测试检查；
GPU kernel、设备同步和四节点效果仅作静态审查，不声称已执行。

## 已修复问题

### P1：候选启动路径没有指向 release 内的实际文件

release 根目录挂载在 `/steering-1022`，生成文件位于其 `steering/` 子目录；
旧 `--additional-config` 却指向 `/steering-1022/heretic-1017-attn-v1.safetensors`。
按该候选启动时会在实际 profile 加载阶段找不到文件。旧离线检查直接选择文件，绕过了配置路径。

修复为 `/steering-1022/steering/heretic-1017-attn-v1.safetensors`。
新增仓库回归对照转换收据与挂载相对路径，并让真实加载器通过候选配置定位本地 release。
路径回归在修复前失败；旧候选及修订候选均通过修复后的检查。

### P1：工具 JSON 键和协议同名字段绕过控制符保护

1022 的 `_strings` 只检查映射值，`_protect` 也只替换值，同时全局跳过键名为
`role` / `type` 的字段。然而参数名、嵌套 JSON 键和 Schema 属性名同样会写入 prompt；
`role` / `type` 在工具参数和 schema properties 中也可以是普通数据。
真实 tokenizer 的复现显示，这些文本中的 `<｜end▁of▁sentence｜>` 会额外生成 token ID 1，
改变模板控制序列。

修复递归处理键和值，取消全局键名豁免，并把键包含的字符纳入 sentinel 冲突检查。
合法协议标识符无需替换；非法角色仍拒绝。新增直接、嵌套、转义、双重转义参数键、
协议同名字段与 schema 属性测试，覆盖 thinking/chat 和两种 tokenizer。
修复前扩展集合为 28 失败、38 通过；修复后为 66 通过。

该修复改变模板身份，因此新建本实验、保留旧 197 项系列，追加一项补丁并重新绑定相同方向。
没有修改旧 source/profile 记录或放宽加载器身份检查。新 profile 拒绝复用旧 r6x 和未修订 1022 绑定。

### P2：stall benchmark 会把部分失败解码计入成功统计

若后台解码流的时间戳覆盖了 foreground prefill，但流记录带有服务端错误，
旧 `stall.measure` 仅检查覆盖范围，返回 `error=None`。该样本会进入成功的 stall/吞吐统计。

修复聚合后台流错误并将该轮标记失败。回归使用覆盖完整时间区间但带错误的流，确认失败计数增加，
成功指标样本数为零。正常主动停止仍保持无错误；前台错误继续保留。

## 验证与限制

最终计数与原始输出见 [results.json](results.json) 和 `runs/`。本轮验证包括：

- 新目录从固定基础提交完整重放 198 项补丁，核对 head、tree、patchset 和干净状态。
- 默认与候选 doctor、完整构建输入检查、源码及部署 diff whitespace 检查。
- 126 项固定 steering CPU 集合；66 项编码器和 11 项真实 profile 检查。
- 38 项不依赖 checkpoint 文件的编码器 CI 集合，另 28 项真实 tokenizer 用例明确不选取。
- 492 项仓库完整回归全部通过，包含新增路径/错误处理用例；release 分类为 129 文件、72 gate、57 excluded、0 问题。
- CI 同时保留原 197 项候选和新 198 项候选，分别执行其固定 CPU 集合；新候选还检查编码器。

原始失败尝试保留：编码器修复前失败报告、首次加入新候选时未登记 TileLang profile 的仓库测试失败。
新增候选的 image tree label 曾被 doctor 拒绝，已同步至新 source tree；未通过任何启动或服务操作绕过检查。

本次审查发现的三项问题均已修复。未构建镜像、未运行完整 release gate，未执行 GPU 或 TP4 验证。
sink 阈值在 steering 后的分布、draft-zero/KV 回滚、两套 CUDA graph 的 late-admission 事件、
多模态在线路径及四节点质量/性能仍须后续硬件验证。当前结论仅支持继续离线集成，不能据此晋级服务。
