# 오늘의 브리핑 대시보드

Notion DB → GitHub Actions → Gemini 요약 → GitHub Pages.

```
.github/workflows/build.yml   평일 08:00 KST 자동 실행 + 수동 실행
scripts/build.py              노션 수집 · Gemini 요약 · data.json 생성
docs/                         GitHub Pages 루트 (index.html / style.css / app.js / data.json)
```

## 설치 순서

**1. 저장소에 파일 올리기**

**2. Secrets 등록** — Settings → Secrets and variables → Actions → New repository secret

| 이름 | 값 |
|---|---|
| `NOTION_TOKEN` | 내부 연결 액세스 토큰 |
| `NOTION_DATABASE_ID` | `3a42fec4-e4a1-80b5-879a-f9dcf969b3c0` |
| `GEMINI_API_KEY` | Google AI Studio 키 |

**3. Pages 설정** — Settings → Pages → Source: `Deploy from a branch` → Branch: `main` / `/docs`

**4. 첫 실행** — Actions 탭 → `오늘의 브리핑 갱신` → Run workflow

`https://<계정>.github.io/<저장소>/` 에서 확인.

## 로컬 확인

```bash
pip install -r requirements.txt

export NOTION_TOKEN=...
export NOTION_DATABASE_ID=3a42fec4-e4a1-80b5-879a-f9dcf969b3c0
export GEMINI_API_KEY=...
python scripts/build.py

cd docs && python -m http.server 8000   # http://localhost:8000
```

`docs/data.json`에는 목업 확인용 샘플이 들어 있어, 스크립트를 돌리지 않아도 화면을 먼저 볼 수 있습니다.

## 알아둘 것

- **요약 캐시** — `docs/data.json`의 `last_edited_time`을 비교해 변경된 행만 Gemini를 호출합니다. 노션에서 수정하지 않은 안건은 API를 다시 쓰지 않습니다.
- **속성명 의존** — 노션에서 속성 이름을 바꾸면 `scripts/build.py` 상단의 `P_*` 상수를 같이 고쳐야 합니다.
- **담당자 마스킹** — `MASK_PEOPLE=1`이면 이름을 `홍**` 형태로 저장합니다. 워크플로 env에서 `0`으로 끄면 실명이 공개 URL에 노출됩니다.
- **실행 주기 변경** — `build.yml`의 cron은 UTC 기준입니다. KST 08:00 = UTC 23:00 (전날).
