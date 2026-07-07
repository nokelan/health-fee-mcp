"""
건강보험 PDF → SQLite 변환 스크립트 (개선판 v2)
- sections 테이블: 장/절 단위 분리
- regulations 테이블: 구조화된 규정 (마취료 가산 초기값 포함)
- FTS5 실제 채움
실행: pip install pdfplumber  →  python build_db.py
"""
import sqlite3
import re
import sys
from pathlib import Path
import pdfplumber

PDF_DIR = Path(r"D:\건강보험공단 PDF")
DB_PATH = Path(__file__).parent / "health_fee.db"

TYPE_RULES = [
    ("fee_schedule",   ["요양급여비용", "수가표", "행위급여목록", "건강보험요양급여비용"]),
    ("billing_guide",  ["청구방법", "작성요령", "명세서서식", "청구서명세서"]),
    ("billing_intro",  ["청구길라잡이", "길라잡이"]),
    ("drg_guide",      ["포괄수가", "DRG", "포괄수가제"]),
    ("standard_guide", ["실무안내", "요양급여기준", "급여기준"]),
]

YEAR_PATTERN = re.compile(r"20(\d{2})")
SECTION_PATTERN = re.compile(r'(제\s*\d+\s*[장절관]\s*[^\n]{1,50})', re.MULTILINE)


def detect_type(text: str) -> str:
    for doc_type, keywords in TYPE_RULES:
        if any(kw in text for kw in keywords):
            return doc_type
    return "other"


def detect_year(text: str) -> str | None:
    m = YEAR_PATTERN.search(text)
    return f"20{m.group(1)}" if m else None


def extract_head(pdf_path: Path, pages: int = 2) -> str:
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages[:pages])
    except Exception as e:
        print(f"  [경고] 헤드 추출 실패: {pdf_path.name} — {e}")
        return ""


def extract_full(pdf_path: Path) -> str:
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
        print(f"  [오류] {pdf_path.name} — {e}")
        return ""


def split_sections(full_text: str) -> list[tuple[str, str]]:
    """텍스트를 장/절 단위로 분리. 반환: [(title, content)]"""
    parts = SECTION_PATTERN.split(full_text)
    sections = []

    if parts[0].strip():
        sections.append(("서문", parts[0].strip()))

    for i in range(1, len(parts), 2):
        title = parts[i].strip() if i < len(parts) else ""
        content = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if title and content:
            sections.append((title, content))

    return sections if sections else [("전체", full_text)]


def init_db(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS documents (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        filename   TEXT NOT NULL,
        doc_type   TEXT NOT NULL,
        year       TEXT,
        is_latest  INTEGER DEFAULT 0,
        content    TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_type_year ON documents(doc_type, year);

    CREATE TABLE IF NOT EXISTS sections (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id        INTEGER REFERENCES documents(id),
        section_title TEXT,
        content       TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_sec_docid ON sections(doc_id);

    CREATE TABLE IF NOT EXISTS regulations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        category    TEXT NOT NULL,
        subcategory TEXT,
        condition   TEXT,
        rule_text   TEXT,
        rate        REAL,
        code_prefix TEXT,
        hosp_type   TEXT DEFAULT '일반',
        eff_year    TEXT,
        source_doc  TEXT
    );

    CREATE TABLE IF NOT EXISTS synonyms (
        canonical TEXT NOT NULL,
        synonym   TEXT NOT NULL,
        PRIMARY KEY (canonical, synonym)
    );

    CREATE VIRTUAL TABLE IF NOT EXISTS section_fts
        USING fts5(section_title, content, content=sections, content_rowid=id);
    """)
    conn.commit()


def seed_regulations(conn: sqlite3.Connection):
    """마취료 소아/노인 가산 규정 초기 입력"""
    existing = conn.execute("SELECT COUNT(*) FROM regulations WHERE category='마취료'").fetchone()[0]
    if existing > 0:
        print("  규정 초기 데이터 이미 존재, 스킵")
        return

    rules = [
        ("마취료", "신생아가산", "신생아",
         "신생아 마취시에는 마취료 소정점수의 100%를 가산한다. (산정코드 첫 번째 자리: 1)",
         100.0, "1", "일반", "2025"),
        ("마취료", "신생아가산", "신생아",
         "상급종합병원·종합병원은 신생아 마취시 120%를 가산한다. (산정코드 첫 번째 자리: 1)",
         120.0, "1", "상급종합", "2025"),
        ("마취료", "소아가산", "1세미만",
         "1세 미만의 소아의 경우에는 마취료 소정점수의 50%를 가산한다. (산정코드 첫 번째 자리: A)",
         50.0, "A", "일반", "2025"),
        ("마취료", "소아가산", "1세미만",
         "상급종합병원·종합병원은 1세 미만의 소아의 경우 100%를 가산한다. (산정코드 첫 번째 자리: A)",
         100.0, "A", "상급종합", "2025"),
        ("마취료", "소아가산", "1세이상_6세미만",
         "1세 이상 6세 미만의 소아의 경우에는 마취료 소정점수의 30%를 가산한다. (산정코드 첫 번째 자리: B)",
         30.0, "B", "일반", "2025"),
        ("마취료", "소아가산", "1세이상_6세미만",
         "상급종합병원·종합병원은 1세 이상 6세 미만의 소아의 경우 50%를 가산한다. (산정코드 첫 번째 자리: B)",
         50.0, "B", "상급종합", "2025"),
        ("마취료", "노인가산", "70세이상",
         "70세 이상의 노인의 경우에는 마취료 소정점수의 30%를 가산한다. (산정코드 첫 번째 자리: 4)",
         30.0, "4", "일반", "2025"),
    ]

    conn.executemany("""
        INSERT INTO regulations
            (category, subcategory, condition, rule_text, rate, code_prefix, hosp_type, eff_year)
        VALUES (?,?,?,?,?,?,?,?)
    """, rules)
    conn.commit()
    print(f"  규정 {len(rules)}건 초기 입력 완료")


SYNONYM_SEED = [
    ("마취료", "마취"), ("마취료", "전신마취"), ("마취료", "마취비"), ("마취", "전신마취"),
    ("입원료", "입원"), ("입원료", "입원진료"), ("입원료", "입원비"),
    ("외래", "외래진료"), ("외래", "통원"), ("외래", "외래비"),
    ("수술", "수술료"), ("수술", "수술비"),
    ("처치", "처치료"), ("처치", "시술"),
    ("MRI", "자기공명영상"), ("MRI", "자기공명촬영"),
    ("CT", "전산화단층촬영"), ("CT", "컴퓨터단층촬영"),
    ("초음파", "초음파검사"), ("초음파", "초음파촬영"),
    ("수가", "요양급여비용"), ("수가", "급여비용"), ("수가", "요양급여"),
    ("가산", "가산점수"), ("가산", "가산율"), ("가산", "추가산정"),
    ("진찰", "진찰료"), ("진찰", "초진"), ("진찰", "재진"),
    ("검사", "검사료"), ("검사", "진단검사"),
    ("약제", "약제비"), ("약제", "투약"), ("약제", "조제"),
]


def seed_synonyms(conn: sqlite3.Connection):
    existing = conn.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0]
    if existing > 0:
        print(f"  동의어 이미 {existing}건, 스킵")
        return
    conn.executemany("INSERT OR IGNORE INTO synonyms (canonical, synonym) VALUES (?,?)", SYNONYM_SEED)
    conn.commit()
    print(f"  동의어 {len(SYNONYM_SEED)}건 입력 완료")


def mark_latest(conn: sqlite3.Connection):
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


def rebuild_fts(conn: sqlite3.Connection):
    try:
        conn.execute("INSERT INTO section_fts(section_fts) VALUES('rebuild')")
        conn.commit()
        print("FTS5 인덱스 재빌드 완료")
    except Exception as e:
        print(f"FTS5 재빌드 실패: {e}")


def main():
    pdf_files = list(PDF_DIR.glob("*.pdf")) + list(PDF_DIR.glob("**/*.pdf"))
    if not pdf_files:
        print(f"PDF 파일 없음: {PDF_DIR}")
        sys.exit(1)

    print(f"총 {len(pdf_files)}개 PDF 발견\n")

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    seed_regulations(conn)
    seed_synonyms(conn)

    already = {row[0] for row in conn.execute("SELECT filename FROM documents")}

    for i, pdf_path in enumerate(pdf_files, 1):
        if pdf_path.name in already:
            print(f"[{i}/{len(pdf_files)}] 스킵: {pdf_path.name}")
            continue

        print(f"[{i}/{len(pdf_files)}] {pdf_path.name}")
        head = extract_head(pdf_path)
        doc_type = detect_type(head)
        year = detect_year(head) or detect_year(pdf_path.name)
        print(f"  유형: {doc_type} | 연도: {year or '미확인'}")

        content = extract_full(pdf_path)
        cur = conn.execute(
            "INSERT INTO documents (filename, doc_type, year, content) VALUES (?,?,?,?)",
            (pdf_path.name, doc_type, year, content)
        )
        doc_id = cur.lastrowid

        sections = split_sections(content)
        print(f"  섹션 {len(sections)}개 분리")
        for title, sec_content in sections:
            conn.execute(
                "INSERT INTO sections (doc_id, section_title, content) VALUES (?,?,?)",
                (doc_id, title, sec_content)
            )
        conn.commit()

    rebuild_fts(conn)
    mark_latest(conn)

    print("\n=== 처리 결과 ===")
    for row in conn.execute(
        "SELECT doc_type, year, is_latest, filename FROM documents ORDER BY doc_type, year"
    ):
        latest = " ★최신" if row[2] else ""
        print(f"  [{row[0]}] {row[1] or '연도미상'}{latest} — {row[3]}")

    reg_count = conn.execute("SELECT COUNT(*) FROM regulations").fetchone()[0]
    sec_count = conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    print(f"\n섹션: {sec_count}개 | 규정: {reg_count}개")
    conn.close()
    print(f"\nDB 저장 완료: {DB_PATH}")


if __name__ == "__main__":
    main()
