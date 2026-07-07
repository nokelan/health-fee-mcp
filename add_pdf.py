"""단일 PDF를 DB에 추가하는 스크립트. 사용: python3 add_pdf.py <pdf_path> [doc_type] [year]"""
import sqlite3
import re
import sys
from pathlib import Path
import pdfplumber

DB_PATH = Path(__file__).parent / "health_fee.db"
SECTION_PATTERN = re.compile(r'(제\s*\d+\s*[장절관]\s*[^\n]{1,50})', re.MULTILINE)

TYPE_RULES = [
    ("fee_schedule",   ["요양급여비용", "수가표", "행위급여목록"]),
    ("billing_guide",  ["청구방법", "작성요령", "명세서서식"]),
    ("billing_intro",  ["청구길라잡이", "길라잡이"]),
    ("drg_guide",      ["포괄수가", "DRG", "신포괄", "신포괄수가"]),
    ("standard_guide", ["실무안내", "요양급여기준"]),
]

def detect_type(text):
    for doc_type, keywords in TYPE_RULES:
        if any(kw in text for kw in keywords):
            return doc_type
    return "other"

def detect_year(text):
    m = re.search(r"20(\d{2})", text)
    return f"20{m.group(1)}" if m else None

def main():
    if len(sys.argv) < 2:
        print("사용법: python3 add_pdf.py <pdf_path> [doc_type] [year]")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"파일 없음: {pdf_path}")
        sys.exit(1)

    print(f"PDF 처리 중: {pdf_path.name}")
    with pdfplumber.open(pdf_path) as pdf:
        pages = []
        for i, page in enumerate(pdf.pages):
            t = page.extract_text()
            if t:
                pages.append(t)
            if (i + 1) % 50 == 0:
                print(f"  {i+1}/{len(pdf.pages)} 페이지...")
        full_text = "\n".join(pages)

    head = full_text[:2000]
    doc_type = sys.argv[2] if len(sys.argv) > 2 else detect_type(full_text)
    year = sys.argv[3] if len(sys.argv) > 3 else detect_year(head)
    print(f"  doc_type={doc_type}, year={year}, 텍스트={len(full_text)}자")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    # 중복 확인
    existing = conn.execute("SELECT id FROM documents WHERE filename=?", (pdf_path.name,)).fetchone()
    if existing:
        print(f"  이미 등록됨 (id={existing[0]}), 건너뜀")
        conn.close()
        return

    # 기존 같은 타입 is_latest 해제
    if year:
        conn.execute("UPDATE documents SET is_latest=0 WHERE doc_type=? AND is_latest=1", (doc_type,))

    conn.execute(
        "INSERT INTO documents (filename, doc_type, year, is_latest, content) VALUES (?,?,?,?,?)",
        (pdf_path.name, doc_type, year, 1 if year else 0, full_text)
    )
    doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    print(f"  문서 등록 완료 (id={doc_id})")

    # 섹션 분리
    matches = list(SECTION_PATTERN.finditer(full_text))
    sections = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i+1].start() if i+1 < len(matches) else len(full_text)
        content = full_text[start:end].strip()
        if len(content) > 50:
            sections.append((doc_id, m.group(1).strip(), content))

    if not sections:
        sections = [(doc_id, "전체", full_text)]

    conn.executemany("INSERT INTO sections (doc_id, section_title, content) VALUES (?,?,?)", sections)
    print(f"  섹션 {len(sections)}개 등록")

    # FTS5 재구축
    conn.execute("INSERT INTO section_fts(section_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    print(f"완료: {pdf_path.name} → {doc_type} {year}, 섹션 {len(sections)}개")

if __name__ == "__main__":
    main()
