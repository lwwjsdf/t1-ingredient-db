# t1-ingredient-db

GB 2760-2024《食品安全国家标准 食品添加剂使用标准》结构化查询库，数据来自国家卫生健康委员会公告页附带的官方全文 PDF；不抓取商业数据库。

## 生成

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 build_db.py --download
python3 verify_db.py --sample-size 200
```

`build_db.py` 会下载官方公开 PDF、解析附录 A/B/C，并生成 `gb2760.db` 与 `data/source_manifest.json`。`verify_db.py` 使用固定种子抽样 200 个添加剂，对 CNS/INS/功能/分级/限量逐字段比对官方 PDF 参照数据。

## 查询 API

```bash
python3 api.py --port 8787
curl -s --get 'http://127.0.0.1:8787/api/query' --data-urlencode 'name=脱氢乙酸钠'
```

支持添加剂中文名、常见别名、CNS 号和 INS 号；响应包含分级、规则、国标与公开评估出处、全部适用范围和限量。
