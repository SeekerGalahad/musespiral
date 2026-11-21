"""AI 输出缓存（§五 F79-1 key 五元组 / F79-4 条目淘汰 / F79-6 --clear 零副作用）。

全部用例在 MUSESPIRAL_HOME 沙箱里跑（monkeypatch 环境变量，不触真实 ~/.musespiral）。
"""
from __future__ import annotations

import json

import pytest

from musespiral.infra.ai import cache


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


BASE = dict(
    task="tag_suggest",
    profile="local",
    temperature=0.6,
    template_sha_value="tpl-abc",
    prompt_sha_value="prompt-xyz",
)


# ── F79-1：key 五元组 ───────────────────────────────────────────────


def test_key_stable_for_same_inputs():
    k1 = cache.cache_key(**BASE)
    k2 = cache.cache_key(**BASE)
    assert k1 == k2
    assert len(k1) == 16 and all(c in "0123456789abcdef" for c in k1)


def test_key_changes_on_each_component():
    base = cache.cache_key(**BASE)
    variants = {
        "task": cache.cache_key(**{**BASE, "task": "agent_extract"}),
        "profile": cache.cache_key(**{**BASE, "profile": "cloud"}),
        "temperature": cache.cache_key(**{**BASE, "temperature": 0.7}),
        "template_sha": cache.cache_key(**{**BASE, "template_sha_value": "tpl-other"}),
        "prompt_sha": cache.cache_key(**{**BASE, "prompt_sha_value": "prompt-other"}),
    }
    assert all(v != base for v in variants.values()), "五元组任一变须 new key"
    assert len(set(variants.values()) | {base}) == 6, "五个变体互不相同"


def test_temperature_float_repr_normalization():
    """0.6 与 0.60 视为同值（float repr 归一——`repr(0.60)=='0.6'`）。"""
    k1 = cache.cache_key(**{**BASE, "temperature": 0.6})
    k2 = cache.cache_key(**{**BASE, "temperature": 0.60})
    assert k1 == k2
    # 但真不同值仍不同 key
    assert cache.cache_key(**{**BASE, "temperature": 0.61}) != k1


def test_prompt_sha_preserves_message_order():
    """prompt_sha 保插入序不 sort_keys：role/content 序列即语义。"""
    a = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    b = [{"role": "user", "content": "U"}, {"role": "system", "content": "S"}]
    assert cache.prompt_sha(a) != cache.prompt_sha(b)


def test_prompt_sha_ensure_ascii_false():
    """中文不被转义（ensure_ascii=False——同文本同摘要）。"""
    msgs = [{"role": "user", "content": "中文"}]
    assert cache.prompt_sha(msgs) == cache.prompt_sha(msgs)


def test_template_sha_none_and_empty_same():
    assert cache.template_sha(None) == cache.template_sha("")
    assert cache.template_sha("x") != cache.template_sha("")


# ── 条目读写与形状（无 prompt/密钥正文）──────────────────────────────


def test_put_then_get_roundtrip(env):
    key = cache.cache_key(**BASE)
    entry = cache.put(
        key=key, task=BASE["task"], profile=BASE["profile"],
        temperature=BASE["temperature"], template_sha_value=BASE["template_sha_value"],
        text="生成的标签行", created_at="2026-11-17T10:00:00",
    )
    assert set(entry) == set(cache.ENTRY_KEYS)
    assert entry["chars"] == len("生成的标签行")
    assert cache.read_text(key) == "生成的标签行"


def test_entry_has_no_prompt_or_text_body(env):
    """红线：条目只存 text（输出）+ 元数据，**无 prompt 原文、无 api_key**。"""
    key = cache.cache_key(**BASE)
    cache.put(
        key=key, task="tag_suggest", profile="local", temperature=0.6,
        template_sha_value="t", text="输出", created_at="2026-11-17T10:00:00",
    )
    raw = json.loads(cache.entry_path(key).read_text(encoding="utf-8"))
    assert set(raw) == set(cache.ENTRY_KEYS)
    for forbidden in ("prompt", "messages", "api_key", "key_secret"):
        assert forbidden not in raw, f"缓存条目不得含 {forbidden} 字段"


def test_get_missing_returns_none(env):
    assert cache.get("0123456789abcdef") is None
    assert cache.read_text("0123456789abcdef") is None


def test_get_corrupt_file_returns_none(env):
    (cache.cache_dir()).mkdir(parents=True, exist_ok=True)
    (cache.entry_path("0123456789abcdef")).write_text("{not json", encoding="utf-8")
    assert cache.get("0123456789abcdef") is None


def test_entry_path_rejects_bad_key():
    for bad in ("short", "../etc/passwd", "zzzzzzzzzzzzzzzz", "G" * 16):
        with pytest.raises(ValueError, match="16 位十六进制"):
            cache.entry_path(bad)


def test_get_with_bad_key_is_miss_not_crash(env):
    """非法 key 走读侧 → None（不抛——读侧宽容）。"""
    assert cache.get("../boom") is None


# ── F79-4：条目淘汰 200 LRU by created_at ──────────────────────────


def _seed(env, n, prefix="k"):
    """写 n 个条目，created_at 单调递增（秒级不足 → 用可排序 ISO 串）。"""
    keymod = cache
    made = []
    for i in range(n):
        key = f"{i:016x}"
        # created_at 用 6 位小数确保严格单调（同一秒内也能按序淘汰）
        stamp = f"2026-11-17T10:{i // 60:02d}:{i % 60:02d}.{i:06d}"
        keymod.put(
            key=key, task="t", profile="p", temperature=0.6,
            template_sha_value="s", text=f"body-{i}", created_at=stamp,
        )
        made.append((key, stamp))
    return made


def test_evict_keeps_max_entries_and_drops_oldest(env):
    made = _seed(env, cache.MAX_ENTRIES + 1)
    entries = cache._iter_entries()
    assert len(entries) <= cache.MAX_ENTRIES
    # 最旧（i=0）应已不在
    oldest_key = made[0][0]
    assert cache.get(oldest_key) is None, "created_at 最旧者须被淘汰"
    # 最新仍在
    assert cache.get(made[-1][0]) is not None


def test_evict_noop_when_under_limit(env):
    _seed(env, 5)
    assert cache.evict() == 0
    assert cache.stats()["entries"] == 5


def test_put_triggers_eviction_at_limit(env):
    """第 201 次 put 自动淘汰（put 内部调 evict）。"""
    _seed(env, cache.MAX_ENTRIES)
    assert cache.stats()["entries"] == cache.MAX_ENTRIES
    cache.put(
        key=f"{cache.MAX_ENTRIES:016x}", task="t", profile="p", temperature=0.6,
        template_sha_value="s", text="newest", created_at="2026-11-17T23:59:59.999999",
    )
    assert cache.stats()["entries"] == cache.MAX_ENTRIES


# ── stats / clear ───────────────────────────────────────────────────


def test_stats_shape_and_by_task(env):
    _seed(env, 3)
    s = cache.stats()
    assert s["entries"] == 3
    assert s["max_entries"] == cache.MAX_ENTRIES
    assert s["by_task"] == {"t": 3}
    assert s["oldest"] is not None
    assert s["chars"] == len("body-0") + len("body-1") + len("body-2")


def test_stats_empty_dir(env):
    s = cache.stats()
    assert s["entries"] == 0 and s["by_task"] == {} and s["oldest"] is None


# ── F79-6：--clear 零副作用 ──────────────────────────────────────────


def test_clear_removes_everything_and_returns_count(env):
    _seed(env, 4)
    removed = cache.clear()
    assert removed == 4
    assert cache.stats()["entries"] == 0
    # 目录仍在（重建无障碍），只是空了
    assert cache.cache_dir().is_dir()


def test_clear_twice_is_noop(env):
    _seed(env, 2)
    assert cache.clear() == 2
    assert cache.clear() == 0


def test_clear_then_put_rebuilds(env):
    """清空后写回同样 OK（派生状态非资产——重建无障碍）。"""
    key = cache.cache_key(**BASE)
    cache.put(key=key, task="t", profile="p", temperature=0.6,
              template_sha_value="s", text="v1", created_at="2026-11-17T10:00:00")
    cache.clear()
    assert cache.read_text(key) is None
    cache.put(key=key, task="t", profile="p", temperature=0.6,
              template_sha_value="s", text="v2", created_at="2026-11-17T11:00:00")
    assert cache.read_text(key) == "v2"


def test_bad_files_are_skipped_by_iter_and_stats(env):
    """坏 JSON 文件既不计数也不算最旧（读侧宽容；clear 不动非条目文件）。"""
    d = cache.cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "deadbeefdeadbeef.json").write_text("garbage", encoding="utf-8")
    (d / "notes.txt").write_text("x", encoding="utf-8")
    assert cache.stats()["entries"] == 0
    assert cache.clear() == 0
    assert (d / "notes.txt").exists(), "clear 只删合法条目，不碰其他文件"
