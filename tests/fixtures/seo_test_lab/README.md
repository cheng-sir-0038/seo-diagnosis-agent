# 页面诊断样例

这组样例用于核对 Agent 的页面诊断是否有据可查。

| 样例 | 核对内容 |
| --- | --- |
| `healthy.html` | 字段正常时不误报 |
| `missing-title.html` | 从源码发现标题缺失，并复检修改结果 |
| `duplicate-canonical.html` | 展示多条 Canonical 声明和待核实状态 |
| `noindex.html` | 识别页面声明，保留实际收录状态为未知 |

检查入口见[页面诊断与复检回归](../../seo_agent/test_lab_diagnosis.py)。
