# bookengine 接入指引（JVM 引擎，R1-R2）

> 状态：**引擎本体未建设**（D3 裁决 2026-11-24：不做，改为此指引）。
> musespiral 侧的客户端契约已完整钉死，本文件说明「有人愿意接时怎么做」。

## 一、为什么是独立 JVM 服务

legado 的书源规则引擎（jsoup 索引语法 + Rhino JS 桥 + OkHttp 请求层）是 Kotlin/JVM 生态。Python 重写「阅读语法」的长期兼容成本远高于维护一个独立进程（架构正典 §二 裁决 A）。

## 二、契约（musespiral 侧已实现，不可改）

客户端：`musespiral/infra/bookengine.py`，**回环钉死 `http://127.0.0.1:8792`**（无构造参数、不读环境变量）。

| 端点 | 方法 | 请求 | 响应 |
|---|---|---|---|
| `/health` | GET | — | `{ok, version, sources_loaded}` |
| `/search` | POST | `{sources:"all"\|[...], key, page}` | `{ok, items:[{name,author,kind,wordCount,lastChapter,intro,coverUrl,bookUrl,origin,originName}], searched, total}` |
| `/book/info` | GET | `?sourceUrl=&bookUrl=` | `{ok, info:{name,author,intro,kind,wordCount,lastChapter,coverUrl,tocUrl}}` |
| `/book/toc` | GET | `?sourceUrl=&bookUrl=` | `{ok, chapters:[{title,url,index?}], total}`（翻页由引擎侧合并完） |
| `/book/content` | GET | `?sourceUrl=&bookUrl=&chapterUrl=` | `{ok, title?, content:str}`（nextContentUrl 分页合并与源级 replaceRegex 已在引擎侧执行） |

**形状校验**：客户端用 `_require_shape` 逐字段校验，形状不符=集成故障（不静默）；连接失败=`BookEngineUnavailable`→灰灯降级。

## 三、接入步骤（建议路径）

1. **取 legado 规则引擎**：从 `LegadoTeam/legado` 剥离 `BookSource` 规则执行链（`AnalyzeRule` / `AnalyzeUrl` / `WebBook`），去掉 Room DAO 与 Android WebView 依赖
2. **补 HTTP 层**：上面五个端点，请求层用 OkHttp（限流语义沿 legado `concurrentRate`）
3. **起服务**：`127.0.0.1:8792`，读取 `~/.musespiral/booksources/*.json` 作为规则库（规范 JSON 键名不可变）
4. **验证**：
   ```bash
   musespiral bookengine status          # 应显示 🟢 可搜（N 源已加载）
   musespiral bookengine check           # 四端点逐项自检
   ```
5. **关闭体验模式**（若曾开启）：`musespiral bookengine demo off && ctl.sh restart`

## 四、当前可用替代

```bash
musespiral bookengine demo on   # 确定性假数据跑通全流程（搜索→选书→选章→入素材库）
```

## 五、数据面

| 路径 | 内容 |
|---|---|
| `~/.musespiral/booksources/<sha1前16>.json` | 规范书源（键名不可变，legado 兼容） |
| `~/.musespiral/booksources/index.json` | 轻量投影（列表页不载规则大字段） |
| `~/.musespiral/booksource_subs.json` | 订阅表 |

实测规模：aoaostar 主流水源包 3,907 源 / 20.9MB，导入后落库 2,583 源（去重 1,323）。
