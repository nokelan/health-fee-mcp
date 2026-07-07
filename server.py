"""
건강보험 MCP FastAPI 서버 (v3 — 벡터 검색)
로컬 실행: uvicorn server:app --host 0.0.0.0 --port 8400
VPS 실행: nohup uvicorn server:app --host 0.0.0.0 --port 8400 &
"""
import sqlite3
import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from fastapi_mcp import FastApiMCP

DB_PATH = Path(__file__).parent / "health_fee.db"
QA_LOG_PATH = Path(__file__).parent / "qa_log.jsonl"
CHROMA_PATH = Path(__file__).parent / "chroma_db"
EMBED_MODEL = "gemini-embedding-2"

QA_KEYWORDS = ["수가", "요양급여", "신포괄", "포괄수가", "청구", "DRG", "질병군", "산정기준", "급여기준", "행위료", "마취", "입원", "외래", "가산", "감산"]


class QALog(BaseModel):
    question: str
    answer: str
    sources: list = []


app = FastAPI(title="건강보험 MCP API", version="3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _gemini_embed(text: str) -> list:
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        raise RuntimeError("GEMINI_API_KEY 미설정")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{EMBED_MODEL}:embedContent?key={gemini_key}"
    payload = json.dumps({"model": f"models/{EMBED_MODEL}", "content": {"parts": [{"text": text[:2000]}]}}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["embedding"]["values"]


def _chroma_search(keyword: str, limit: int, doc_type: str = None, year: str = None) -> list:
    try:
        import chromadb
    except ImportError:
        raise RuntimeError("chromadb 미설치")
    if not CHROMA_PATH.exists():
        raise RuntimeError("임베딩 DB 없음")

    qvec = _gemini_embed(keyword)
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    col = client.get_collection("health_fee_sections")

    where = {}
    if doc_type:
        where["doc_type"] = doc_type
    if year:
        where["year"] = year

    res = col.query(query_embeddings=[qvec], n_results=limit, where=where if where else None)

    results = []
    for i, doc_id in enumerate(res["ids"][0]):
        meta = res["metadatas"][0][i]
        results.append({
            "filename": meta.get("filename"),
            "doc_type": meta.get("doc_type"),
            "year": meta.get("year"),
            "section": meta.get("section"),
            "snippet": (res["documents"][0][i] or "")[:400],
            "distance": res["distances"][0][i] if res.get("distances") else None,
            "source": "vector",
        })
    return results


@app.get("/")
def root():
    return {"status": "ok", "description": "건강보험 MCP API v3 (벡터 검색)"}


@app.get("/chat")
def chat_ui():
    return FileResponse(Path(__file__).parent / "chatbot.html", media_type="text/html")


@app.post("/log")
def log_qa(payload: QALog):
    """Q&A 자동 저장 (요양급여/신포괄 관련만 필터링)"""
    text = payload.question + payload.answer
    if not any(kw in text for kw in QA_KEYWORDS):
        return {"status": "skip", "reason": "관련 키워드 없음"}
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "question": payload.question,
        "answer": payload.answer,
        "sources": payload.sources,
    }
    with open(QA_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"status": "ok"}


def _keyword_search(keyword: str, limit: int, doc_type: str = None, year: str = None) -> list:
    """SQLite LIKE 기반 키워드 직접 검색 (다단어면 첫 단어로 검색)"""
    conn = get_conn()
    search_term = keyword.split()[0] if " " in keyword else keyword
    where = ["s.content LIKE ?"]
    params: list = [f"%{search_term}%"]
    if doc_type:
        where.append("d.doc_type = ?")
        params.append(doc_type)
    if year:
        where.append("d.year = ?")
        params.append(year)
    sql = f"""
        SELECT s.id, s.section_title, s.content, d.filename, d.doc_type, d.year
        FROM sections s JOIN documents d ON s.doc_id = d.id
        WHERE {' AND '.join(where)}
        ORDER BY CASE WHEN d.doc_type='drug_criteria' THEN 0 ELSE 1 END,
                 LENGTH(s.content) DESC
        LIMIT {limit}
    """
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    results = []
    for row in rows:
        content = row["content"] or ""
        idx = content.find(search_term)
        snippet = content[max(0, idx - 100):idx + 350].strip() if idx >= 0 else content[:400]
        results.append({
            "filename": row["filename"],
            "doc_type": row["doc_type"],
            "year": row["year"],
            "section": row["section_title"] or "",
            "snippet": snippet,
            "distance": None,
            "source": "keyword",
        })
    return results


@app.get("/search")
def search(
    keyword: str = Query(..., description="검색어"),
    year: str = Query(None, description="연도 (예: 2026)"),
    doc_type: str = Query(None, description="문서유형 (fee_schedule / billing_guide 등)"),
    limit: int = Query(5, le=20),
    mode: str = Query("hybrid", description="vector / keyword / hybrid"),
):
    """하이브리드 검색: 벡터(의미) + 키워드(직접) 결합. mode=hybrid(기본), vector, keyword"""
    try:
        if mode == "vector":
            results = _chroma_search(keyword, limit, doc_type, year)
        elif mode == "keyword":
            results = _keyword_search(keyword, limit, doc_type, year)
        else:
            kw = _keyword_search(keyword, limit, doc_type, year)
            vec = _chroma_search(keyword, limit, doc_type, year)
            seen: set = set()
            merged = []
            for r in kw:  # keyword 우선
                key = (r["filename"], r["section"])
                if key not in seen:
                    seen.add(key)
                    merged.append(r)
            for r in vec:  # 벡터로 나머지 보완
                key = (r["filename"], r["section"])
                if key not in seen:
                    seen.add(key)
                    merged.append(r)
            results = merged[:limit]
        return {"keyword": keyword, "mode": mode, "count": len(results), "results": results}
    except RuntimeError as e:
        return {"error": str(e)}


@app.get("/vsearch")
def vsearch(
    keyword: str = Query(..., description="검색어 (벡터 전용)"),
    year: str = Query(None),
    doc_type: str = Query(None),
    limit: int = Query(5, le=20),
):
    """벡터(의미론적) 전용 검색"""
    try:
        results = _chroma_search(keyword, limit, doc_type, year)
        return {"keyword": keyword, "mode": "vector", "count": len(results), "results": results}
    except RuntimeError as e:
        return {"error": str(e)}


@app.get("/synonyms")
def list_synonyms(canonical: str = Query(None, description="표준어 (미입력 시 전체)")):
    """동의어 사전 조회"""
    conn = get_conn()
    if canonical:
        rows = conn.execute(
            "SELECT canonical, synonym FROM synonyms WHERE canonical=? ORDER BY synonym",
            (canonical,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT canonical, synonym FROM synonyms ORDER BY canonical, synonym"
        ).fetchall()
    conn.close()
    return [{"canonical": r["canonical"], "synonym": r["synonym"]} for r in rows]


@app.post("/synonyms")
def add_synonym(
    canonical: str = Query(..., description="표준어"),
    synonym: str = Query(..., description="동의어"),
):
    """동의어 추가"""
    conn = get_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO synonyms (canonical, synonym) VALUES (?,?)", (canonical, synonym))
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM synonyms WHERE canonical=?", (canonical,)).fetchone()[0]
        return {"status": "ok", "canonical": canonical, "synonym": synonym, "total_for_canonical": count}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


@app.delete("/synonyms")
def delete_synonym(
    canonical: str = Query(..., description="표준어"),
    synonym: str = Query(..., description="삭제할 동의어"),
):
    """동의어 삭제"""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM synonyms WHERE canonical=? AND synonym=?", (canonical, synonym))
        conn.commit()
        return {"status": "ok", "deleted": f"{canonical} → {synonym}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


@app.get("/regulation")
def get_regulation(
    category: str = Query(None, description="카테고리 (예: 마취료)"),
    condition: str = Query(None, description="조건 (예: 1세이상_6세미만)"),
    hosp_type: str = Query(None, description="기관유형: 일반 / 상급종합"),
):
    """구조화된 규정 조회"""
    conn = get_conn()
    where = []
    params = []
    if category:
        where.append("category = ?")
        params.append(category)
    if condition:
        where.append("condition = ?")
        params.append(condition)
    if hosp_type:
        where.append("hosp_type = ?")
        params.append(hosp_type)
    sql = "SELECT * FROM regulations"
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return {"count": len(rows), "results": [dict(r) for r in rows]}


@app.get("/latest")
def get_latest(
    doc_type: str = Query(..., description="문서유형"),
    keyword: str = Query(None, description="추가 검색어"),
):
    """최신판 문서에서 조회"""
    conn = get_conn()
    if keyword:
        row = conn.execute(
            "SELECT filename, year, content FROM documents WHERE doc_type=? AND is_latest=1 AND content LIKE ?",
            (doc_type, f"%{keyword}%")
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT filename, year, content FROM documents WHERE doc_type=? AND is_latest=1",
            (doc_type,)
        ).fetchone()
    conn.close()
    if not row:
        return {"error": "해당 문서를 찾을 수 없습니다."}
    snippet = ""
    if keyword and row["content"]:
        idx = row["content"].find(keyword)
        snippet = row["content"][max(0, idx - 100):idx + 400].strip() if idx >= 0 else ""
    return {"filename": row["filename"], "year": row["year"], "snippet": snippet or row["content"][:500]}


@app.get("/compare")
def compare(
    keyword: str = Query(...),
    year1: str = Query(...),
    year2: str = Query(...),
    doc_type: str = Query("fee_schedule"),
):
    """두 연도 간 내용 비교"""
    conn = get_conn()
    results = {}
    for year in (year1, year2):
        row = conn.execute(
            "SELECT content FROM documents WHERE doc_type=? AND year=? AND content LIKE ?",
            (doc_type, year, f"%{keyword}%")
        ).fetchone()
        if row:
            idx = row["content"].find(keyword)
            results[year] = row["content"][max(0, idx - 50):idx + 400].strip()
        else:
            results[year] = "해당 연도 데이터 없음"
    conn.close()
    return {"keyword": keyword, "doc_type": doc_type, "compare": results}


@app.get("/list")
def list_docs():
    """보유 문서 목록"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT doc_type, year, is_latest, filename FROM documents ORDER BY doc_type, year"
    ).fetchall()
    conn.close()
    return [{"doc_type": r["doc_type"], "year": r["year"],
             "is_latest": bool(r["is_latest"]), "filename": r["filename"]} for r in rows]


mcp = FastApiMCP(app)
mcp.mount()


class ChatProxyRequest(BaseModel):
    body: dict


@app.post("/chat_proxy")
def chat_proxy(req: ChatProxyRequest):
    import urllib.error
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="서버 API 키 미설정")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
    payload = json.dumps(req.body).encode("utf-8")
    http_req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(http_req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=e.code, detail=e.read().decode())
