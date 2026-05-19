"""
건강보험 MCP FastAPI 서버
로컬 실행: uvicorn server:app --host 0.0.0.0 --port 8400
VPS 실행: nohup uvicorn server:app --host 0.0.0.0 --port 8400 &
"""
import sqlite3
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP

DB_PATH = Path(__file__).parent / "health_fee.db"

app = FastAPI(title="건강보험 MCP API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/")
def root():
    return {"status": "ok", "description": "건강보험 MCP API"}


@app.get("/search")
def search(
    keyword: str = Query(..., description="검색어"),
    year: str = Query(None, description="연도 (예: 2024)"),
    doc_type: str = Query(None, description="문서유형: fee_schedule/billing_guide/billing_intro/drg_guide"),
    limit: int = Query(5, le=20),
):
    """키워드로 건강보험 문서 검색"""
    conn = get_conn()
    where = ["content LIKE ?"]
    params = [f"%{keyword}%"]

    if year:
        where.append("year = ?")
        params.append(year)
    if doc_type:
        where.append("doc_type = ?")
        params.append(doc_type)

    rows = conn.execute(
        f"SELECT filename, doc_type, year, is_latest, content FROM documents WHERE {' AND '.join(where)} LIMIT ?",
        params + [limit]
    ).fetchall()
    conn.close()

    results = []
    for r in rows:
        # keyword 주변 200자만 발췌
        idx = r["content"].find(keyword)
        snippet = r["content"][max(0, idx-100):idx+200].strip() if idx >= 0 else ""
        results.append({
            "filename": r["filename"],
            "doc_type": r["doc_type"],
            "year": r["year"],
            "is_latest": bool(r["is_latest"]),
            "snippet": snippet,
        })

    return {"keyword": keyword, "count": len(results), "results": results}


@app.get("/latest")
def get_latest(
    doc_type: str = Query(..., description="문서유형: fee_schedule/billing_guide/billing_intro/drg_guide"),
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
        snippet = row["content"][max(0, idx-100):idx+300].strip() if idx >= 0 else ""

    return {
        "filename": row["filename"],
        "year": row["year"],
        "snippet": snippet or row["content"][:500],
    }


@app.get("/compare")
def compare(
    keyword: str = Query(..., description="비교할 항목"),
    year1: str = Query(..., description="비교 연도1"),
    year2: str = Query(..., description="비교 연도2"),
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
            results[year] = row["content"][max(0, idx-50):idx+300].strip()
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
