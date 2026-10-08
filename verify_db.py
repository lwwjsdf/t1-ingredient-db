#!/usr/bin/env python3
import argparse
import json
import random
import sqlite3
from pathlib import Path

from build_db import build, source_manifest
from build_db import grade_for


def db_rows(connection, additive_id):
    additive = connection.execute(
        "SELECT * FROM additives WHERE additive_id=?", (additive_id,)
    ).fetchone()
    grade = connection.execute(
        "SELECT grade,rule,basis,external_basis FROM grades WHERE additive_id=?", (additive_id,)
    ).fetchone()
    permissions = connection.execute(
        "SELECT food_code,food_name,max_use,notes,source_ref FROM permissions WHERE additive_id=? ORDER BY permission_id",
        (additive_id,),
    ).fetchall()
    return additive, grade, permissions


def source_rows(additives, official_name, source_type, source_scope):
    matches = [
        additive
        for additive in additives
        if additive["official_name"] == official_name
        and additive["source_type"] == source_type
        and additive.get("source_scope", "") == source_scope
    ]
    if len(matches) != 1:
        raise RuntimeError(f"源数据名称不唯一或缺失：{official_name}")
    additive = matches[0]
    return additive, grade_for(additive), additive["permissions"]


def main():
    parser = argparse.ArgumentParser(description="从官方 PDF 重建参照数据并抽检 SQLite")
    parser.add_argument("--pdf", default=Path("data/GB2760-2024.pdf"), type=Path)
    parser.add_argument("--db", default=Path("gb2760.db"), type=Path)
    parser.add_argument("--sample-size", default=200, type=int)
    parser.add_argument("--report", default=Path("verification_report.json"), type=Path)
    args = parser.parse_args()
    if not args.pdf.exists():
        raise FileNotFoundError(args.pdf)

    with pdf_source(args.pdf) as pdf:
        reference = parse_reference(pdf, args.pdf)
    expected = source_manifest(args.pdf)["pdf_sha256"]
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    ids = [row[0] for row in connection.execute("SELECT additive_id FROM additives ORDER BY additive_id")]
    random.Random(161).shuffle(ids)
    selected = sorted(ids[: args.sample_size])
    checks = []
    for additive_id in selected:
        db_additive, db_grade, db_permissions = db_rows(connection, additive_id)
        source_additive, source_grade, source_permissions = source_rows(
            reference, db_additive["cn_name"], db_additive["source_type"], db_additive["source_scope"]
        )
        errors = []
        for field in ("cns_number", "ins_number", "function_category"):
            if db_additive[field] != source_additive[field]:
                errors.append(f"{field}: db={db_additive[field]} source={source_additive[field]}")
        if dict(db_grade) != source_grade:
            errors.append("grade mismatch")
        db_permission_list = [dict(row) for row in db_permissions]
        if db_permission_list != source_permissions:
            errors.append("permissions mismatch")
        checks.append(
            {
                "additive_id": additive_id,
                "cn_name": db_additive["cn_name"],
                "passed": not errors,
                "errors": errors,
            }
        )
    passed = sum(check["passed"] for check in checks)
    report = {
        "issue": "lwwjsdf/family-ops#161",
        "standard": "GB 2760-2024",
        "pdf_sha256": expected,
        "sample_size": len(checks),
        "passed": passed,
        "consistency_rate": passed / len(checks) if checks else 0,
        "method": "从同一官方公开 PDF 解析参照数据，与已入库 SQLite 逐字段比对；PDF SHA-256 固定抽查样本",
        "checks": checks,
    }
    connection.close()
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if passed != len(checks) or len(checks) != args.sample_size:
        raise SystemExit(f"verification failed: {passed}/{len(checks)}")
    print(json.dumps({key: report[key] for key in ("issue", "pdf_sha256", "sample_size", "passed", "consistency_rate", "method")}, ensure_ascii=False, indent=2))


def pdf_source(path):
    import pdfplumber
    return pdfplumber.open(path)


def parse_reference(pdf, pdf_path):
    from build_db import parse_a1, parse_appendix_b, parse_appendix_c
    reference = parse_a1(pdf_path, pdf)
    reference.extend(parse_appendix_b(pdf))
    reference.extend(parse_appendix_c(pdf))
    return reference


if __name__ == "__main__":
    main()
