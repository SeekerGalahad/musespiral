"""M3 多模态 + B4a 采集前端接线测试（第二十一期前端接线批）。

两层断言（任务书口径=「结构断言+API 调用断言」）：
  a) 结构断言（读构建产物 ui.html，沿 test_studio_ui.py 惯例）：七项交付的关键
     结构锚——上传三入口与二进制旁路字面量（F59）/灯箱 DOM 与键盘字面量
     （F60，即 A 正典 T60-4 结构锚）/登记模态调用体（F61）/集合轨五动作调用
     体（F62）/标签 chips+datalist+窄门调用（F63，含 12 上限琥珀提示）/搜索
     防抖 300+mark 高亮+×清空钮（F64，即 A 正典 T64-4 结构锚）/B4a rss 第四型
     与剪藏/爬取入口（preview→adopt 确认闸字面量）。
  b) API 调用断言（真实回环服务器 + http.client，tmp_path 与 MUSESPIRAL_HOME 全
     隔离）：UI 发出的真实请求形状逐一到端——upload 二进制旁路（?confirm=1
     query 特判）、register 表单体与 dedup、collections create/add/remove、
     triage tags 全量替换、?tag=/?q= 过滤与 tags 频次表、file 双 variant、
     clip/crawl 提交期 URL 门（IP 字面量 400 零网络）。
后端全量契约（T59-T69）由 GA/GB 批测试持有；本文件只锁「前端面调用的形状」。
"""
from __future__ import annotations

import http.client
import io
import json
import threading
from pathlib import Path

import pytest
from PIL import Image as _Image

from musespiral import studio

UI = Path(__file__).resolve().parent.parent / "musespiral" / "studio" / "ui.html"
# M1 前端多文件化（2026-11-18）：JS/CSS 结构标记在 assets/core.js|core.css
_CORE_JS = Path(__file__).resolve().parent.parent / "musespiral" / "studio" / "assets" / "core.js"
_CORE_CSS = Path(__file__).resolve().parent.parent / "musespiral" / "studio" / "assets" / "core.css"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    return root


@pytest.fixture
def server(studio_root: Path):
    httpd = studio.make_server(studio_root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _png(size=(60, 30)) -> bytes:
    img = _Image.new("RGB", size, (120, 40, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _req(server, method: str, path: str, body: bytes | dict | None = None,
         ctype: str = "application/json"):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if isinstance(body, dict) else body
        headers["Content-Type"] = ctype
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        data = json.loads(raw) if raw[:1] in (b"{", b"[") else raw
    except json.JSONDecodeError:
        data = raw
    return resp.status, dict(resp.getheaders()), data


def _source() -> str:
    assert UI.exists(), f"构建产物缺失: {UI}"
    return UI.read_text(encoding="utf-8")


def _views_js() -> str:
    """M3（2026-11-19）：九视图产物合并（views/*.js 全部拼接）。

    拆分后视图专属的 render*/bind* 字面量搬出 core.js 进 assets/views/<view>.js，
    断言「某字面量存在于前端某处」应读 core+全部 views 的合并面。
    """
    parts = [_CORE_JS.read_text(encoding="utf-8")]
    vdir = _CORE_JS.parent / "views"
    if vdir.is_dir():
        parts.extend(p.read_text(encoding="utf-8") for p in sorted(vdir.glob("*.js")))
    return "\n".join(parts)


def _shell_js() -> str:
    """壳+core.js+views/*.js 合并视图（HTML 静态标记与 JS 标记混断言）。"""
    return _source() + "\n" + _views_js()


def _shell_js_css() -> str:
    """壳+core.js+全部 CSS（core.css+views/*.css，ARCH-1 ② 合并域）混断言。"""
    css = _CORE_CSS.read_text(encoding="utf-8")
    vdir = _CORE_CSS.parent / "views"
    if vdir.is_dir():
        css += "\n" + "\n".join(
            p.read_text(encoding="utf-8") for p in sorted(vdir.glob("*.css")))
    return _shell_js() + "\n" + css


# ══ a) 结构断言（构建产物锚）════════════════════════════


def test_upload_three_entries_and_binary_bypass_literal() -> None:
    """F59：按钮/拖放/粘贴三入口 + 二进制旁路字面量（?confirm=1 query 特判）。"""
    src = _shell_js()
    assert "mat-upload-btn" in src and "mat-file" in src
    assert 'accept="image/png,image/jpeg,image/gif,image/webp,.pdf,.docx,.epub,.md,.markdown,.txt"' in src  # 四图片+六文档格式白名单（DOC-1）
    assert "id=\"mat-drop\"" in src and "松开上传图片" in src  # 拖放高亮框（drop-overlay 复用+indigo 改色）
    assert "$('view-material').addEventListener('drop'" in src  # 拖放入队
    assert "document.addEventListener('paste'" in src  # 粘贴入队（任务书「拖放/粘贴」）
    assert "e.clipboardData.files.length" in src
    # 二进制旁路：原始字节流 body + confirm 走 query（非 JSON dict）
    assert "'/api/materials/upload?confirm=1&name='+encodeURIComponent(r.f.name)" in src
    assert "{method:'POST',body:r.f}" in src
    # 前端预检第一层（服务端门保留不信任前端）：扩展名 + 8MiB
    assert "/\\.(png|jpe?g|gif|webp|pdf|docx|epub|md|markdown|txt)$/i" in src  # DOC-1 前端预检扩文档后缀
    assert "f.size>8388608" in src


def test_lightbox_structure_and_keyboard_literals() -> None:
    """F60（A 正典 T60-4 结构锚）：id=lightbox + Esc/ArrowLeft/ArrowRight 键位字面量。"""
    src = _shell_js_css()
    assert 'id="lightbox"' in src
    assert "id=\"lb-img\"" in src and "id=\"lb-bar\"" in src
    assert "if(e.key==='Escape')lbClose();" in src
    assert "e.key==='ArrowLeft'" in src and "e.key==='ArrowRight'" in src
    # 灯箱开启即吞单键（浮层守卫）：A 类专项（2026-11-20）起键位处理自 core 全局
    # keydown 下沉 @view:material 的 capture 监听（capture+stopPropagation 先于
    # core bubble 分支，吞 g 序列/⌘K 语义不变）；守卫取提前 return 形态
    # UIX-3 无障碍批（2026-11-23）：关闭态分支扩兼收 gen/syn 两面板 Esc——守卫
    # 形态由单行 return 改为块形态 if($('lightbox').hidden){…Esc 兼收…return;}
    assert "if(!$('lightbox').hidden())" not in src  # $ 返回元素非函数（防笔误形态）
    assert "if($('lightbox').hidden){" in src
    assert "if(gAb)gAb.abort();$('gen-panel').hidden=true;" in src  # 生成面板 Esc（含中止在途请求）
    assert "e.stopPropagation();" in src  # 吞键：capture 阶段拦断 core 全局处理器
    assert "lbClose();\nelse if(e.key==='ArrowLeft')lbGo(-1);" in src
    # 导航序=当前可见集 image 子序（matFiltered 截断序即导航序，正典 F60 交互流 3）
    assert "LB.ids=matFiltered().filter(function(m){return m.type==='image';}).map" in src
    # 全屏暗底 overlay + immutable 内容寻址 src（thumb/full 双态由 file 端点出）
    assert ".lightbox{position:fixed;inset:0;z-index:var(--z-modal);background:rgba(0,0,0,.82)" in src
    assert "variant=full'" in src and "variant=thumb'" in src
    # 关闭释放 src（回列表滚动位保持）
    assert "$('lb-img').removeAttribute('src');" in src


def test_register_modal_call_shape() -> None:
    """F61：登记模态三字段（URL+标题+描述）→ POST register 表单体；行内错误不弹 toast。"""
    src = _shell_js()
    assert "mat-reg-btn" in src
    assert "clip-sub" in src and "clip-note" in src  # 登记与剪藏共用一壳（体积门裁决的合并模态）
    assert "postJSON('/api/materials/register',{url:u,title:$('clip-title-in')" in src or \
        "postJSON('/api/materials/register',{url:u,title:" in src
    # URL 前端预检（http(s) 前缀 + 无 user:pass@）镜像服务端提交期门
    assert "!/^https:\\/\\/|^http:\\/\\//i.test(u)" in src
    assert "URL 须 http(s)" in src
    # dedup 幂等提示（同 URL 二登不新增卡）
    assert "'已登记过（幂等命中）'" in src


def test_collection_rail_and_action_call_shapes() -> None:
    """F62：集合轨段（含已删双计数）+ create/add/remove 调用体 + 退出集合 chip。"""
    src = _shell_js()
    assert "coll-manual-sec" in src and "coll-new-btn" in src
    assert "coll-tags-sec" in src  # 按标签段（F63）
    assert "mat-coll-exit" in src and 'data-mcoll=""' in src
    # 五色圆点选择（CFC 枚举=服务端 COLLECTION_COLORS 同源）
    assert "var CF={color:'indigo'},CFC=['vermilion','indigo','jade','amber','slate'];" in src
    assert "{action:'create',name:n,color:CF.color,confirm:true}" in src
    # add=全量替换（现成员 ∪ 本素材）/remove=差集（[mid]）——服务端语义镜像
    assert "{action:'add',id:cid,member_ids:(c.member_ids||[]).concat([mid]),confirm:true}" in src
    assert "{action:'remove',id:cid,member_ids:[mid],confirm:true}" in src
    # 墓碑非静默：双计数「含 m 已删」琥珀标注
    assert "（含 '+d+' 已删）" in src
    # 集合页=服务端 collection 参数（?collection=<cid> 过滤）
    assert "p.push('collection='+encodeURIComponent(state.mcoll));" in src
    # 删除素材两步 confirm 追加集合连坐声明（K 个集合显「含已删」）
    assert "该素材属 '+k+' 个集合" in src


def test_tag_chips_autocomplete_and_limit_hint() -> None:
    """F63：chips ×移除/点击过滤 + datalist 自动补全（频次表填充）+ 12 上限琥珀提示。"""
    src = _shell_js()
    assert 'class="chip-tag"' in src or "class=\"chip-tag\"+(" in src or "'chip-tag" in src
    assert "data-mtagdel" in src and "data-mtagf" in src
    assert '<datalist id="tag-dl">' in src and 'list="tag-dl"' in src
    # 频次表填充 datalist（GET /api/materials/tags 的建议层数据面）
    assert "dl.innerHTML=state.tagList.map(function(t){return '<option value=\"'+esc(t.name)+'\">';}).join('');" in src
    # 窄门=POST triage tags 全量替换；客户端规范化镜像（小写/非法转连字符/收敛/剥首尾）
    assert "postJSON('/api/materials/triage',{id:id,tags:tags,confirm:true})" in src
    assert "[^a-z0-9\\u4e00-\\u9fff-]+" in src
    # 超限琥珀提示 + maxlength=24 物理上限
    assert "已达 12 个标签上限，先移除再添加" in src
    assert 'maxlength="24"' in src
    # chip 点击=过滤深链（?tag=），matFiltered 本地同词过滤（与服务端参数双保险）
    assert "indexOf(state.mtag)>=0" in src


def test_search_debounce_mark_clear_structure() -> None:
    """F64（A 正典 T64-4 结构锚）：× 清空钮 id + mark 样式规则 + 防抖常量 300。"""
    src = _shell_js_css()
    assert 'id="mat-search-x"' in src  # × 清空钮
    assert "mark{background:color-mix(in srgb,var(--amber) 18%,transparent)" in src  # 高亮样式
    assert ",300);" in src and "setTimeout(matRefetch,300)" in src  # 防抖 300ms
    # 服务端 q（全库检索不在截断集上做）+ 全参正交拼参
    assert "p.push('q='+encodeURIComponent(state.mquery));" in src
    assert "matQS(state.mquery||state.mcoll?500:200)" in src  # 搜索/集合态提额 500
    # 截断可见性：命中 total>500 显「前 N / 共 M」
    assert "'前 '+state.mats.length+' / 共 '+state.mqTotal" in src
    # 高亮=分段 esc 再包 <mark>（词/短语/前缀各自大小写不敏感）
    assert "'+<mark>'+" not in src  # 防笔误：mark 须经 esc 段拼接
    assert "esc(m[0])+'</mark>'" in src
    # 输入框 Esc 分层：先清内容再还焦点（前端深构文 §5.1 输入框豁免同款）
    assert "if(this.value){this.value='';" in src
    # 空态搜索文案（语法提示）
    assert "全库未命中『" in src and "精确短语" in src


def test_b4a_rss_fourth_type_and_clip_crawl_entries() -> None:
    """B4a：rss 第四型注册面 + 剪藏/爬取入口 + last_refresh 展示 + preview→adopt 确认闸。"""
    src = _shell_js()
    # W2 重设计（2026-11-24）：下拉四选项 → 类型卡（radiogroup）+ 字段按型渲染
    assert "SRC_ADD_TYPES=['rss','radar_hub','kb','dir'];" in src  # 四型注册面
    assert 'id="sf-types"' in src and 'data-srcpick=' in src  # 类型卡交互
    assert "function renderSrcAdd(){" in src  # 字段按型切换
    # 健康灯「上次拉取」并入卡片统计行（F68 两键分工展示——src-stats-row）
    # W2：抽屉参数行改为按型渲染（readFields），四型字段各归其位
    # 剪藏/爬取入口按钮（源中枢 action-row）
    # W2 重设计（2026-11-24）：头排「剪藏 URL/手动爬取」两按钮并入统一搜索流
    # （粘贴网址 → 网页阅读面板 → 存入素材库 adopt / 存入待决区 crawl）——
    # 剪藏/爬取的 API 契约与确认闸语义不变，入口由源中枢搜索框承载。
    vs = _shell_js()  # 壳+全视图 JS 合并语料
    assert "rdOpen('web'" in vs  # 网页阅读入口（统一搜索/目录「读一篇」）
    assert "postJSON('/api/ingest/clip',{action:'preview',url:RD.url,confirm:true})" in vs
    assert "action:'adopt'" in vs  # 存入素材库（确认闸在 rdSave→askConfirm）
    assert "postJSON('/api/ingest/crawl',{url:RD.url,confirm:true})" in vs  # 待决区
    assert "clip:'网页剪藏'" in vs  # JN 任务标签（core 段 job 轮询消费）


# ══ b) API 调用断言（UI 请求形状到端）══════════════════


def test_ui_upload_binary_bypass_roundtrip(server) -> None:
    """F59：UI 的二进制旁路请求形状（query confirm=1 + 原始字节 body + name 参数）。"""
    png = _png()
    status, _, data = _req(
        server, "POST",
        "/api/materials/upload?confirm=1&name=" + __import__("urllib.parse", fromlist=["quote"]).quote("测试图.png"),
        body=png, ctype="application/octet-stream")
    assert status == 200 and data["ok"] is True
    assert data["material"]["type"] == "image"
    assert data["material"]["origin"]["source_id"] == "upload"
    assert data["dedup"] is False and data["thumb_ready"] is True
    # 缺 confirm → 400 同文案（UI 不放宽闸门）
    status, _, data = _req(server, "POST", "/api/materials/upload?name=x.png", body=png,
                           ctype="application/octet-stream")
    assert status == 400 and "confirm" in data["error"]
    mid = None
    status, _, lst = _req(server, "GET", "/api/materials?source=upload")
    assert status == 200 and lst["total"] == 1
    mid = lst["items"][0]["id"]
    # 灯箱两态 src 形状：thumb=WebP 缩略、full=原 PNG
    status, hdrs, body = _req(server, "GET", f"/api/materials/file?id={mid}&variant=thumb")
    assert status == 200 and hdrs["Content-Type"] == "image/webp"
    assert "immutable" in hdrs.get("Cache-Control", "")
    status, _, body = _req(server, "GET", f"/api/materials/file?id={mid}&variant=full")
    assert status == 200 and bytes(body)[:4] == b"\x89PNG"


def test_ui_register_video_shape_and_dedup(server) -> None:
    """F61：UI 表单体 {url,title,note,confirm} → dedup 幂等。"""
    body = {"url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "title": "", "note": "参考素材", "confirm": True}
    status, _, data = _req(server, "POST", "/api/materials/register", body)
    assert status == 200 and data["material"]["type"] == "video"
    assert data["material"]["payload"]["meta"]["platform"] == "bilibili"
    status, _, data2 = _req(server, "POST", "/api/materials/register", body)
    assert status == 200 and data2["dedup"] is True
    # UI 前端预检镜像的服务端门：非 http(s) → 400
    status, _, data3 = _req(server, "POST", "/api/materials/register",
                            {"url": "ftp://x/v", "confirm": True})
    assert status == 400


def test_ui_collections_and_tags_query_shapes(server) -> None:
    """F62/F63：create→add→?collection= 过滤→remove；triage tags 全量替换→?tag= 与频次表。"""
    png = _png()
    _, _, up = _req(server, "POST", "/api/materials/upload?confirm=1&name=a.png",
                    body=png, ctype="application/octet-stream")
    mid = up["material"]["id"]
    status, _, coll = _req(server, "POST", "/api/materials/collections",
                           {"action": "create", "name": "封面备选", "color": "jade", "confirm": True})
    assert status == 200
    cid = coll["collection"]["id"]
    status, _, coll = _req(server, "POST", "/api/materials/collections",
                           {"action": "add", "id": cid, "member_ids": [mid], "confirm": True})
    assert status == 200 and coll["collection"]["member_count"] == 1
    # 集合页过滤参数（UI matQS 拼参形状）
    status, _, lst = _req(server, "GET", f"/api/materials?collection={cid}&limit=500")
    assert status == 200 and lst["total"] == 1 and lst["items"][0]["id"] == mid
    # 标签窄门：全量替换语义（UI tagSet 调用形状）
    # 服务端规范化（A §1.2）：空白转连字符——"b 站" 存为 "b-站"（预览即承诺语义）
    status, _, tri = _req(server, "POST", "/api/materials/triage",
                          {"id": mid, "tags": ["封面-素材", "b 站"], "confirm": True})
    assert status == 200 and tri["triage"]["tags"] == ["封面-素材", "b-站"]
    status, _, lst = _req(server, "GET", "/api/materials?tag=" + __import__(
        "urllib.parse", fromlist=["quote"]).quote("封面-素材"))
    assert status == 200 and lst["total"] == 1
    # 频次表（datalist 建议层数据面）
    status, _, tags = _req(server, "GET", "/api/materials/tags")
    assert status == 200 and {t["name"] for t in tags["tags"]} >= {"封面-素材", "b-站"}
    # remove 差集（UI collTg 反勾选形状）
    status, _, coll = _req(server, "POST", "/api/materials/collections",
                           {"action": "remove", "id": cid, "member_ids": [mid], "confirm": True})
    assert status == 200 and coll["collection"]["member_count"] == 0


def test_ui_search_q_cross_modal_shape(server) -> None:
    """F64：?q= 跨模态（正文/标签/meta 域）+ total 不随 limit 缩水（截断可见性数据面）。"""
    png = _png()
    _, _, up = _req(server, "POST", "/api/materials/upload?confirm=1&name=shot.png",
                    body=png, ctype="application/octet-stream")
    mid = up["material"]["id"]
    _req(server, "POST", "/api/materials/triage",
         {"id": mid, "tags": ["雷达图"], "confirm": True})
    # 标签域命中（跨模态：q 搜 triage.tags）
    status, _, lst = _req(server, "GET", "/api/materials?q=" + __import__(
        "urllib.parse", fromlist=["quote"]).quote("雷达图"))
    assert status == 200 and lst["total"] == 1
    # meta 域拍平可搜（platform=upload 图？非 video——用 sha256 前缀搜 meta 域）
    sha12 = up["material"]["payload"]["meta"]["sha256"][:12]
    status, _, lst = _req(server, "GET", "/api/materials?q=" + sha12)
    assert status == 200 and lst["total"] == 1
    # 与 type 正交 AND（image ∧ 雷达图）
    status, _, lst = _req(server, "GET", "/api/materials?type=image&q=" + __import__(
        "urllib.parse", fromlist=["quote"]).quote("雷达图"))
    assert status == 200 and lst["total"] == 1
    status, _, lst = _req(server, "GET", "/api/materials?type=video&q=" + __import__(
        "urllib.parse", fromlist=["quote"]).quote("雷达图"))
    assert status == 200 and lst["total"] == 0


def test_ui_clip_crawl_submit_gate_zero_network(server) -> None:
    """B4a：UI 入口提交期 URL 门——IP 字面量/回环 400 零网络（E2E-5 语义，UI 预检镜像）。"""
    for path in ("/api/ingest/clip", "/api/ingest/crawl"):
        status, _, data = _req(server, "POST", path,
                               {"action": "preview", "url": "http://127.0.0.1:8766/x", "confirm": True})
        assert status == 400, path
        status, _, data = _req(server, "POST", path, {"url": "http://127.0.0.1:8766/x", "confirm": True})
        assert status == 400, path
        status, _, data = _req(server, "POST", path, {"url": "not-a-url", "confirm": True})
        assert status == 400, path
