"""插件契约层测试（插件系统设计 PL-1 校验要求 1-6/10/11）。

覆盖：契约类型表恰好 10 个锚 / 10×10 兼容矩阵全对比（相等兼容其余不
兼容）/ ext 命名空间规则 / manifest 红绿双路径（§4.2 六个必需键各缺
一条 × 各非法值类各一条）/ 流水线静态契约校验（正典 §9.1 首链绿 +
拖错顺序/kind 位置/重复 id 红）。core/plugins 两模块纯函数，无 I/O。
"""
from __future__ import annotations

import pytest

from musespiral.core.plugins import types
from musespiral.core.plugins.manifest import (
    PERMISSIONS,
    expected_dirname,
    is_known_port_type,
    parse_manifest,
    validate_pipeline_contract,
)
from musespiral.core.plugins.types import CONTRACT_TYPES, types_compatible

# ── 正典 §4.2 官方示例 manifest（绿路径基准）──────────────────────


def _canonical() -> dict:
    return {
        "id": "transcribe",
        "version": "1.2.0",
        "kind": "process",
        "name": {"zh": "语音转写", "en": "Transcribe"},
        "description": {"zh": "把音轨转成带时间戳的文字"},
        "inputs": {
            "media_path": {"type": "file/path", "required": True},
            "language": {"type": "string", "required": False, "default": "auto"},
            "hotwords": {"type": "string[]", "required": False},
        },
        "outputs": {
            "transcript": {"type": "segments"},
            "duration_s": {"type": "number"},
        },
        "runtime": {"type": "python", "entry": "plugin.py:run", "timeout_s": 7200},
        "requires": ["ffmpeg"],
        "permissions": ["fs:read", "fs:write:tmp"],
        "config_schema": {
            "model": {
                "type": "string",
                "enum": ["large-v3-turbo", "paraformer-zh"],
                "default": "large-v3-turbo",
            }
        },
    }


# ── 契约类型表与兼容矩阵（校验要求 10）────────────────────────────


def test_contract_types_exactly_ten() -> None:
    """锚：v1 恰好 10 个（正典 §3.1「不多不少」——多一个少一个都是契约漂移）。"""
    assert CONTRACT_TYPES == (
        "file/path", "url/http", "media/av", "segments", "ranges",
        "images", "clips", "text/markdown", "article", "material",
    )


def test_permission_table_exact() -> None:
    """权限表六值锚（正典 §7.1）。"""
    assert PERMISSIONS == (
        "fs:read", "fs:write:tmp", "fs:write:output",
        "net:none", "net:http", "exec:subprocess",
    )


def test_compat_matrix_10x10() -> None:
    """10×10 全覆盖：相等兼容，其余一律不兼容（校验要求 10）。"""
    for out_t in CONTRACT_TYPES:
        for in_t in CONTRACT_TYPES:
            assert types_compatible(out_t, in_t) is (out_t == in_t), (out_t, in_t)


def test_ext_type_rules() -> None:
    """ext/<name> 只与自身兼容；裸 ext/ 前缀不是合法类型名；非 str 宽容 False。"""
    assert types.is_ext_type("ext/vector") is True
    assert types.is_ext_type("ext/") is False
    assert types.is_ext_type("segments") is False
    assert types.is_ext_type("") is False
    assert types.is_ext_type(None) is False  # type: ignore[arg-type]
    assert types.is_ext_type(3) is False  # type: ignore[arg-type]
    # ext 与自身兼容（相等规则推论）
    assert types_compatible("ext/vector", "ext/vector") is True
    # ext 不被任何内置类型吸收（正典红线：插件自带 schema，平台不认识）
    for builtin in CONTRACT_TYPES:
        assert types_compatible("ext/vector", builtin) is False
        assert types_compatible(builtin, "ext/vector") is False
    assert types_compatible("ext/vector", "ext/other") is False
    assert types_compatible(None, "segments") is False  # type: ignore[arg-type]


def test_is_known_port_type() -> None:
    """端口类型表 = 契约 10 型 ∪ ext/ ∪ 标量基元（§4.2 示例自用 string/number/string[]）。"""
    for t in CONTRACT_TYPES:
        assert is_known_port_type(t)
    for t in ("string", "number", "boolean", "string[]", "number[]", "boolean[]"):
        assert is_known_port_type(t)
    assert is_known_port_type("ext/vector")
    assert not is_known_port_type("vector")
    assert not is_known_port_type("ext/")
    assert not is_known_port_type("")


def test_expected_dirname_flattens_slash() -> None:
    """目录名 = id 斜杠转下划线（校验要求 1）。"""
    assert expected_dirname("transcribe") == "transcribe"
    assert expected_dirname("acme/transcribe") == "acme_transcribe"


# ── manifest 绿路径 ────────────────────────────────────────────────


def test_canonical_manifest_parses() -> None:
    """正典 §4.2 官方示例全绿：字段逐一落位。"""
    plugin, errors = parse_manifest(_canonical())
    assert errors == []
    assert plugin is not None
    assert plugin.id == "transcribe"
    assert plugin.kind == "process"
    assert plugin.inputs["media_path"].type == "file/path"
    assert plugin.inputs["language"].required is False
    assert plugin.inputs["language"].default == "auto"
    assert plugin.outputs["transcript"].type == "segments"
    assert plugin.runtime.type == "python"
    assert plugin.runtime.timeout_s == 7200
    assert plugin.permissions == ["fs:read", "fs:write:tmp"]
    assert plugin.uses_ai is False
    assert plugin.name is not None and plugin.name.zh == "语音转写"


def test_port_required_defaults_true() -> None:
    """required 缺省 True（契约从严：未声明视为必需）。"""
    data = _canonical()
    del data["inputs"]["media_path"]["required"]
    plugin, errors = parse_manifest(data)
    assert errors == []
    assert plugin is not None
    assert plugin.inputs["media_path"].required is True


def test_optional_keys_omitted_green() -> None:
    """可选项全省略仍绿（必需要素只剩六键）。"""
    plugin, errors = parse_manifest({
        "id": "local-file",
        "version": "1.0.0",
        "kind": "source",
        "inputs": {"path": {"type": "string"}},
        "outputs": {"media": {"type": "file/path"}},
        "runtime": {"type": "exec", "entry": "run.sh"},
    })
    assert errors == []
    assert plugin is not None
    assert plugin.runtime.type == "exec"
    assert plugin.name is None and plugin.requires == [] and plugin.permissions == []


def test_vendor_id_and_semver_extras_green() -> None:
    """vendor/name 形 id 与预发布版本号合法。"""
    data = _canonical() | {"id": "acme/transcribe", "version": "1.0.0-rc.1+build.7"}
    plugin, errors = parse_manifest(data)
    assert errors == []
    assert plugin is not None and plugin.id == "acme/transcribe"


def test_ext_port_type_green() -> None:
    """ext/<name> 端口类型合法（扩展命名空间）。"""
    data = _canonical()
    data["outputs"]["embedding"] = {"type": "ext/vector"}
    plugin, errors = parse_manifest(data)
    assert errors == []
    assert plugin is not None and plugin.outputs["embedding"].type == "ext/vector"


# ── manifest 红路径：必需键缺失（校验要求 11，每键一条）──────────


@pytest.mark.parametrize("required_key", ["id", "version", "kind", "inputs", "outputs", "runtime"])
def test_missing_required_key_red(required_key: str) -> None:
    data = _canonical()
    del data[required_key]
    plugin, errors = parse_manifest(data)
    assert plugin is None
    assert errors and any(required_key in e for e in errors), (required_key, errors)


# ── manifest 红路径：非法值（校验要求 2-5，每类一条）──────────────


def _mutated(key: str, value: object) -> dict:
    data = _canonical()
    data[key] = value
    return data


def test_id_uppercase_red() -> None:
    plugin, errors = parse_manifest(_mutated("id", "Transcribe"))
    assert plugin is None and any("id 格式非法" in e for e in errors)


def test_id_double_slash_red() -> None:
    plugin, errors = parse_manifest(_mutated("id", "a/b/c"))
    assert plugin is None and any("id 格式非法" in e for e in errors)


def test_id_empty_red() -> None:
    plugin, errors = parse_manifest(_mutated("id", ""))
    assert plugin is None and any("id 格式非法" in e for e in errors)


def test_version_not_semver_red() -> None:
    plugin, errors = parse_manifest(_mutated("version", "1.2"))
    assert plugin is None and any("语义化版本" in e for e in errors)


def test_kind_unknown_red() -> None:
    plugin, errors = parse_manifest(_mutated("kind", "transform"))
    assert plugin is None and any("kind" in e for e in errors)


def test_port_type_unknown_red() -> None:
    data = _canonical()
    data["inputs"]["media_path"]["type"] = "vector"
    plugin, errors = parse_manifest(data)
    assert plugin is None
    assert any("inputs.media_path.type 不在已知端口类型表内" in e for e in errors)


def test_output_port_type_unknown_red() -> None:
    data = _canonical()
    data["outputs"]["transcript"]["type"] = "text"
    plugin, errors = parse_manifest(data)
    assert plugin is None
    assert any("outputs.transcript.type" in e for e in errors)


def test_runtime_http_rejected_in_v1() -> None:
    """http runtime 留接口不实现（正典 §4.3 裁决）——声明即红。"""
    data = _canonical()
    data["runtime"] = {"type": "http", "entry": "http://127.0.0.1:9000/run"}
    plugin, errors = parse_manifest(data)
    assert plugin is None and any("runtime.type" in e for e in errors)


def test_python_entry_without_func_red() -> None:
    plugin, errors = parse_manifest(_mutated("runtime", {"type": "python", "entry": "plugin.py"}))
    assert plugin is None and any("runtime.entry" in e for e in errors)


def test_python_entry_without_py_red() -> None:
    plugin, errors = parse_manifest(_mutated("runtime", {"type": "python", "entry": "plugin:run"}))
    assert plugin is None and any("runtime.entry" in e for e in errors)


def test_unknown_permission_red() -> None:
    plugin, errors = parse_manifest(_mutated("permissions", ["fs:delete"]))
    assert plugin is None and any("permissions 不在已知权限表内" in e for e in errors)


def test_inputs_must_be_object_red() -> None:
    """inputs/outputs 是具名对象不是数组（正典 §4.2 关键设计点）。"""
    plugin, errors = parse_manifest(_mutated("inputs", [{"type": "file/path"}]))
    assert plugin is None and any("inputs" in e for e in errors)


def test_port_missing_type_red() -> None:
    data = _canonical()
    del data["inputs"]["media_path"]["type"]
    plugin, errors = parse_manifest(data)
    assert plugin is None and any("inputs.media_path.type" in e for e in errors)


def test_name_must_be_i18n_object_red() -> None:
    """name 是 i18n 对象不是字符串（正典 §4.2 关键设计点）。"""
    plugin, errors = parse_manifest(_mutated("name", "语音转写"))
    assert plugin is None and any("name" in e for e in errors)


def test_name_without_zh_red() -> None:
    plugin, errors = parse_manifest(_mutated("name", {"en": "Transcribe"}))
    assert plugin is None and any("zh" in e for e in errors)


def test_unknown_extra_key_red() -> None:
    """拼错键静默忽略是契约隐性坑——extra=forbid 宁可安装时红（v1 键集钉死）。"""
    data = _canonical()
    data["permissins"] = ["fs:read"]
    plugin, errors = parse_manifest(data)
    assert plugin is None and any("permissins" in e for e in errors)


def test_non_dict_manifest_red() -> None:
    plugin, errors = parse_manifest(["not", "a", "dict"])
    assert plugin is None and errors


# ── 流水线静态契约校验（§3.2 + §9.1 首链）─────────────────────────


def _pl(pid: str, kind: str, inputs: dict, outputs: dict) -> dict:
    return {
        "id": pid, "version": "1.0.0", "kind": kind,
        "inputs": inputs, "outputs": outputs,
        "runtime": {"type": "exec", "entry": "run.sh"},
    }


def _canonical_chain() -> list[dict]:
    """正典 §9.1 六插件链（frame-grab 的 file/path 来自两步之前的
    local-file——传递供给判定的基准链）。"""
    return [
        {"id": "n0", "plugin": _pl(
            "local-file", "source",
            {"path": {"type": "string", "required": True}},
            {"media": {"type": "file/path"}})},
        {"id": "n1", "plugin": _pl(
            "transcribe", "process",
            {"media_path": {"type": "file/path", "required": True}},
            {"transcript": {"type": "segments"}, "duration_s": {"type": "number"}})},
        {"id": "n2", "plugin": _pl(
            "key-segment", "process",
            {"transcript": {"type": "segments", "required": True}},
            {"ranges": {"type": "ranges"}})},
        {"id": "n3", "plugin": _pl(
            "frame-grab", "process",
            {"ranges": {"type": "ranges", "required": True},
             "media_path": {"type": "file/path", "required": True}},
            {"images": {"type": "images"}})},
        {"id": "n4", "plugin": _pl(
            "article-writer", "process",
            {"segments": {"type": "segments", "required": True},
             "images": {"type": "images", "required": True}},
            {"article": {"type": "article"}})},
        {"id": "n5", "plugin": _pl(
            "material-sink", "sink",
            {"article": {"type": "article", "required": True}},
            {"material": {"type": "material"}})},
    ]


def test_canonical_chain_green() -> None:
    assert validate_pipeline_contract(_canonical_chain()) == []


def test_single_source_node_green() -> None:
    """单节点流水线：source 即开头、非 sink 即结尾，kind 双规则平凡满足。"""
    nodes = [{"id": "only", "plugin": _pl(
        "local-file", "source",
        {"path": {"type": "string", "required": True}},
        {"media": {"type": "file/path"}})}]
    assert validate_pipeline_contract(nodes) == []


def test_frame_grab_before_key_segment_red() -> None:
    """正典 §3.2 场景：frame-grab 拖到 key-segment 前——保存前标红。"""
    chain = _canonical_chain()
    chain[2], chain[3] = chain[3], chain[2]
    errors = validate_pipeline_contract(chain)
    assert errors and any("ranges" in e and "frame-grab" in e for e in errors), errors


def test_type_mismatch_red() -> None:
    """相邻类型接不上（segments ≠ images）→ 红。"""
    nodes = [
        {"id": "a", "plugin": _pl("p1", "source", {}, {"out": {"type": "segments"}})},
        {"id": "b", "plugin": _pl("p2", "sink", {"inp": {"type": "images", "required": True}}, {"m": {"type": "material"}})},
    ]
    errors = validate_pipeline_contract(nodes)
    assert errors and any("images" in e for e in errors)


def test_ext_type_chain_compat() -> None:
    """ext/<name> 在流水线里同样只与自身兼容。"""
    producer = _pl("src", "source", {}, {"data": {"type": "ext/vector"}})
    same = _pl("sink1", "sink", {"data": {"type": "ext/vector", "required": True}}, {})
    other = _pl("sink2", "sink", {"data": {"type": "ext/other", "required": True}}, {})
    assert validate_pipeline_contract([{"id": "a", "plugin": producer}, {"id": "b", "plugin": same}]) == []
    errors = validate_pipeline_contract([{"id": "a", "plugin": producer}, {"id": "b", "plugin": other}])
    assert errors and any("ext/other" in e for e in errors)


def test_source_mid_chain_red() -> None:
    """source 只能开头（正典 §4.2 kind 三态规则）。"""
    chain = _canonical_chain()
    chain.insert(2, dict(chain[0], id="mid-source"))
    errors = validate_pipeline_contract(chain)
    assert errors and any("source" in e for e in errors)


def test_sink_mid_chain_red() -> None:
    """sink 只能结尾。"""
    chain = _canonical_chain()
    chain.insert(3, dict(chain[5], id="mid-sink"))
    errors = validate_pipeline_contract(chain)
    assert errors and any("sink" in e for e in errors)


def test_duplicate_node_ids_red() -> None:
    nodes = _canonical_chain()[:2]
    nodes.append(dict(nodes[1]))  # 同 id
    errors = validate_pipeline_contract(nodes)
    assert errors and any("重复" in e for e in errors)


def test_empty_pipeline_red() -> None:
    assert validate_pipeline_contract([]) != []


def test_optional_unfed_input_green() -> None:
    """可选输入（required=False）不供给不红（canonical 里 language/hotwords 同语义）。"""
    nodes = [
        {"id": "a", "plugin": _pl("p1", "source", {}, {"out": {"type": "segments"}})},
        {"id": "b", "plugin": _pl("p2", "process",
                                  {"extra": {"type": "images", "required": False}},
                                  {"done": {"type": "material"}})},
        {"id": "c", "plugin": _pl("p3", "sink", {"done": {"type": "material", "required": True}}, {})},
    ]
    assert validate_pipeline_contract(nodes) == []


def test_bad_plugin_dict_red_isolated() -> None:
    """节点携带坏 manifest dict → 报错但给出完整红清单，不抛异常。"""
    nodes = [{"id": "a", "plugin": {"id": "x", "kind": "nope"}}]
    errors = validate_pipeline_contract(nodes)
    assert errors and any("manifest 非法" in e for e in errors)


def test_node_not_dict_red() -> None:
    errors = validate_pipeline_contract(["not-a-node"])
    assert errors


def test_node_missing_id_red() -> None:
    nodes = [{"plugin": _pl("p1", "source", {}, {})}]
    errors = validate_pipeline_contract(nodes)
    assert errors and any("id" in e for e in errors)
