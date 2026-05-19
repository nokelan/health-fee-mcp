# health-fee-mcp

건강보험 요양급여비용(수가표) 검색 MCP 서버

건강보험심사평가원 공개 PDF 자료를 기반으로 수가표, 청구방법, 포괄수가 등을 검색할 수 있는 MCP 서버입니다.

---

## 바로 사용하기 (추천)

별도 설치 없이 공개 서버에 바로 연결할 수 있습니다.

`~/.claude/.mcp.json` 또는 프로젝트 `.mcp.json`에 추가:

```json
{
  "mcpServers": {
    "health-fee": {
      "type": "http",
      "url": "https://health.autotaxsystem.co.kr/mcp"
    }
  }
}
```

Claude Code 재시작 후 사용 가능합니다.

---

## 사용 가능한 도구

| 도구 | 설명 |
|---|---|
| `search` | 키워드로 문서 검색 (연도, 문서유형 필터 가능) |
| `latest` | 특정 문서유형의 최신판 조회 |
| `compare` | 두 연도 간 항목 비교 |
| `list` | 보유 문서 목록 조회 |

### 사용 예시

```
# 초음파 수가 검색
search(keyword="초음파", doc_type="fee_schedule")

# 2025년 최신 수가표 조회
latest(doc_type="fee_schedule")

# 2024 vs 2025 수가 비교
compare(keyword="MRI", year1=2024, year2=2025)
```

---

## 이런 질문을 할 수 있습니다

MCP 연결 후 Claude에게 자연어로 질문하면 됩니다.

**수가 검색**
- "초음파 검사 수가가 얼마야?"
- "MRI 촬영 요양급여비용 알려줘"
- "2026년 처치 및 수술료 수가표 찾아줘"

**청구 방법**
- "외래 진료비 청구서 어떻게 작성해?"
- "요양급여비용 청구방법 알려줘"
- "입원 명세서 작성 기준이 뭐야?"

**연도 비교**
- "2024년과 2025년 MRI 수가 비교해줘"
- "올해 수가표에서 바뀐 항목 있어?"

**포괄수가 (DRG)**
- "포괄수가제 대상 질병군이 뭐야?"
- "DRG 청구 방법 알려줘"

---

## 문서 유형 (doc_type)

| 값 | 설명 |
|---|---|
| `fee_schedule` | 요양급여비용 (수가표) |
| `billing_guide` | 청구방법/작성요령 |
| `billing_intro` | 청구 길라잡이 |
| `drg_guide` | 포괄수가제 안내 |

---

## DB에 포함된 자료

건강보험심사평가원이 공개한 PDF를 SQLite DB로 변환하여 제공합니다.

| 문서유형 | 연도 범위 | 설명 |
|---|---|---|
| 요양급여비용 (수가표) | 2024 ~ 2026 | 행위료, 약제비, 치료재료 수가 |
| 청구방법/작성요령 | 2024 ~ 2026 | 진료비 청구서·명세서 작성 기준 |
| 청구 길라잡이 | 2024 ~ 2026 | 유형별 청구 실무 안내 |
| 포괄수가제 안내 | 2024 ~ 2026 | DRG 포괄수가 적용 기준 |

총 18개 PDF 파일 기반, 전문 텍스트 추출 후 청크 단위 인덱싱.

> PDF 원본 파일은 저작권 문제로 포함되지 않습니다. DB 구축 방법은 아래 직접 설치 항목을 참고하세요.

---

## 직접 설치하기

PDF가 있는 경우 로컬 서버를 구축할 수 있습니다.

### 요구사항

- Python 3.10+
- 건강보험공단 공개 PDF 파일

### 설치

```bash
pip install -r requirements.txt
```

### DB 구축

```bash
# PDF가 있는 폴더 경로를 build_db.py 내 PDF_DIR에 설정 후 실행
python build_db.py
```

### 서버 실행

```bash
uvicorn server:app --host 0.0.0.0 --port 8400
```

---

## API 엔드포인트

| 경로 | 설명 |
|---|---|
| `GET /list` | 보유 문서 목록 |
| `GET /search?keyword=&year=&doc_type=&limit=` | 키워드 검색 |
| `GET /latest?doc_type=&keyword=` | 최신판 조회 |
| `GET /compare?keyword=&year1=&year2=&doc_type=` | 연도 비교 |
| `GET /mcp` | MCP SSE 엔드포인트 |

---

## 라이선스

MIT
