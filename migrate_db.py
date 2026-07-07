"""
기존 health_fee.db에 sections + regulations 테이블 추가 (마이그레이션)
PDF 없이 기존 DB content로 섹션 분리
실행: python migrate_db.py
"""
import sqlite3
import re
from pathlib import Path

DB_PATH = Path(__file__).parent / "health_fee.db"

SECTION_PATTERN = re.compile(r'(제\s*\d+\s*[장절관]\s*[^\n]{1,50})', re.MULTILINE)


def split_sections(full_text: str) -> list[tuple[str, str]]:
    if not full_text:
        return []
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


def migrate(conn: sqlite3.Connection):
    # 1. 테이블 추가
    conn.executescript("""
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
    print("테이블 생성 완료")

    # 2. 기존 documents content → sections 분리
    already_migrated = conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    if already_migrated > 0:
        print(f"sections 이미 {already_migrated}개 존재, 스킵")
    else:
        docs = conn.execute("SELECT id, filename, content FROM documents WHERE content IS NOT NULL").fetchall()
        total_sections = 0
        for doc in docs:
            sections = split_sections(doc["content"])
            for title, content in sections:
                conn.execute(
                    "INSERT INTO sections (doc_id, section_title, content) VALUES (?,?,?)",
                    (doc["id"], title, content)
                )
            total_sections += len(sections)
            print(f"  {doc['filename']}: 섹션 {len(sections)}개")
        conn.commit()
        print(f"섹션 분리 완료: 총 {total_sections}개")

    # 3. regulations 초기 데이터
    existing = conn.execute("SELECT COUNT(*) FROM regulations WHERE category='마취료'").fetchone()[0]
    if existing > 0:
        print(f"규정 이미 {existing}건 존재, 스킵")
    else:
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
        print(f"규정 {len(rules)}건 입력 완료")

    # 4. 동의어 시드
    existing_syn = conn.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0]
    if existing_syn > 0:
        print(f"동의어 이미 {existing_syn}건 존재, 스킵")
    else:
        conn.executemany("INSERT OR IGNORE INTO synonyms (canonical, synonym) VALUES (?,?)", SYNONYM_SEED)
        conn.commit()
        print(f"동의어 {len(SYNONYM_SEED)}건 입력 완료")

    # 5. FTS5 재빌드
    try:
        conn.execute("INSERT INTO section_fts(section_fts) VALUES('rebuild')")
        conn.commit()
        print("FTS5 인덱스 재빌드 완료")
    except Exception as e:
        print(f"FTS5 재빌드 실패: {e}")

    # 6. 결과 요약
    sec_count = conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    reg_count = conn.execute("SELECT COUNT(*) FROM regulations").fetchone()[0]
    syn_count = conn.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0]
    print(f"\n완료: sections={sec_count}개, regulations={reg_count}개, synonyms={syn_count}건")

    # 마취료 검색 테스트
    print("\n--- 마취료 검색 테스트 ---")
    rows = conn.execute(
        "SELECT section_title, substr(content, 1, 200) FROM sections WHERE content LIKE '%마취%소아%' LIMIT 3"
    ).fetchall()
    if rows:
        for r in rows:
            print(f"[{r[0]}] {r[1][:100]}...")
    else:
        rows2 = conn.execute(
            "SELECT section_title, substr(content, 1, 200) FROM sections WHERE content LIKE '%마취%' LIMIT 3"
        ).fetchall()
        if rows2:
            print("'마취'만 포함된 섹션:")
            for r in rows2:
                print(f"[{r[0]}] {r[1][:100]}...")
        else:
            print("마취 관련 섹션 없음 → PDF 텍스트 추출 문제 가능성")


if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"DB 파일 없음: {DB_PATH}")
        exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    conn.close()
