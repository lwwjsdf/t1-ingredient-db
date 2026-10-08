#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import pdfplumber


PDF_URL = "https://cfaaweb.oss-cn-beijing.aliyuncs.com/%E7%BD%91%E7%AB%99/1.GB2760-2024%20%E9%A3%9F%E5%93%81%E5%AE%89%E5%85%A8%E5%9B%BD%E5%AE%B6%E6%A0%87%E5%87%86%20%E9%A3%9F%E5%93%81%E6%B7%BB%E5%8A%A0%E5%89%82%E4%BD%BF%E7%94%A8%E6%A0%87%E5%87%86.pdf"
ROOT = Path(__file__).resolve().parent


def compact(value):
    return re.sub(r"\s+", "", value or "")


def normalize(value):
    value = compact(value)
    value = value.replace("（", "(").replace("）", ")")
    return value.strip("、,，;；")


def download_pdf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(PDF_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request) as response, path.open("wb") as output:
        shutil.copyfileobj(response, output)


def source_manifest(pdf_path):
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    return {
        "standard": "GB 2760-2024",
        "source_url": PDF_URL,
        "pdf_sha256": digest,
        "publisher": "国家卫生健康委员会食品安全标准与监测评估司公开页面附带全文",
    }


def char_title(page, start, end):
    characters = [
        char
        for char in page.chars
        if start <= char["top"] < end and char["x0"] >= 58 and char["x1"] <= 258 and char["text"].strip()
    ]
    characters.sort(key=lambda char: char["top"])
    rows = []
    last_top = -999
    for char in characters:
        if abs(char["top"] - last_top) > 3:
            rows.append([])
        rows[-1].append(char)
        last_top = char["top"]
    return "".join(
        "".join(char["text"] for char in sorted(row, key=lambda char: char["x0"]))
        for row in rows
    )


def raw_page_lines(pdf_path, page_number):
    text = subprocess.check_output(
        ["pdftotext", "-raw", "-f", str(page_number), "-l", str(page_number), str(pdf_path), "-"],
        text=True,
    )
    return [compact(line) for line in text.splitlines() if compact(line)]


def raw_titles_for_page(lines):
    titles = []
    for index, line in enumerate(lines):
        if "CNS" not in line:
            continue
        cursor = index - 1
        collected = []
        while cursor >= 0 and len(collected) < 5:
            candidate = lines[cursor]
            if (
                not candidate
                or candidate.startswith(("食品分类号", "GB", "表A.1", "功能"))
                or re.match(r"^\d+\.\d", candidate)
                or "按生产需要适量使用" in candidate
                or "g/kg" in candidate
            ):
                break
            if re.search(r"[\u4e00-\u9fff]", candidate):
                collected.append(candidate)
            cursor -= 1
        titles.append("".join(reversed(collected)))
    return titles


def strip_english(title):
    depth = 0
    last_cjk = -1
    for index, char in enumerate(title):
        if char in "（(":
            depth += 1
        elif char in "）)":
            depth = max(0, depth - 1)
            if depth == 0 and index + 1 < len(title) and title[index + 1].isascii() and title[index + 1].isalpha():
                return title[:index + 1]
        if re.search(r"[\u4e00-\u9fff]", char):
            last_cjk = index
    return title


def clean_official_name(title):
    title = strip_english(title)
    title = re.sub(r"^.*。", "", title)
    leading_notes = (
        r"^添加该添加剂.*?。",
        r"^如用于[^,，]*,?按冲调倍数增加使用量",
        r"^以即饮状态计[^,，]*,?按稀释倍数增加使用量",
    )
    for pattern in leading_notes:
        title = re.sub(pattern, "", title)
    title = re.sub(r"\d+\)$", "", title)
    title = re.sub(r"号+$", "", title)
    return normalize(title)


def parse_a1(pdf_path, pdf):
    additives = []
    for page_index in range(7, 151):
        page = pdf.pages[page_index]
        page_number = page_index + 1
        lines = page.extract_text_lines()
        tables = sorted(page.find_tables(), key=lambda table: table.bbox[1])
        raw_lines = raw_page_lines(pdf_path, page_number)
        raw_titles = raw_titles_for_page(raw_lines)

        headers = []
        for line in lines:
            if "CNS" not in line["text"] or "INS" not in line["text"]:
                continue
            match = re.search(r"CNS\s*(.+?)\s*INS\s*(.+)", line["text"], re.I)
            if not match:
                raise ValueError(f"PDF 第 {page_number} 页 CNS/INS 解析失败：{line['text']}")
            previous_tables = [table.bbox[3] for table in tables if table.bbox[3] <= line["top"] - 2]
            previous_captions = [
                item["bottom"]
                for item in lines
                if item["top"] < line["top"] and ("表A1" in item["text"] or "表A.1" in item["text"])
            ]
            start = max(previous_tables + previous_captions, default=45) + 2
            title = char_title(page, start, line["top"] - 2)
            title = re.sub(r"^—|^GB\d+|表食品添加剂的允许使用品种", "", title)
            title = clean_official_name(title)
            function_line = next(
                (item for item in lines if item["top"] > line["top"] and item["text"].startswith("功能")),
                None,
            )
            function = compact(function_line["text"][2:]) if function_line else ""
            headers.append(
                {
                    "page": page_number,
                    "top": line["top"],
                    "official_name": normalize(title),
                    "cns_number": normalize(match.group(1)),
                    "ins_number": normalize(match.group(2)),
                    "function_category": function,
                    "source_type": "GB2760_AppendixA",
                    "source_ref": f"GB 2760—2024 表A.1，PDF第{page_number}页",
                    "permissions": [],
                }
            )
        if len(headers) == len(raw_titles):
            for header, raw_title in zip(headers, raw_titles):
                cleaned = clean_official_name(raw_title)
                if cleaned and ("包括" in cleaned or "又名" in cleaned):
                    header["official_name"] = cleaned

        current = None
        header_cursor = 0
        for table in tables:
            extracted = table.extract()
            if not extracted or len(extracted[0]) != 4:
                continue
            while header_cursor < len(headers) and headers[header_cursor]["top"] <= table.bbox[1]:
                current = headers[header_cursor]
                header_cursor += 1
            if current is None:
                continue
            for row in extracted[1:]:
                food_code = compact(row[0])
                if food_code.startswith("—"):
                    food_code = "—"
                if len(row) != 4 or not food_code or not re.match(r"^(?:\d+(?:\.\d+)*|—)$", food_code):
                    continue
                current["permissions"].append(
                    {
                        "food_code": food_code,
                        "food_name": normalize(row[1]),
                        "max_use": normalize(row[2]),
                        "notes": normalize(row[3]),
                        "source_ref": current["source_ref"],
                    }
                )
        additives.extend(headers)
    return additives


def parse_appendix_b(pdf):
    additives = []
    for page_index in range(152, 225):
        page = pdf.pages[page_index]
        page_number = page_index + 1
        for table in page.find_tables():
            rows = table.extract()
            if not rows or len(rows[0]) != 5 or "香料中文名称" not in compact(rows[0][2]):
                continue
            for row in rows[1:]:
                if len(row) != 5 or not re.match(r"^\d+$", compact(row[0])):
                    continue
                name = normalize(row[2])
                code = normalize(row[1])
                additives.append(
                    {
                        "page": page_number,
                        "official_name": name,
                        "cns_number": code,
                        "ins_number": normalize(row[4]),
                        "function_category": "食品用香料",
                        "source_type": "GB2760_AppendixB",
                        "source_ref": f"GB 2760—2024 表B.2/B.3，PDF第{page_number}页",
                        "source_scope": f"B-{code}",
                        "permissions": [
                            {
                                "food_code": "ALL_EXCEPT_B1",
                                "food_name": "各类食品（表B.1不得添加食品用香料、香精的食品除外）",
                                "max_use": "按生产需要适量使用（用于生产食品用香精）",
                                "notes": "应符合第6章和附录B使用原则",
                                "source_ref": f"GB 2760—2024 第6章、表B.2/B.3，PDF第{page_number}页",
                            }
                        ],
                    }
                )
    return additives


def parse_appendix_c(pdf):
    additives = []
    for page_index in range(225, 242):
        page = pdf.pages[page_index]
        page_number = page_index + 1
        for table in page.find_tables():
            rows = table.extract()
            if not rows:
                continue
            header = [compact(cell) for cell in rows[0]]
            if header[:3] == ["序号", "助剂中文名称", "助剂英文名称"] and len(header) == 3:
                for row in rows[1:]:
                    if len(row) != 3 or not re.match(r"^\d+$", compact(row[0])):
                        continue
                    name = normalize(row[1])
                    additives.append(
                        {
                            "page": page_number,
                            "official_name": name,
                            "cns_number": f"C1-{compact(row[0])}",
                            "ins_number": "—",
                            "function_category": "食品工业用加工助剂",
                            "source_type": "GB2760_AppendixC1",
                            "source_ref": f"GB 2760—2024 表C.1，PDF第{page_number}页",
                            "source_scope": f"C1-{compact(row[0])}",
                            "permissions": [
                                {
                                    "food_code": "ALL_PROCESSING",
                                    "food_name": "各类食品加工过程",
                                    "max_use": "按工艺必要性使用；制成最终成品前尽可能除去",
                                    "notes": "残留量不需限定，且不应在最终食品中发挥功能作用",
                                    "source_ref": f"GB 2760—2024 C.2、表C.1，PDF第{page_number}页",
                                }
                            ],
                        }
                    )
            elif header[:2] == ["序号", "助剂中文名称"] and len(header) == 5:
                for row in rows[1:]:
                    if len(row) != 5 or not re.match(r"^\d+$", compact(row[0])):
                        continue
                    name = normalize(row[1])
                    function = normalize(row[3])
                    scope = normalize(row[4])
                    additives.append(
                        {
                            "page": page_number,
                            "official_name": name,
                            "cns_number": f"C2-{compact(row[0])}",
                            "ins_number": "—",
                            "function_category": function or "食品工业用加工助剂",
                            "source_type": "GB2760_AppendixC2",
                            "source_ref": f"GB 2760—2024 表C.2，PDF第{page_number}页",
                            "source_scope": f"C2-{compact(row[0])}",
                            "permissions": [
                                {
                                    "food_code": "PROCESSING_SCOPED",
                                    "food_name": scope,
                                    "max_use": "按工艺必要性使用；制成最终成品前尽可能除去",
                                    "notes": function,
                                    "source_ref": f"GB 2760—2024 C.2、表C.2，PDF第{page_number}页",
                                }
                            ],
                        }
                    )
            elif header[:2] == ["序号", "酶"] and len(header) == 4:
                for row in rows[1:]:
                    if len(row) != 4 or not re.match(r"^\d+$", compact(row[0])):
                        continue
                    raw_name = normalize(row[1])
                    match = re.match(r"(.*[\u4e00-\u9fff）)])", raw_name)
                    name = match.group(1) if match else raw_name
                    additives.append(
                        {
                            "page": page_number,
                            "official_name": name,
                            "cns_number": f"C3-{compact(row[0])}",
                            "ins_number": "—",
                            "function_category": "食品用酶制剂",
                            "source_type": "GB2760_AppendixC3",
                            "source_ref": f"GB 2760—2024 表C.3，PDF第{page_number}页",
                            "source_scope": f"C3-{compact(row[0])}",
                            "permissions": [
                                {
                                    "food_code": "PROCESSING_ENZYME",
                                    "food_name": "相应食品加工工艺",
                                    "max_use": "按生产需要适量使用",
                                    "notes": f"来源：{normalize(row[2])}；供体：{normalize(row[3]) or '不适用'}",
                                    "source_ref": f"GB 2760—2024 C.2、表C.3，PDF第{page_number}页",
                                }
                            ],
                        }
                    )
    return additives


def derive_aliases(official_name):
    aliases = {normalize(official_name)}
    base = re.split(r"[（(]", official_name, 1)[0]
    if base:
        aliases.add(normalize(base))
    top_level = []
    depth = 0
    start = -1
    for index, char in enumerate(official_name):
        if char in "（(":
            if depth == 0:
                start = index + 1
            depth += 1
        elif char in "）)":
            depth -= 1
            if depth == 0 and start >= 0:
                top_level.append(official_name[start:index])
                start = -1
    for parenthetical in top_level:
        content = parenthetical
        for keyword in ("包括", "又名"):
            if keyword in content:
                content = content.split(keyword, 1)[1]
        for alias in re.split(r"[,，、;；/]", content):
            alias = normalize(alias)
            if alias and not alias.startswith(("仅", "除外")):
                aliases.add(alias)
    return sorted(alias for alias in aliases if alias)


def merge_duplicates(additives):
    merged = []
    by_key = {}
    for additive in additives:
        key = (additive["official_name"], additive["source_type"], additive.get("source_scope", ""))
        if key not in by_key:
            by_key[key] = additive
            merged.append(additive)
            continue
        target = by_key[key]
        existing = {
            (row["food_code"], row["food_name"], row["max_use"], row["notes"])
            for row in target["permissions"]
        }
        for permission in additive["permissions"]:
            identity = (
                permission["food_code"], permission["food_name"],
                permission["max_use"], permission["notes"],
            )
            if identity not in existing:
                target["permissions"].append(permission)
                existing.add(identity)
    return merged


def grade_for(additive):
    source_type = additive["source_type"]
    if source_type == "GB2760_AppendixA":
        functions = additive["function_category"]
        red_keys = ("防腐剂", "着色剂", "甜味剂", "护色剂", "漂白剂", "抗氧化剂", "面粉处理剂")
        yellow_keys = (
            "乳化剂", "稳定剂", "增稠剂", "酸度调节剂", "增味剂", "抗结剂", "被膜剂",
            "水分保持剂", "膨松剂", "凝固剂", "消泡剂", "其他",
        )
        if any(key in functions for key in red_keys):
            grade, rule = "red", "儿童慎入：具有生理活性或儿童暴露敏感的功能类别，按最小必要原则控制"
        elif any(key in functions for key in yellow_keys):
            grade, rule = "yellow", "注意：常规食品可用，但应关注类别用途、限量与混合使用限制"
        else:
            grade, rule = "green", "常规：GB 2760 附录A允许且未列入儿童敏感功能规则"
        return {
            "grade": grade,
            "rule": rule,
            "basis": f"{additive['source_ref']}；GB 2760—2024 第3章使用原则；分级规则：本库 function_grade_rules v1",
            "external_basis": "EFSA Food Additives 公开评估主题与 JECFA/WHO 公开评估库检索索引；https://www.efsa.europa.eu/en/search?f%5B0%5D=topic%3A3200；https://inchem.org/pages/jecfa",
        }
    if source_type.startswith("GB2760_AppendixB"):
        return {
            "grade": "green",
            "rule": "常规：GB 2760 附录B允许的食品用香料，禁止表B.1食品添加",
            "basis": f"{additive['source_ref']}；GB 2760—2024 第6章、表B.1",
            "external_basis": "JECFA/WHO 食品添加剂公开评价/specifications 检索索引；https://inchem.org/pages/jecfa",
        }
    return {
        "grade": "green",
        "rule": "常规：按工艺必要性使用的加工助剂/酶制剂，不应在终产品发挥功能作用",
        "basis": f"{additive['source_ref']}；GB 2760—2024 C.1使用原则",
        "external_basis": "JECFA/WHO 加工助剂与酶制剂公开评估/specifications 检索索引；https://inchem.org/pages/jecfa",
    }


def create_schema(connection):
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE additives (
            additive_id INTEGER PRIMARY KEY,
            cn_name TEXT NOT NULL,
            cns_number TEXT NOT NULL,
            ins_number TEXT NOT NULL,
            function_category TEXT NOT NULL,
            aliases_json TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            source_scope TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE additive_aliases (
            alias_id INTEGER PRIMARY KEY,
            additive_id INTEGER NOT NULL REFERENCES additives(additive_id) ON DELETE CASCADE,
            alias TEXT NOT NULL,
            UNIQUE(additive_id, alias)
        );
        CREATE TABLE permissions (
            permission_id INTEGER PRIMARY KEY,
            additive_id INTEGER NOT NULL REFERENCES additives(additive_id) ON DELETE CASCADE,
            food_code TEXT NOT NULL,
            food_name TEXT NOT NULL,
            max_use TEXT NOT NULL,
            notes TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            UNIQUE(additive_id, food_code, food_name, max_use, notes)
        );
        CREATE TABLE grades (
            additive_id INTEGER PRIMARY KEY REFERENCES additives(additive_id) ON DELETE CASCADE,
            grade TEXT NOT NULL CHECK(grade IN ('green','yellow','red')),
            rule TEXT NOT NULL,
            basis TEXT NOT NULL,
            external_basis TEXT NOT NULL
        );
        CREATE TABLE food_categories (
            food_code TEXT PRIMARY KEY,
            food_name TEXT NOT NULL,
            source_ref TEXT NOT NULL
        );
        CREATE INDEX idx_alias ON additive_aliases(alias);
        CREATE INDEX idx_additive_cn ON additives(cn_name);
        CREATE INDEX idx_permission_food ON permissions(food_code);
        """
    )


def build(pdf_path, output_path):
    with pdfplumber.open(pdf_path) as pdf:
        additives = parse_a1(pdf_path, pdf)
        additives.extend(parse_appendix_b(pdf))
        additives.extend(parse_appendix_c(pdf))
        additives = merge_duplicates(additives)

    if output_path.exists():
        output_path.unlink()
    connection = sqlite3.connect(output_path)
    create_schema(connection)
    categories = {}
    permission_counts = []
    for additive_id, additive in enumerate(additives, 1):
        aliases = derive_aliases(additive["official_name"])
        connection.execute(
            "INSERT INTO additives VALUES (?,?,?,?,?,?,?,?,?)",
            (
                additive_id,
                additive["official_name"],
                additive["cns_number"],
                additive["ins_number"],
                additive["function_category"],
                json.dumps(aliases, ensure_ascii=False, separators=(",", ":")),
                additive["source_type"],
                additive["source_ref"],
                additive.get("source_scope", ""),
            ),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO additive_aliases(additive_id,alias) VALUES (?,?)",
            [(additive_id, alias) for alias in aliases],
        )
        rows = []
        for permission in additive["permissions"]:
            row = (
                additive_id,
                permission["food_code"],
                permission["food_name"],
                permission["max_use"],
                permission["notes"],
                permission["source_ref"],
            )
            rows.append(row)
            categories[(permission["food_code"], permission["food_name"])] = permission["source_ref"]
        connection.executemany(
            "INSERT OR IGNORE INTO permissions(additive_id,food_code,food_name,max_use,notes,source_ref) VALUES (?,?,?,?,?,?)",
            rows,
        )
        permission_counts.append(len(rows))
        grade = grade_for(additive)
        connection.execute(
            "INSERT INTO grades VALUES (?,?,?,?,?)",
                (additive_id, grade["grade"], grade["rule"], grade["basis"], grade["external_basis"]),
        )
    connection.executemany(
        "INSERT OR IGNORE INTO food_categories VALUES (?,?,?)",
        [(code, name, source_ref) for (code, name), source_ref in sorted(categories.items())],
    )
    connection.commit()
    counts = {
        name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        for name in ("additives", "permissions", "grades", "food_categories", "additive_aliases")
    }
    missing_basis = connection.execute(
        "SELECT COUNT(*) FROM grades WHERE TRIM(basis)='' OR TRIM(external_basis)=''"
    ).fetchone()[0]
    connection.close()
    if counts["additives"] < 2000:
        raise RuntimeError(f"additives={counts['additives']}，低于验收要求 2000")
    if missing_basis:
        raise RuntimeError(f"grades 空依据 {missing_basis} 条")
    return {"pdf_sha256": source_manifest(pdf_path)["pdf_sha256"], "counts": counts, "permissions_per_additive": permission_counts}


def main():
    parser = argparse.ArgumentParser(description="从 GB 2760-2024 官方公开全文构建结构化 SQLite")
    parser.add_argument("--pdf", default=ROOT / "data" / "GB2760-2024.pdf", type=Path)
    parser.add_argument("--db", default=ROOT / "gb2760.db", type=Path)
    parser.add_argument("--manifest", default=ROOT / "data" / "source_manifest.json", type=Path)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if args.download or not args.pdf.exists():
        download_pdf(args.pdf)
    result = build(args.pdf, args.db)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(source_manifest(args.pdf), ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"db": str(args.db), **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
