#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 多维度词条随机抽卡工具
---------------------------------------------------------------------
 功能：
   * 自定义任意数量的创作维度，每维度独立词条库，独立随机抽取
   * 自定义任意正整数连抽（1 抽、5 抽、10 抽、百抽……均可）
   * 维度优先级（默认 21/22/23…递增）+ 词条权重（同维度内只比权重，不比优先级）
   * 词条冲突检测 + 自动重抽（保留高优先级词条，精准替换冲突项）
   * 每组自动拼接为可直接使用的创作文案，逐段写入 抽卡.txt（段间换行隔开）
---------------------------------------------------------------------
 使用：
   直接运行本脚本，按菜单输入数字即可。
   词条配置在 gacha_config.json（与本脚本同目录），改完保存即生效。
---------------------------------------------------------------------
 运行环境：原生 Python 3.6+，无任何第三方依赖。
=====================================================================
"""

import json
import os
import random
import sys
import time

# ---- 控制台编码兼容（Windows 下避免中文/emoji 乱码）----
for _stream in (sys.stdout, sys.stderr):
    try:
        enc = (_stream.encoding or "").lower().replace("-", "").replace("_", "")
        if enc not in ("utf8", "utf"):
            _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "gacha_config1.json")
OUTPUT_TXT = "抽卡1.txt"   # 连抽结果输出文件名
MAX_DRAW = 1000  # 单次连抽上限，防止超限数值卡死

# ── 维度优先级（外部）──
# 优先级是「外部」概念，决定哪个维度先被抽取（数值越大越先抽）
# 默认第 1 维度=21、第 2 维度=22、第 3 维度=23……（依此类推递增）
DIM_PRIORITY_START = 21

# ── 词条权重（内部）──
# 权重是「内部」概念，决定该词条在所属维度内被抽中的概率
# 权重独立于维度优先级，默认 20，可自定义 0-100（数值越大越容易抽中）
DEFAULT_WEIGHT = 20

# 兼容旧的「高 / 低」写法
PRIORITY_MAP = {
    "高": 70, "high": 70, "h": 70,
    "低": 5, "low": 5, "l": 5,
}


def resolve_dim_priority(p, idx):
    """维度优先级解析：默认第 idx 维度 = 21 + idx；可手动指定数字覆盖。"""
    if isinstance(p, (int, float)):
        return max(0.0, min(100.0, float(p)))
    if p is not None:
        try:
            return max(0.0, min(100.0, float(str(p).strip())))
        except ValueError:
            pass
    return float(DIM_PRIORITY_START + idx)


def resolve_weight(w):
    """词条权重解析（内部概念）：默认 20，可自定义 0-100 数值。"""
    if w is None:
        return float(DEFAULT_WEIGHT)
    if isinstance(w, (int, float)):
        return max(0.0, min(100.0, float(w)))
    s = str(w).strip().lower()
    if s in PRIORITY_MAP:
        return float(PRIORITY_MAP[s])
    if s in ("中", "mid", "medium", "m"):
        return float(DEFAULT_WEIGHT)
    try:
        return max(0.0, min(100.0, float(s)))
    except ValueError:
        return float(DEFAULT_WEIGHT)


def priority_weight(entry):
    """返回词条权重（加载时已解析为 0-100 数值）。"""
    w = entry.get("weight")
    if isinstance(w, (int, float)):
        return max(0.0, min(100.0, float(w)))
    return float(DEFAULT_WEIGHT)


def priority_tag(entry):
    """词条权重的展示标签（0-100 数值）。"""
    return f"★{priority_weight(entry):.0f}"


def load_and_normalize():
    """读取并规范化配置：词条支持字符串或 {"text":..., "priority":...} 两种写法。"""
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ 找不到配置文件：{CONFIG_PATH} 喵～")
        print("   请确保 gacha_config.json 与 gacha.py 在同一目录。")
        sys.exit(1)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ 配置文件 JSON 格式错误，请检查逗号/引号是否缺失：{e} 喵～")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 读取配置文件失败：{e} 喵～")
        sys.exit(1)

    dims = []
    for idx, d in enumerate(data.get("dimensions", [])):
        name = str(d.get("name", "")).strip()
        # 维度优先级（外部）：默认 21、22、23……
        dim_prio = resolve_dim_priority(d.get("priority"), idx)
        entries = []
        for e in d.get("entries", []):
            if isinstance(e, str):
                text = e.strip()
                if text:
                    entries.append({"text": text, "weight": float(DEFAULT_WEIGHT)})
            elif isinstance(e, dict):
                text = str(e.get("text", "")).strip()
                if text:
                    w = resolve_weight(e.get("weight", e.get("priority")))
                    entries.append({"text": text, "weight": w})
        if name and entries:
            dims.append({"name": name, "priority": dim_prio, "entries": entries})
        elif name:
            print(f"⚠️ 维度「{name}」词条为空，已自动跳过。")

    if not dims:
        print("❌ 配置中没有任何可用维度/词条，请先在 gacha_config.json 中填写词条喵～")
        sys.exit(1)

    conflicts = []
    for rule in data.get("conflicts", []):
        words = [str(w).strip() for w in rule if str(w).strip()]
        if len(words) >= 2:
            conflicts.append(words)

    max_redraw = data.get("max_redraw", 10)
    try:
        max_redraw = int(max_redraw)
    except Exception:
        max_redraw = 10
    max_redraw = max(0, min(max_redraw, 100))

    # 校验冲突规则：冲突是「跨维度」的，同维度词条只会抽一个、不会同时出现
    word_dim = {}
    for d in dims:
        for e in d["entries"]:
            word_dim[e["text"]] = d["name"]
    for rule in conflicts:
        involved = {word_dim.get(w) for w in rule}
        involved.discard(None)
        if len(involved) <= 1:
            print(f"  ℹ️ 冲突规则 {rule} 全落在同一维度（同维度只抽一个词条、不会同时出现），该规则不会生效，可删除。")

    global_prefix = str(data.get("global_prefix", "")).strip()

    return {"dimensions": dims, "conflicts": conflicts, "max_redraw": max_redraw, "global_prefix": global_prefix}


def weighted_choice(entries):
    """按优先级权重随机抽取一个词条。"""
    weights = [priority_weight(e) for e in entries]
    total = sum(weights)
    if total <= 0:
        return random.choice(entries)
    r = random.uniform(0, total)
    acc = 0.0
    for entry, w in zip(entries, weights):
        acc += w
        if r <= acc:
            return entry
    return entries[-1]


def draw_one_group(dims, conflicts, max_redraw):
    """按维度优先级从高到低依次抽取：先抽的维度优先，后抽的维度避让冲突。"""
    ordered = sorted(dims, key=lambda d: d["priority"], reverse=True)
    result = {}
    redraw_count = 0

    def conflicts_with(dn, entry):
        """把 (dn, entry) 加入已抽结果后是否触发冲突。"""
        for rule in conflicts:
            present = (1 if entry["text"] in rule else 0)
            present += sum(1 for k, e in result.items() if k != dn and e["text"] in rule)
            if present >= 2:
                return True
        return False

    for d in ordered:
        dn = d["name"]
        entry = weighted_choice(d["entries"])
        redraw = 0
        while redraw < max_redraw and conflicts_with(dn, entry):
            entry = weighted_choice(d["entries"])
            redraw += 1
        result[dn] = entry
        redraw_count += redraw

    # 最终残留冲突检测
    still_conflict = False
    for rule in conflicts:
        present = sum(1 for e in result.values() if e["text"] in rule)
        if present >= 2:
            still_conflict = True
            break

    # 结果按配置顺序重排
    final = {d["name"]: result[d["name"]] for d in dims}
    return final, still_conflict, redraw_count


def compose_text(result, dims, global_prefix=""):
    """将各维度词条拼接为完整创作文案；有全局前缀时置于最前。"""
    parts = [result[d["name"]]["text"] for d in dims]
    if global_prefix:
        parts.insert(0, global_prefix)
    return "，".join(parts)


def print_group(idx, result, dims, conflicted, total, global_prefix=""):
    """单抽时的详细展示（罗列各维度词条 + 拼接文案）。"""
    print(f"  ────── 第 {idx}/{total} 抽 ──────")
    if global_prefix:
        print(f"    【全局】{global_prefix}")
    for d in dims:
        entry = result[d["name"]]
        print(f"    【{d['name']}】{entry['text']}  {priority_tag(entry)}")
    print("    ── 拼接文案 ──")
    print(f"    {compose_text(result, dims, global_prefix)}")
    if conflicted:
        print("    ⚠️ 该组仍存在轻微冲突（已达最大重抽次数）")
    print()


def loading(text="抽取中", seconds=0.6):
    """简易加载延时动画。"""
    frames = ["|", "/", "-", "\\"]
    end = time.time() + seconds
    i = 0
    while time.time() < end:
        print(f"\r  {text} {frames[i % 4]}", end="", flush=True)
        time.sleep(0.08)
        i += 1
    print("\r" + " " * 40 + "\r", end="", flush=True)


def read_positive_int(prompt, max_val=MAX_DRAW):
    """读取正整数，拦截空输入/乱码/非数字/负数/超限等异常。"""
    while True:
        raw = input(prompt).strip()
        if raw == "":
            print("  ⚠️ 输入不能为空，请输入正整数喵～")
            continue
        if not raw.isdigit():
            print("  ⚠️ 输入无效：请只输入纯数字（正整数）喵～")
            continue
        try:
            n = int(raw)
        except ValueError:
            print("  ⚠️ 输入无效：请只输入纯数字（正整数）喵～")
            continue
        if n <= 0:
            print("  ⚠️ 抽数必须大于 0 喵～")
            continue
        if n > max_val:
            print(f"  ⚠️ 单次最多支持 {max_val} 抽，请重新输入喵～")
            continue
        return n


def show_config(cfg):
    dims = cfg["dimensions"]
    print()
    print("  ── 当前词条配置 ──")
    if cfg.get("global_prefix"):
        print(f"    【全局前缀】{cfg['global_prefix']}")
    for d in dims:
        names = [e["text"] for e in d["entries"]]
        print(f"    【{d['name']}】维度优先级 {d['priority']:.0f} · 共 {len(names)} 条")
        preview = " / ".join(names[:6])
        print(f"      {preview}{' …' if len(names) > 6 else ''}")
    print(f"  ── 冲突规则 ── 共 {len(cfg['conflicts'])} 组（跨维度词条互斥，组内任意两个词互斥）")
    for rule in cfg["conflicts"][:5]:
        print(f"      {' / '.join(rule)}")
    if len(cfg["conflicts"]) > 5:
        print(f"      … 共 {len(cfg['conflicts'])} 组")
    print(f"  ── 最大重抽次数 ── {cfg['max_redraw']}")
    print()
    print("  💡 修改词条：直接编辑 gacha_config.json 保存即生效，无需改代码喵～")
    print("     维度优先级（外部）：决定哪个维度先被抽取，默认第1维=21、第2维=22、第3维=23（递增）")
    print("     词条权重（内部）：决定维度内词条被抽中的概率，默认 20，自定义填 0-100（越大越优先）")
    print("     冲突规则：跨维度词条互斥，把互斥词写进同一组 [\"A\", \"B\"]（组内任意两个词即互斥）")
    print()


def draw_flow(cfg):
    dims = cfg["dimensions"]
    conflicts = cfg["conflicts"]
    max_redraw = cfg["max_redraw"]
    global_prefix = cfg.get("global_prefix", "")

    print()
    print("  ── 快捷抽取参考 ──")
    print("  1 = 单抽  5 = 五连抽  10 = 十连抽（也可输入任意正整数）")
    n = read_positive_int("  请输入抽数：")

    loading("抽取中", 0.8)

    results = []        # 每组结果 (result, still)
    texts = []          # 每组拼接好的完整文案（一段）
    conflict_groups = 0
    total_redraws = 0
    for _ in range(n):
        result, still, redraw = draw_one_group(dims, conflicts, max_redraw)
        if still:
            conflict_groups += 1
        total_redraws += redraw
        results.append((result, still))
        texts.append(compose_text(result, dims, global_prefix))

    # 单抽时额外详细展示这一组（罗列各维度词条）
    if n == 1:
        print()
        print_group(1, results[0][0], dims, results[0][1], 1, global_prefix)

    # 写入 抽卡.txt：每组一段，段间换行隔开
    out_path = os.path.join(BASE_DIR, OUTPUT_TXT)
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            for t in texts:
                f.write(t + "\n")
    except OSError as e:
        print(f"  ❌ 写入文件失败：{e} 喵～")
        return

    # 控制台简报 + 预览
    print()
    print("  " + "=" * 40)
    print(f"  ✅ 本次共抽取 {n} 组，每组拼接为一段文案，已写入 {OUTPUT_TXT}")
    print(f"     （共 {n} 段，段间换行隔开）")
    print("  ── 预览 ──")
    preview_n = min(n, 5)
    for idx, t in enumerate(texts[:preview_n], 1):
        print(f"    {idx}. {t}")
    if n > preview_n:
        print(f"    …（其余 {n - preview_n} 段见文件）")
    if conflict_groups:
        print(f"  ℹ️ 其中 {conflict_groups} 组触发轻微冲突（已保留高优先级词条并精准重抽）。")
    print(f"  📁 文件路径：{out_path}")
    print("  " + "=" * 40)


def main():
    print()
    print("  ╔════════════════════════════════════╗")
    print("  ║      多维度词条随机抽卡系统        ║")
    print("  ║   影视 · 摄影 · AI创作 灵感生成    ║")
    print("  ╚════════════════════════════════════╝")

    cfg = load_and_normalize()

    while True:
        print()
        print("  ── 主菜单 ──")
        print("  [1] 自定义连抽（任意正整数抽数）")
        print("  [2] 查看当前词条配置 / 使用说明")
        print("  [0] 退出程序")
        choice = input("  请输入序号：").strip()

        if choice == "1":
            draw_flow(cfg)
        elif choice == "2":
            show_config(cfg)
        elif choice == "0":
            print("  感谢使用，再见喵～ ヽ(●´∀`●)ﾉ")
            break
        else:
            print("  ⚠️ 无效输入，请输入 0 / 1 / 2 喵～")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  已退出，再见喵～ 🐱")
