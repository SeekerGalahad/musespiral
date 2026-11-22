"""studio/handlers —— POST 动作按业务域拆分（R-2，方案 §一；行为零变更）。

六域模块各导出一张域内 ROUTES 片段，由 `studio/actions.py` 合并为唯一
注册表 `_ACTION_ROUTES`（契约锁 CONTRACT_POST_EXACT 75 = 本表 71 JSON 动作
（67 域内 + 4 actions 内联）∪ plugin_actions.POST_DYNAMIC_SURFACE 3 动态段
∪ server._BINARY_POST_ROUTES 1；漏注册由 test_architecture 红灯兜底）:

  compose.py    /api/compose/*（11；执行体=compose_actions，前批已下沉）
  materials.py  /api/materials/* + /api/sources/* + /api/ingest/*（17；
                N-4 verify、ARCH-5 reindex 等后续批次增量在内）
  article.py    /api/article/* + /api/editor/* + /api/annotations/* +
                /api/visual/*（11；执行体=edit/editor/visual/version_actions）
  platform.py   /api/action/* + /api/jobs/* + /api/settings/* + 其余（22；
                含 _runner/_submit 共享管道与 publish_flow 补丁锚点执行体）
  discover.py   发现/知识库/回采三动作（3；sync_stats/discover_draft/
                star 家族——行数门二拆自 platform）
  assist.py     AI 辅助预览三动作（3；cover/refine/tags_suggest——同上）
  （actions.py 内联 4：/api/pipelines、/api/pipelines/run、
   /api/pipelines/runs/segments、/api/plugins/refresh——PL-3 接驳批入表）

既有兄弟动作模块（compose_actions 等，Phase A 起陆续拆出）保持原位不动：
其函数被本包按域 re-export 进对应 ROUTES 片段，测试亦按文件路径/模块属性
锚定（test_tts 对 studio/tts_actions.py 的读面断言）。
"""
