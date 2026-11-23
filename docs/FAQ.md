
---

## 书籍搜索为什么提示「bookengine 未就绪」？

**这不是故障，是一个尚未建设的组件。**

书籍功能分两段：

| 段 | 状态 | 能做什么 |
|---|---|---|
| **书源协议层**（R0，已完成） | ✅ 可用 | 导入书源包、订阅管理、书源库索引、三态合并 |
| **抓取引擎**（R1-R2，未开工） | ⚠️ 未建设 | 搜索书籍 → 选章 → 取正文入素材库 |

引擎是一个独立的 JVM 微服务（`http://127.0.0.1:8792`），负责执行搜书脚本、解析书目、抓章节正文。它依赖 legado 的 Kotlin 规则引擎，属独立的建设分期，不在 musespiral 的 Python 进程里。

所以「未就绪」只影响**搜索与取文**；书源的导入、订阅刷新、库管理全部正常。

### 想先体验完整流程？开体验模式

```bash
musespiral bookengine demo on     # 或界面「书籍」页的一键按钮
~/.musespiral/local/ctl.sh restart
```

体验模式用确定性假数据跑通「搜索 → 选书 → 选章 → 入素材库」全链，内容不是真实书籍，但能验证产品形态。

```bash
musespiral bookengine demo off    # 关闭
musespiral bookengine status      # 三态查看：未建设 / 体验模式 / 可搜
musespiral bookengine check       # 四端点自检（health/search/info/toc）
```
