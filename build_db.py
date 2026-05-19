"""
건강보험 PDF → SQLite 변환 스크립트
실행: pip install pdfplumber  →  python build_db.py
"""
import sqlite3
import re
import sys
from pathlib import Path
import pdfplumber

PDF_DIR = Path(r"D:\건강보험공단 PDF")
DB_PATH = Path(__file__).parent / "health_fee.db"

# 문서 유형 자동 분류 키워드
TYPE_RULES = [
    ("fee_schedule",    ["요양급여비용", "수가표", "행위급여목록", "건강보험요양급여비용"]),
    ("billing_guide",   ["청구방법", "작성요령", "명세서서식", "청구서명세서"]),
    ("billing_intro",   ["청구길라잡이", "길라잡이"]),
    ("drg_guide",       ["포괄수가", "DRG", "포괄수가제"]),
]

YEAR_PATTERN = re.compile(r"20(\d{2})")


def detect_type(text: str) -> str:
    for doc_type, keywords in TYPE_RULES:
        if any(kw in text for kw in keywords):
            return doc_type
    return "other"


def detect_year(text: str) -> str | None:
    m = YEAR_PATTERN.search(text)
    return f"20{m.group(1)}" if m else None


def extract_head(pdf_path: Path, pages: int = 2) -> str:
    """첫 N페이지 텍스트 추출 (분류용)"""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return "\n".join(
                (p.extract_text() or "") for p in pdf.pages[:pages]
            )
    except Exception as e:
        print(f"  [경고] 헤드 추출 실패: {pdf_path.name} — {e}")
        return ""


def extract_full(pdf_path: Path) -> str:
    """전체 텍스트 추출"""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            parts = []
            for i, page in enumerate(pdf.pages):
                t = page.extract_text()
                if t:
                    parts.append(t)
                if (i + 1) % 50 == 0:
                    print(f"    {i+1}/{len(pdf.pages)} 페이지 처리 중...")
            return "\n".join(parts)
    except Exception as e:
        print(f"  [오류] 전체 추출 실패: {pdf_path.name} — {e}")
        return ""


def init_db(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS documents (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        filename    TEXT NOT NULL,
        doc_type    TEXT NOT NULL,
        year        TEXT,
        is_latest   INTEGER DEFAULT 0,
        content     TEXT,
        created_at  TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_type_year ON documents(doc_type, year);
    CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts
        USING fts5(content, content=documents, content_rowid=id);
    """)
    conn.commit()


def mark_latest(conn: sqlite3.Connection):
    """같은 doc_type에서 연도가 가장 최신인 것을 is_latest=1로 표시"""
    conn.execute("UPDATE documents SET is_latest = 0")
    rows = conn.execute(
        "SELECT doc_type, MAX(year) FROM documents WHERE year IS NOT NULL GROUP BY doc_type"
    ).fetchall()
    for doc_type, max_year in rows:
        conn.execute(
            "UPDATE documents SET is_latest = 1 WHERE doc_type=? AND year=?",
            (doc_type, max_year)
        )
    conn.commit()


def main():
    pdf_files = list(PDF_DIR.glob("*.pdf")) + list(PDF_DIR.glob("**/*.pdf"))
    if not pdf_files:
        print(f"PDF 파일 없음: {PDF_DIR}")
        sys.exit(1)

    print(f"총 {len(pdf_files)}개 PDF 발견\n")

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    already = {row[0] for row in conn.execute("SELECT filename FROM documents")}

    for i, pdf_path in enumerate(pdf_files, 1):
        if pdf_path.name in already:
            print(f"[{i}/{len(pdf_files)}] 스킵 (이미 처리됨): {pdf_path.name}")
            continue

        print(f"[{i}/{len(pdf_files)}] {pdf_path.name}")

        head     = extract_head(pdf_path)
        doc_type = detect_type(head)
        year     = detect_year(head) or detect_year(pdf_path.name)

        print(f"  유형: {doc_type} | 연도: {year or '미확인'}")

        content = extract_full(pdf_path)

        conn.execute(
            "INSERT INTO documents (filename, doc_type, year, content) VALUES (?,?,?,?)",
            (pdf_path.name, doc_type, year, content)
        )
        conn.commit()

    mark_latest(conn)

    # 결과 요약
    print("\n=== 처리 결과 ===")
    for row in conn.execute(
        "SELECT doc_type, year, is_latest, filename FROM documents ORDER BY doc_type, year"
    ):
        latest = " ★최신" if row[2] else ""
        print(f"  [{row[0]}] {row[1] or '연도미상'}{latest} — {row[3]}")

    conn.close()
    print(f"\nDB 저장 완료: {DB_PATH}")


if __name__ == "__main__":
    main()
