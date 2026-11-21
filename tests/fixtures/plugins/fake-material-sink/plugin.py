"""假插件·素材落库（夹具 sink）：成稿 → 素材 id 摘要（不触素材库）。

真实 sink 走既有 materials API 以 preview 态落库（正典 §7.3 红线：人工
采纳才正式入库）；夹具只产一个可断言的确定性 id，避免测试污染真实库。
"""


def run(inputs: dict, config: dict, ctx: object) -> dict:
    article = str(inputs["article"])
    out = ctx.artifacts_dir / "final.md"
    out.write_text(article, encoding="utf-8")
    material = {
        "id": f"fake-mat-{abs(hash(article)) % 100000:05d}",
        "chars": len(article),
        "preview": True,
        "path": str(out),
    }
    ctx.log(f"material-sink: 落库 chars={material['chars']} preview=True")
    ctx.progress(1, 1)
    return {"material": material}
