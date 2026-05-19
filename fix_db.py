"""
DB 중복 제거 + other 재분류
실행: python fix_db.py
"""
import sqlite3
import pdfplumber
from pathlib import Path

PDF_DIR = Path(r"D:\건강보험공단 PDF")
DB_PATH = Path(__file__).parent / "health_fee.db"

TYPE_RULES = [
    ("fee_schedule",  ["요양급여비용", "수가표", "행위급여목록", "상대가치점수", "약제급여목록"]),
    ("billing_guide", ["청구방법", "작성요령", "명세서서식", "청구서명세서", "심사청구"]),
    ("billing_intro", ["청구길라잡이", "길라잡이", "청구 길라잡이"]),
    ("drg_guide",     ["포괄수가", "DRG", "포괄수가제", "신포괄"]),
    ("standard_guide",["실무안내", "요양급여기준", "급여기준", "비급여"]),
]


def detect_type(text: str) -> str:
    for doc_type, keywords in TYPE_RULES:
        if any(kw in text for kw in keywords):
            return doc_type
    return "other"


def main():
    conn = sqlite3.connect(DB_PATH)

    # 1. 중복 제거 (filename 기준, id 가장 작은 것만 남김)
    dups = conn.execute("""
        SELECT filename, COUNT(*) cnt FROM documents
        GROUP BY filename HAVING cnt > 1
    """).fetchall()

    removed = 0
    for fname, _ in dups:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM documents WHERE filename=? ORDER BY id", (fname,)
        ).fetchall()]
        for dup_id in ids[1:]:
            conn.execute("DELETE FROM documents WHERE id=?", (dup_id,))
            removed += 1

    conn.commit()
    print(f"중복 제거: {removed}건")

    # 2. other → 재분류 (더 많은 페이지 읽기)
    others = conn.execute(
        "SELECT id, filename FROM documents WHERE doc_type='other'"
    ).fetchall()

    reclassified = 0
    for doc_id, filename in others:
        pdf_path = PDF_DIR / filename
        if not pdf_path.exists():
            continue
        try:
            with pdfplumber.open(pdf_path) as pdf:
                text = "\n".join(
                    (p.extract_text() or "") for p in pdf.pages[:5]
                )
            new_type = detect_type(text)
            if new_type != "other":
                conn.execute(
                    "UPDATE documents SET doc_type=? WHERE id=?",
                    (new_type, doc_id)
                )
                print(f"  재분류: {filename} → {new_type}")
                reclassified += 1
        except Exception as e:
            print(f"  오류: {filename} — {e}")

    conn.commit()
    print(f"재분류: {reclassified}건")

    # 3. is_latest 재계산
    conn.execute("UPDATE documents SET is_latest = 0")
    rows = conn.execute(
        "SELECT doc_type, MAX(year) FROM documents WHERE year IS NOT NULL GROUP BY doc_type"
    ).fetchall()
    for doc_type, max_year in rows:
        conn.execute(
            "UPDATE documents SET is_latest=1 WHERE doc_type=? AND year=?",
            (doc_type, max_year)
        )
    conn.commit()

    # 4. 최종 결과 출력
    print("\n=== 최종 결과 ===")
    for row in conn.execute(
        "SELECT doc_type, year, is_latest, filename FROM documents ORDER BY doc_type, year"
    ):
        latest = " ★최신" if row[2] else ""
        print(f"  [{row[0]}] {row[1] or '연도미상'}{latest} — {row[3]}")

    conn.close()
    print(f"\nDB: {DB_PATH}")


if __name__ == "__main__":
    main()
