"""
기존 documents 테이블 content를 청크로 분할하여 chunks 테이블 생성
실행: python chunk_db.py
"""
import sqlite3
import re
from pathlib import Path

DB_PATH = Path(__file__).parent / "health_fee.db"
CHUNK_MAX = 800
CHUNK_OVERLAP = 100


def split_chunks(content: str) -> list:
    """단락 기준으로 청크 분할, 최대 CHUNK_MAX자"""
    if not content:
        return []

    # 단락 분리: 빈 줄 또는 번호 목록 패턴
    paragraphs = re.split(r'\n{2,}', content)
    chunks = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= CHUNK_MAX:
            current = (current + "\n\n" + para).strip() if current else para
        else:
            if current:
                chunks.append(current)
            # 단락이 너무 길면 강제 분할
            while len(para) > CHUNK_MAX:
                chunks.append(para[:CHUNK_MAX])
                para = para[CHUNK_MAX - CHUNK_OVERLAP:]
            current = para.strip()

    if current:
        chunks.append(current)

    return [c for c in chunks if len(c.strip()) > 30]


def init_chunk_tables(conn: sqlite3.Connection):
    conn.executescript("""
    DROP TABLE IF EXISTS chunk_fts;
    DROP TABLE IF EXISTS chunks;

    CREATE TABLE chunks (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id    INTEGER NOT NULL,
        chunk_idx INTEGER NOT NULL,
        content   TEXT NOT NULL,
        FOREIGN KEY(doc_id) REFERENCES documents(id)
    );
    CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunks(doc_id);

    CREATE VIRTUAL TABLE chunk_fts
        USING fts5(content, content=chunks, content_rowid=id);
    """)
    conn.commit()


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    docs = conn.execute(
        "SELECT id, filename, doc_type, year, content FROM documents"
    ).fetchall()

    print(f"문서 {len(docs)}개 청크 분할 시작\n")
    init_chunk_tables(conn)

    total_chunks = 0
    for doc in docs:
        chunks = split_chunks(doc["content"] or "")
        for idx, chunk_text in enumerate(chunks):
            conn.execute(
                "INSERT INTO chunks (doc_id, chunk_idx, content) VALUES (?,?,?)",
                (doc["id"], idx, chunk_text)
            )
        total_chunks += len(chunks)
        print(f"  [{doc['doc_type']}] {doc['year']} — {len(chunks)}청크 ({doc['filename'][:40]})")

    conn.commit()
    print(f"\n총 {total_chunks}개 청크 생성 완료")

    # FTS5 인덱스 구축
    print("FTS5 인덱스 구축 중...")
    conn.execute("INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')")
    conn.commit()
    print("FTS5 구축 완료")

    # 검증
    cnt = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"검증: chunks 테이블 {cnt}건")

    # 소아 검색 테스트
    rows = conn.execute("""
        SELECT c.content FROM chunk_fts
        JOIN chunks c ON chunk_fts.rowid = c.id
        WHERE chunk_fts MATCH '소아'
        LIMIT 2
    """).fetchall()
    print(f"\n소아 FTS5 검색: {len(rows)}건")
    for r in rows:
        print(" ", r[0][:100])

    conn.close()


if __name__ == "__main__":
    main()
