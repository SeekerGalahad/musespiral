# 改动描述

做了什么、为什么这样改。diff 已经说明了「改了什么」——请写「为什么」。

如果踩了坑，写清楚坑是什么、怎么发现的（判例进提交信息也行，见 CONTRIBUTING.md 的示例）。

## 关联 Issue

<!-- 关联的 Issue 号，没有就删掉本节 -->

# 测试

跑过哪些门（粘贴关键输出）：

```bash
pytest -q                                   # 全量，应全绿
pytest tests/test_license_compliance.py -q  # 引入/触碰依赖时必跑
pytest tests/test_architecture.py -q        # 触碰分层边界时必跑
pytest tests/test_build_ui.py -q            # 改前端后必跑（先 python scripts/build_ui.py）
```

新增功能：附了测试吗？写测试时问自己——「把实现改错，这个断言会红吗？」不会红就是假门。

# 许可证检查

- [ ] 未引入新依赖，或新依赖为宽松许可（MIT / Apache-2.0 / BSD / LGPL）——**零 AGPL/GPL**（`tests/test_license_compliance.py` 会拦）
- [ ] 未绕过 `ALLOWLIST`

# AI 裁决检查

- [ ] 本 PR 不含 AI 能力，或新增 AI 产物默认 `draft` 态
- [ ] 提供了三件套动作：采纳 / 重新生成 / 弃用（用户能对每个 AI 判断说「不」）

# 其他

- [ ] 凭证 / 密钥 / 个人数据未进入本 PR（含测试数据——用脱敏样例）
- [ ] 改前端源（ui.js / ui.css / ui.views/）后跑过 `python scripts/build_ui.py`，产物已同步
