#!/usr/bin/env python3
"""把 quant-scanner stats 生成的 HTML 报告解析为 markdown 摘要。

为什么需要：微信端 cf 看不到 HTML 渲染，需要把报告核心内容（结论 + 顶层表 + holdout 表）
提取为纯文本/markdown，让 AI 直接回微信。

用法：
    python3 parse-html.py /path/to/stats.html
    python3 parse-html.py /path/to/stats.html --format json   # 给程序消费
    python3 parse-html.py /path/to/stats.html --format md     # 默认，给人读

依赖：beautifulsoup4（quant-scanner venv 已装）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _strip_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_conclusions(soup) -> list[dict]:
    """提取「🎯 核心结论」section。"""
    out = []
    for c in soup.select(".conclusion"):
        title_el = c.select_one(".conclusion-title")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        # body 是 title 之外的文本
        body_html = c.decode_contents()
        # 移除 title 部分
        body_html = re.sub(r'<div class="conclusion-title">.*?</div>', "", body_html, flags=re.DOTALL)
        # 把 <strong>X</strong> 转 **X**，<br> 转换行，<div> 标签清空
        body_html = re.sub(r"<strong>(.*?)</strong>", r"**\1**", body_html)
        body_html = re.sub(r"<br\s*/?>", "\n", body_html)
        body_html = re.sub(r"</?div[^>]*>", "", body_html)
        body = _strip_ws(body_html).replace(" • ", "\n• ")
        severity = next(
            (cls.replace("conclusion-", "") for cls in c.get("class", []) if cls.startswith("conclusion-") and cls != "conclusion"),
            "neutral",
        )
        out.append({"severity": severity, "title": title, "body": body})
    return out


def parse_table(table) -> list[dict]:
    """通用表格解析：第一行是表头。"""
    rows = table.find_all("tr")
    if not rows:
        return []
    headers = [_strip_ws(th.get_text()) for th in rows[0].find_all(["th", "td"])]
    data = []
    for row in rows[1:]:
        cells = [_strip_ws(td.get_text()) for td in row.find_all(["td"])]
        if not cells:
            continue
        # 用 header 长度截断/补齐
        while len(cells) < len(headers):
            cells.append("")
        data.append(dict(zip(headers, cells)))
    return data


def html_to_markdown(html_path: Path) -> str:
    """HTML → markdown 摘要。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return f"[ERROR] beautifulsoup4 未安装，无法解析 {html_path}"

    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")

    lines: list[str] = []
    lines.append("# 形态胜率统计报告（自动解析）\n")

    # 1. 副标题
    subtitle = soup.select_one(".subtitle")
    if subtitle:
        lines.append(f"> {_strip_ws(subtitle.get_text())}\n")

    # 2. 披露
    disclosure = soup.select_one(".disclosure")
    if disclosure:
        lines.append("## ⚠ 披露\n")
        for li in disclosure.find_all("li"):
            lines.append(f"- {_strip_ws(li.get_text())}")
        lines.append("")

    # 3. 核心结论
    conclusions = parse_conclusions(soup)
    if conclusions:
        lines.append("## 🎯 核心结论（自动生成）\n")
        for c in conclusions:
            icon = {"positive": "✅", "negative": "❌", "warning": "⚠️", "neutral": "📌"}.get(c["severity"], "📌")
            lines.append(f"### {icon} {c['title']}\n")
            lines.append(c["body"])
            lines.append("")

    # 4. 顶层表
    tables = soup.find_all("table")
    if tables:
        lines.append("## 📊 顶层信号总表\n")
        top_data = parse_table(tables[0])
        if top_data:
            headers = list(top_data[0].keys())
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("|" + "|".join(["---"] * len(headers)) + "|")
            for row in top_data:
                lines.append("| " + " | ".join(row.get(h, "") for h in headers) + " |")
        lines.append("")

    # 5. 二级表
    if len(tables) >= 2:
        lines.append("## 🔍 二级细分表（前 15 行）\n")
        second_data = parse_table(tables[1])[:15]
        if second_data:
            headers = list(second_data[0].keys())
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("|" + "|".join(["---"] * len(headers)) + "|")
            for row in second_data:
                lines.append("| " + " | ".join(row.get(h, "") for h in headers) + " |")
        lines.append("")

    # 6. holdout 表（如果有，会在最后几张表）
    h2_sections = soup.find_all("h2")
    for h2 in h2_sections:
        if "holdout" in h2.get_text() and "样本不足" not in h2.get_text():
            lines.append(f"## 🛡 {h2.get_text(strip=True)}\n")
            # 找该 h2 之后的所有 table
            for sib in h2.find_all_next():
                if sib.name == "h2":
                    break
                if sib.name == "table":
                    data = parse_table(sib)
                    if data:
                        headers = list(data[0].keys())
                        lines.append("| " + " | ".join(headers) + " |")
                        lines.append("|" + "|".join(["---"] * len(headers)) + "|")
                        for row in data:
                            lines.append("| " + " | ".join(row.get(h, "") for h in headers) + " |")
                        lines.append("")

    lines.append("\n---\n报告来源：" + str(html_path))
    return "\n".join(lines)


def html_to_json(html_path: Path) -> str:
    """HTML → JSON（给程序消费）。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    tables = soup.find_all("table")
    out = {
        "conclusions": parse_conclusions(soup),
        "top_level": parse_table(tables[0]) if tables else [],
        "second_level": parse_table(tables[1]) if len(tables) >= 2 else [],
        "diagnostic": parse_table(tables[2]) if len(tables) >= 3 else [],
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("html_path", type=Path)
    ap.add_argument("--format", choices=["md", "json"], default="md")
    args = ap.parse_args()

    if not args.html_path.exists():
        print(f"[ERROR] file not found: {args.html_path}", file=sys.stderr)
        return 1

    if args.format == "md":
        print(html_to_markdown(args.html_path))
    else:
        print(html_to_json(args.html_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
