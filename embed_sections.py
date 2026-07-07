"""섹션 임베딩 생성 및 ChromaDB 저장. 사용: python3 embed_sections.py"""
import sqlite3
import json
import os
import time
from pathlib import Path
import urllib.request

DB_PATH = Path(__file__).parent / "health_fee.db"
CHROMA_PATH = Path(__file__).parent / "chroma_db"
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
EMBED_MODEL = "gemini-embedding-2"
EMBED_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{EMBED_MODEL}:embedContent?key={GEMINI_KEY}"
BATCH_SIZE = 20

def gemini_embed(texts: list[str]) -> list[list[float]]:
    results = []
    for text in texts:
        payload = json.dumps({
            "model": f"models/{EMBED_MODEL}",
            "content": {"parts": [{"text": text[:2000]}]}
        }).encode()
        req = urllib.request.Request(EMBED_URL, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        results.append(data["embedding"]["values"])
        time.sleep(0.05)  # rate limit 방지
    return results

def main():
    import chromadb
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    col = client.get_or_create_collection("health_fee_sections")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT s.id, s.section_title, s.content, d.doc_type, d.year, d.filename "
        "FROM sections s JOIN documents d ON s.doc_id = d.id"
    ).fetchall()
    conn.close()

    # 이미 임베딩된 ID 확인
    existing = set(col.get()["ids"])
    pending = [r for r in rows if str(r["id"]) not in existing]
    print(f"전체: {len(rows)}개, 기존: {len(existing)}개, 신규: {len(pending)}개")

    for i in range(0, len(pending), BATCH_SIZE):
        batch = pending[i:i+BATCH_SIZE]
        texts = [f"{r['section_title']}\n{r['content'][:1500]}" for r in batch]
        ids = [str(r["id"]) for r in batch]
        metas = [{"doc_type": r["doc_type"], "year": r["year"] or "", "filename": r["filename"], "section": r["section_title"]} for r in batch]

        try:
            embeddings = gemini_embed(texts)
            col.add(embeddings=embeddings, ids=ids, documents=texts, metadatas=metas)
            print(f"  [{i+len(batch)}/{len(pending)}] 완료")
        except Exception as e:
            print(f"  오류 (id {ids[0]}~): {e}")
            time.sleep(2)

    print(f"완료: ChromaDB {CHROMA_PATH}")

if __name__ == "__main__":
    if not GEMINI_KEY:
        print("GEMINI_API_KEY 환경변수 필요")
        exit(1)
    main()
