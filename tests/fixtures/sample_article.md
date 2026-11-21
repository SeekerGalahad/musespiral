---
title: 示例文章：渲染级验收冒烟
cover: cover.png
digest: musespiral check --render 冒烟样例：微信内链保持可点、外链转脚注、代码块带语言。
---

## 内链与外链

这一段包含一个[微信内链](https://mp.weixin.qq.com/s/abc123)，渲染后保持可点击。

还有一段[外部链接](https://github.com/wagic/researchEverything)，渲染后转成文末参考资料角标。

![本地配图](cover.png)

![实验流程图](assets/fig1.png)

## 代码示例

```python
def hello() -> str:
    return "musespiral"
```

## 结尾

以上内容用于 generate → check --render 全链路冒烟。
