#!/usr/bin/env python3
import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


class ApiHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            return self.send_json({"status": "ok"})
        if parsed.path == "/api/query":
            query = parse_qs(parsed.query).get("name", [""])[0].strip()
            if not query:
                return self.send_json({"error": "name is required"}, 400)
            return self.send_json({"results": query_additive(self.db_path, query)})
        self.send_json({"error": "not found"}, 404)

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def query_additive(db_path, query):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    matches = connection.execute(
        """
        SELECT DISTINCT a.*
        FROM additives a
        LEFT JOIN additive_aliases x ON x.additive_id=a.additive_id
        WHERE a.cn_name=? OR a.cns_number=? OR a.ins_number=? OR x.alias=?
        """,
        (query, query, query, query),
    ).fetchall()
    if not matches:
        pattern = f"%{query}%"
        matches = connection.execute(
            """
            SELECT DISTINCT a.*
            FROM additives a
            LEFT JOIN additive_aliases x ON x.additive_id=a.additive_id
            WHERE a.cn_name LIKE ? OR a.cns_number LIKE ? OR a.ins_number LIKE ? OR x.alias LIKE ?
            LIMIT 20
            """,
            (pattern, pattern, pattern, pattern),
        ).fetchall()
    results = []
    for additive in matches:
        additive_id = additive["additive_id"]
        aliases = json.loads(additive["aliases_json"])
        grade = dict(connection.execute("SELECT * FROM grades WHERE additive_id=?", (additive_id,)).fetchone())
        permissions = [
            dict(row)
            for row in connection.execute(
                "SELECT food_code,food_name,max_use,notes,source_ref FROM permissions WHERE additive_id=? ORDER BY permission_id",
                (additive_id,),
            )
        ]
        results.append(
            {
                "additive_id": additive_id,
                "cn_name": additive["cn_name"],
                "cns_number": additive["cns_number"],
                "ins_number": additive["ins_number"],
                "function_category": additive["function_category"],
                "aliases": aliases,
                "grade": grade,
                "permissions": permissions,
                "source_ref": additive["source_ref"],
            }
        )
    connection.close()
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GB 2760-2024 additive query API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument("--db", default=Path(__file__).with_name("gb2760.db"), type=Path)
    args = parser.parse_args()
    ApiHandler.db_path = args.db
    with ThreadingHTTPServer((args.host, args.port), ApiHandler) as server:
        print(f"Listening on http://{args.host}:{args.port}", flush=True)
        server.serve_forever()
