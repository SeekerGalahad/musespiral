---
title: Studio 演示：外链脚注缺失
date: 2026-11-11
cover: cover-missing.png
digest: 三视图演示样例之二：外链脚注与封面双 FAIL。
---

## 外链残留演示

这篇文章故意保留了一条**未被脚注机制转换**的外链：
[外部链接](HTTPS://example.com/research-everything)。

脚注转换只匹配小写 `http(s)://` 协议头，所以大写协议会原样留在 HTML 中，
`render_checks` 据此判定「外链残留」FAIL。

frontmatter 的封面文件名故意指向不存在的 `cover-missing.png`，于是队列与详情
同时亮起两项 FAIL（队列 checks 已是 run_checks + render_checks 的完整审核口径）。

![演示配图](cover.png)
