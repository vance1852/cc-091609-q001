# 中药材来源鉴别证据链

中药检验机构的来源鉴别服务：把送检批次、封签、分样、性状与显微观察、薄层色谱、
DNA 检测及参考标准串成一条可核验、只增不改的证据链。鉴定结论一旦脱离取样过程
便不能用于争议处理，因此系统强制“结果必须挂到留样、留样必须能追到原始封签”。

`domain/contracts.py` 定义样品（`Specimen`）、检验结果（`TestResult`）和签发意见
（`SignedOpinion`）的不可变数据边界；`fixtures/specimen_chain.json` 是一次脱敏的
复检与换标争议记录。

## 业务规则

- **来源与数量守恒**：每次分样沿 `parent_id` 挂接，同一母样分出子样质量之和不得
  超过封签质量；任何留样可逐级追溯到原始封签样。
- **取样过程强制**：检验结果必须注明取自哪份留样；未注明留样的报告（如补来却没
  写留样编号的 DNA 条形码报告）拒绝进入证据链，拒收动作本身留痕。
- **方法版本时效**：报告只能引用检验观察当时已生效且未废止的方法版本；新版本不
  能追溯套用旧报告。
- **原始结果去重**：以 `source_digest` 标识原始结果，重复上传同一摘要不能新增检验。
- **签发阻断**：封签断裂/争议未排除、对照材料在检验当时已过期、冲突证据未处理，
  都会一次性列出全部阻断原因并阻止签发。
- **职责分离**：鉴定师、复核人、签发人必须为不同人员（即便同一人持有多个资格，
  也不能在同一份意见上兼任）。
- **意见冻结与补充意见**：意见签发后冻结；迟到的仪器结果不得改写原意见，只能
  形成 `supplements_opinion_id` 指向原意见的补充意见。
- **签名撤销留因**：撤销签名必须由原签发人填写原因，原签发事实与记录全部保留。
- **只增哈希链**：每次状态变化（含被拒绝的动作）写入带前序哈希的审计日志，
  任何篡改都可在回放时检出。

## 模块

| 路径 | 职责 |
| --- | --- |
| `domain/contracts.py` | 不可变领域契约（已给出，未改动） |
| `chain/registry.py` | 人员职责、方法版本时效、对照材料批号 |
| `chain/specimens.py` | 收样、分样守恒、封签争议与排除 |
| `chain/journal.py` | 只增 SHA-256 哈希链日志 |
| `chain/opinions.py` | 意见生命周期、冲突证据记录 |
| `chain/service.py` | 应用服务，强制执行全部规则 |
| `chain/tracer.py` | 从最终意见倒查留样、交接、方法依据与冲突处理 |
| `chain/replay.py` | 回放 `fixtures/specimen_chain.json` 争议场景 |
| `tests/test_chain.py` | 27 条规则测试（标准库 `unittest`） |

## 使用

回放样例（无第三方依赖，Python ≥ 3.11）：

```bash
python -m compileall domain chain
PYTHONPATH=. python -m chain          # 打印可核验溯源报告
PYTHONPATH=. python -m unittest discover -s tests   # 运行测试
```

编程式用法：

```python
from chain import EvidenceChainService, Registry, Role
from chain.replay import replay, trace_final
from domain.contracts import TestKind

service, fixture, notes = replay()
trace = trace_final(service)           # 从 OP-2 追到 sealed-a、OP-1 与冲突处理
assert service.verify_journal()        # 哈希链完整性
```

回放场景覆盖：未注明留样的 DNA 报告被拒 → 显微与供应商证书冲突登记 → 封签争议导致
首次签发阻断 → 争议排除后签发 OP-1 → DNA 结果迟到形成补充意见 OP-2 → 重复上传同一
原始结果被拒。审阅者可从最终意见追到原始留样、每次分样交接、方法版本依据，并清楚
看到冲突证据如何被处理。
