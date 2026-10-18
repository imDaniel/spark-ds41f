# 请求级 steering 实现

本轮在 r6v 上验证，后续继续跟进原项目。实现保留在独立实验补丁系列，正式配置、
根 lock 和历史基线未修改；推广由维护者另行验收。

## 补丁边界

`vllm/series` 引用原有 172 个补丁，再追加 0181–0188。
精确上游 revision、重放 head、tree 和 patchset 见 [source.json](source.json)。
所有修改在源码准备阶段应用，不使用运行时函数替换。

| 补丁 | 内容 |
| --- | --- |
| 0181 | 有界 safetensors profile、模型/模板内容身份、来源及单位向量检查 |
| 0182 | 请求契约、FP32 强度规范化、首次 block hash 前的 cache salt |
| 0183 | V2 请求生命周期、固定容量 GPU 控制数据、query offsets 行映射 |
| 0184 | target WO-B 到 mHC 的投影、SP/CED 路由、启动 warm-up |
| 0185 | 显式 profiling dummy 标记；union coercion 前拒绝布尔强度 |
| 0186 | admission 失败映射到既有客户端异常，返回 HTTP 400 |
| 0187 | 顶层请求字段、[0,4]、null/FP32 零、冲突与不支持路径 |
| 0188 | attention artifact/目录/本地 bundle、转换来源与零 alpha 旁路 |

现有接入文件为 `input_processor.py`、`nvidia/model_state.py`、`nvidia/model.py`、
`nvidia/vl_model.py`、`gpu/input_batch.py` 和 `serve/engine/protocol.py`。
逻辑主体放在新增 steering 模块，chat/completion 继承既有请求协议。
前四处传递请求身份/控制数据并接入模型算子；后两处分别区分 profiling dummy 和校验 wire 输入。

投影只作用于 target 的指定注意力 writer，draft 不额外投影。
零强度保留 stock writer 位模式。SP partials 保留既有 rank-order FP32 求和和 BF16
舍入边界；compensated reduction、乘积残差与 FMA subtraction 控制近零抵消误差。
CED gather 和非对齐 SP slice 在启动阶段 warm-up，运行时使用预分配的控制缓冲区。
缓存身份包含 profile 内容身份和规范化后的强度，防止不同强度共用不兼容的前缀 KV。

## Profile 与 API

本实验 profile `heretic-1017-attn-v1` 保留现有 heretic 的真实范围：
零基层 2–39、每层 alpha 2.5、只改注意力输出，不增加 MLP。
单位方向 r 上执行 `y' = y - scale * alpha * r * (r^T y)`。
scale 0 关闭投影，scale 0.4 对应系数 1，scale 1 对应该 profile 的完整设定。
这些值属于本 profile 元数据，不是基础模型或引擎的默认 alpha。

profile 为 780,208 字节，SHA-256
`d7f0b997ea2748b03a353188583d494b2bfe7c40838aff98945941032da6a093`。
显式格式为 `gr8-steering-profile-v1+ds41-attention-v1`，模型/模板绑定、方向、逐层 alpha、
转换来源均在包中记录；缺失的原始训练来源不编造。在线不加载 pickle。
`tools/make_profile.py` 提供离线转换、打包和 materialize。

模型目录保留官方权重硬链接，只增加独立的 `steering/index.json` 与 safetensors 文件。
服务通过现有 `--additional-config` 的 `ds41_steering` 加载 profile；
`profile_path` 支持 materialized 目录、artifact 文件及本地模型内的 `hf:default` / `hf:名称`。
不同封装解析后使用同一 manifest 身份。通用原生 CLI 参数不纳入本轮。

请求用顶层 `steering_profile` 和 `steering_scale` 选择已加载配置：

```json
{
  "model": "deepseek-v4.1-flash",
  "messages": [{"role": "user", "content": "用一句话解释递归。"}],
  "steering_profile": "heretic-1017-attn-v1",
  "steering_scale": 0.4
}
```

不选择 profile 时保持原版；只写 scale 0 可接受。只写正确名称时 scale 默认 1。
非零 scale 必须同时提供正确名称；未知名称即使配 scale 0 也返回 400。
未加载配置时具名或非零请求返回 400，不静默回退。null 作为缺省处理；
布尔值、字符串、负数、大于 4 和非有限值均被拒绝。
保留 `vllm_xargs` 接入，重复来源冲突明确报错。

## 验证证据

- 完整 180 补丁重放及全部 build inputs check 通过；新增 CI job 重放实验 vLLM 系列。
- 83 项本地 CPU 合约通过；真实 GPU 25/25，通过数值、缓存、CED/SP、极端值及零 alpha 检查。
- 71 项真实 API 检查通过，包括 stock/zero、八个强度、混批、400 契约及缓存隔离。
  stock/zero token 一致，所比较 logprob 差异不超过 1e-4。
- 模型内 `hf:default` 已在四机真实启动，5 项 API 检查通过，包括未选择、零强度、
  scale 1、未知名称配零及缺名非零。
- 流式取消和 32 个连续强度值的槽位复用通过。
- DSpark 开/关 24 组比较 token 一致，所比较 logprob 差异为 0。
- 已完成 30 项小型能力 smoke；这不代表完整能力准确率。
- 部署仓库 418 项 unittest，5 项 skip，其余通过。CPU 环境跳过 CUDA 不能当 GPU 通过。
- 四机 target、DSpark 和 FULL CUDA graphs 捕获通过；本模型提示不支持 torch.compile，
  不能因配置名 FULL_AND_PIECEWISE 而宣称 PIECEWISE 已验收。

首轮 GPU 13/21 的八项近零抵消失败未放宽门槛，修正后通过。
实机 profiling dummy 和 HTTP 错误分类问题已分别在 0185、0186 修正并复验。
所有失败运行和中间产物在私有验证记录保留。

行为验收现按 [EVALUATION.md](EVALUATION.md) 的 100 条分层请求进行，仅统计拒答。
已有短性能筛查不构成完整性能或长期稳定性证明。本轮不追加能力退化评测。
100条自动拒答评分已完成，见 [结果记录](RESULTS-100-20261018.md)；未知项保留，未做独立人工验收。
首轮之后按维护者要求追加 [scale 1.2 同题探索档位](RESULTS-SCALE12-20261018.md)，
随后按维护者要求继续追加 [scale 1.5 同题探索档位](RESULTS-SCALE15-20261018.md)。
原八组及 scale 1.2 的回答和标签摘要核对不变，不将小样本的点估计差异解释为显著优劣。

## 复现入口

```sh
cp config/examples/nodes-ring4.json config/nodes-steering-example.local.json
bin/spark --shape tp4-ring --cluster-config experiments/2026-10-18-request-steering/cluster.json doctor
bin/spark --shape tp4-ring --cluster-config experiments/2026-10-18-request-steering/cluster.json build prepare
bin/spark --shape tp4-ring --cluster-config experiments/2026-10-18-request-steering/cluster.json build check
```

在准备好的 vLLM 源码根目录，用隔离依赖环境运行：

```sh
PYTHONPATH=. .venv/bin/python -m pytest --noconftest -q tests/models/test_deepseek_v4_1_steering_*.py
```

公开 `cluster.json` 禁止启动；私有配置继承原有容量、精度、DSpark、图片和主机策略，
只增加独立测试模型、缓存及只读 profile。四节点使用同一镜像 ID，48 个权重分片按设备/inode/大小
校验硬链接；不改写硬链接文件。协调启停始终使用显式配置的 `bin/spark cluster`。

## 既有入口兼容

测试服务与官方1017及heretic共用端口 `8000` 和模型ID `deepseek-v4.1-flash`，
切换服务后沿用现有DeepSeek入口。实验容器、模型目录、profile和缓存仍独立。
沿用既有reasoning parser、chat template与采样默认值，兼容现有网关思考深度映射；
不新增网关模型入口，GLM配置保持原样。历史评估的独立端口和模型ID只作为原始记录保留。
