# 1022 attention steering 审查修订候选

状态：离线候选，`launch_enabled=false`；未构建镜像，未运行 GPU、TP4 或服务验证。
本候选接续 [原 197 项集成](../2026-10-23-1022-attention-steering/README.md)，
修复 PR #56 审查中发现的编码器问题。本站服务仍以
`archive/r6x-attention-steering-20261023` / `223a3bb3498d12f470e876db3b9e1f5bdc8772b6`
为回退身份；完整镜像及配置引用见 [base.json](base.json)。

## 改动与身份

唯一运行时变量：把工具参数名、嵌套 JSON 键、Schema 属性名，以及参数中名为
`role` / `type` 的普通数据同样按字面文本编码。原 1022 修复只遍历值，并按键名全局跳过
`role` / `type`，这些位置仍能引入控制 token。新修复遍历键和值；合法协议标识符保持原值，
非法角色仍被拒绝。无控制符的请求继续走原编码路径。

原 197 项补丁逐字节保留并直接引用，在本目录追加 `0500-control-spellings-in-json.patch`。
[patch-map.json](patch-map.json) 记录全部旧→新引用与内容哈希；没有改写旧系列。

| 项目 | 身份 |
| --- | --- |
| vLLM 基础 | `19f2c20ed4d64ae22bd009ceacadd85e2a81e354` |
| 198 项重放 head | `dea96d04ea9eae7f7e19f1dcadd56b8e27f29875` |
| tree | `ab7b6505447360a926fee000df5dc59b2ff770ff` |
| patchset SHA-256 | `047e56e4596a22fe3574f89b9893813631d58947b1522347320a45725eb8e159` |
| 模板身份 | `e27cffc1791e4da15a669f16223dd4b7b7745e0fed48de8d61f958dea939c8cb` |
| 新 profile SHA-256 | `e06b8cb60f3f382157ef6632a93efdd90185beafa2251a8c29575f09c45cd45b` |
| 保持相同的方向 payload | `f11e3762252448f547183d4b2daaf5b58c2d9023cd5b23365bc026a402c1a8e6` |

[rebind_profile.py](tools/rebind_profile.py) 从受保护的原 r6x 文件生成独立 release。
它固定源文件、模型、模板及干净的源码身份，保留层 2–39、alpha 2.5、方向、算子和请求名称
`heretic-1017-attn-v1`。本轮没有重新提取方向，也没有修改权重。
原 r6x 和未修订 1022 的 profile 都不能用于新模板，加载器继续严格拒绝它们。
新文件和模型元数据仅保存在本地，Git 只包含 [转换收据](profile-rebind.json)。

[cluster.json](cluster.json) 使用独立镜像标签、缓存、部署目录和 profile release。
release 根目录挂载到 `/steering-1022`，实际启动文件为
`/steering-1022/steering/heretic-1017-attn-v1.safetensors`。
保留既有 1M / 16seq / 8192 batch / 10.5 GiB KV、DSpark、GID 5、late admission 和 sink 策略。
镜像标签和服务器路径均为候选计划，未创建或同步到节点。

## 验证与复现

审查范围、问题复现与结果见 [REVIEW.md](REVIEW.md)、[results.json](results.json) 和 `runs/`。
新增 28 项 JSON 用例在旧代码上失败；修复后的完整编码器集合为 66 项，覆盖 byte fixture、
真实 checkpoint tokenizer、thinking/chat、嵌套/转义/双重转义参数和 Schema。
真实 profile 检查为 11 项，包含实际候选挂载路径、确定性重绑、旧身份拒绝和错误输入拒绝。

在已安装固定依赖的 CPU 环境中执行：

```sh
candidate=experiments/2026-10-23-1022-attention-review
bin/spark --cluster-config "$candidate/cluster.json" doctor
bin/spark --cluster-config "$candidate/cluster.json" build prepare
bin/spark --cluster-config "$candidate/cluster.json" build check
python scripts/check_steering_cpu.py --upstreams "$candidate/upstreams.lock.json" \
  --cases "$candidate/cpu-cases.json" --report .work/1022-review-cpu.xml
python -m unittest discover -s tests -v

# 事先 export 本地路径：STEERING_VLLM_SOURCE 指向上述干净 prepared tree；
# STEERING_MODEL_IDENTITY 为元数据/tokenizer 快照；STEERING_OLD_PROFILE 为原 r6x 文件；
# STEERING_NEW_RELEASE 为本轮新 release；STEERING_PREVIOUS_RELEASE 为原 1022 release。
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 CUDA_VISIBLE_DEVICES='' \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 \
python -m pytest -p no:cacheprovider --confcutdir="$candidate/tools" -o addopts= -q \
  "$candidate/tools/test_encoder_offline.py" "$candidate/tools/test_profile_offline.py"
```

首次生成 release 时，目标必须不存在：

```sh
python "$candidate/tools/rebind_profile.py" --input "$STEERING_OLD_PROFILE" \
  --model-identity "$STEERING_MODEL_IDENTITY" --vllm-source "$STEERING_VLLM_SOURCE" \
  --source-manifest "$candidate/source.json" --output "$STEERING_NEW_RELEASE"
```

CI 保留 197 项旧候选 gate，并对新候选单独重放 198 项补丁、运行 126 项固定 CPU 集合，
再运行 38 项不需要真实模型文件的编码器检查。另 28 项真实 checkpoint tokenizer 用例
在该 CI 步骤明确不选取，77 项编码器/profile 全集属于本地离线证据。

## 后续边界

继续采用原候选 README 的 GPU 和晋级门槛：纯 1022 对照、未加载/scale=0/scale=1/4、
stock/active CUDA graph、SP/CED、late admission、sink 阈值与 KV 回滚、取消与缓存隔离，
以及 TP4 稳定性、质量和性能验证。本轮 CPU 结果不替代这些检查。
需要后续明确授权硬件窗口后才能构建和验证服务；当前 PR 保持草稿，不合并。
