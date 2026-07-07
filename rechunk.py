import sqlite3, json, os, urllib.request
from pathlib import Path

DB_PATH = Path(__file__).parent / "health_fee.db"
CHROMA_PATH = Path(__file__).parent / "chroma_db"
EMBED_MODEL = "gemini-embedding-2"
CHUNK_SIZE = 1800
OVERLAP = 200

def _embed(text):
    key = os.environ.get("GEMINI_API_KEY", "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{EMBED_MODEL}:embedContent?key={key}"
    payload = json.dumps({"model": f"models/{EMBED_MODEL}", "content": {"parts": [{"text": text[:2000]}]}}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["embedding"]["values"]

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT s.id, s.section_title, s.content, d.filename, d.doc_type, d.year
    FROM sections s JOIN documents d ON s.doc_id=d.id
    WHERE d.doc_type='drug_criteria' AND LENGTH(s.content)>5000
""").fetchall()
print(f"대상 섹션: {len(rows)}개")

import chromadb
client = chromadb.PersistentClient(path=str(CHROMA_PATH))
col = client.get_collection("health_fee_sections")
existing = set(col.get()["ids"])

ids, vecs, metas, docs = [], [], [], []
for row in rows:
    text = row["content"]
    i, ci = 0, 0
    while i < len(text):
        chunk = text[i:i+CHUNK_SIZE]
        cid = f"drug_chunk_{row['id']}_{ci}"
        if cid not in existing:
            print(f"  {row['filename']} 청크{ci+1} 임베딩...")
            ids.append(cid)
            vecs.append(_embed(chunk))
            metas.append({"filename": row["filename"], "doc_type": row["doc_type"],
                          "year": row["year"], "section": f"{row['section_title']}(청크{ci+1})"})
            docs.append(chunk)
        i += CHUNK_SIZE - OVERLAP
        ci += 1

if ids:
    col.add(ids=ids, embeddings=vecs, metadatas=metas, documents=docs)
    print(f"완료: {len(ids)}개 청크 추가")
else:
    print("신규 없음")
conn.close()
