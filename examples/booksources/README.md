# browserRule 示例书源（W18，2026-11-24）

这三个 JSON 示例可直接经 `musespiral booksource import <file>` 导入。
`browserRule` 是 musespiral 对 legado 书源格式的**增量扩展**子对象——不动
任何原字段（键名不可变铁律），只给 Playwright 引擎提供纯 CSS 选择器规则。

## 规则写法

```json
{
  "bookSourceUrl": "站点根 URL（同时作为书源 PK）",
  "searchUrl": "搜索页模板，{{key}} 占位符会被 URL 编码",
  "browserRule": {
    "search": {
      "list": "结果列表项的 CSS 选择器",
      "fields": {
        "name": "h3 a",
        "bookUrl": {"sel": "h3 a", "attr": "href"},
        "author": ".author"
      }
    },
    "info":   {"fields": {"name": "h1", "intro": ".desc"}},
    "toc":    {"list": "目录链接", "fields": {"title": ".", "url": {"sel": ".", "attr": "href"}}},
    "content":{"sel": "正文容器", "title": {"sel": "h1", "attr": "text"}}
  }
}
```

字段简写：字符串 = 选择器 + 按字段名推断属性（`name`/`title`/`intro` 等取
text，`*Url` 取 href，`cover*` 取 src）；完整形态 `{"sel","attr"}` 显式指定。
`"."` 表示列表项自身。

## 示例

- `example-generic.json`：通用模板（占位 URL，按站点实际 CSS 修改）
- `example-li-alias.json`：字段别名 demo（author/kind/lastChapter 简写）
- `example-minimal.json`：最小规则（仅搜索，演示渐进式补充）

**注意**：真实网站页面结构会变，CSS 选择器需要用户自行维护——这是
Playwright 路线的已知代价（换来的是零 JVM/Kotlin 依赖 + Python 可贡献）。
