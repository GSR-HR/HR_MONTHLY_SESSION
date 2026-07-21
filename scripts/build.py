#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notion DB -> Gemini 요약 -> docs/data.json

환경변수
  NOTION_TOKEN        내부 연결(Internal connection) 액세스 토큰
  NOTION_DATABASE_ID  대상 데이터베이스 ID
  GEMINI_API_KEY      Google AI Studio API 키
"""

import json
import os
import pathlib
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from google import genai
from google.genai import types

# ── 설정 ────────────────────────────────────────────────────────────
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

NOTION_VERSION = "2022-06-28"
GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_FALLBACK = "gemini-3.1-flash-lite"  # 503이 계속되면 이쪽으로 넘어감

# 프롬프트를 고칠 때마다 이 숫자를 1씩 올리면 캐시를 무시하고 전부 새로 만든다
PROMPT_VERSION = "3"

# 공개 Pages 대응: 담당자 이름을 이니셜로 마스킹
MASK_PEOPLE = os.environ.get("MASK_PEOPLE", "1") == "1"

# 노션 속성명 ↔ 대시보드 칼럼 매핑 (노션에서 속성명 바꾸면 여기만 수정)
P_TITLE = "작업 이름"
P_DESC = "설명"
P_PRIORITY = "우선순위"
P_BU = "상태"          # status 속성에 BU명이 들어있음
P_CATEGORY = "작업 유형"  # 목업의 '구분' 칼럼
P_DUE = "마감일"
P_EFFORT = "노력 수준"
P_ASSIGNEE = "담당자"

PRIORITY_ORDER = {"높음": 0, "보통": 1, "낮음": 2}

KST = ZoneInfo("Asia/Seoul")
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "docs" / "data.json"

NOTION_BASE = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


# ── Notion 읽기 ─────────────────────────────────────────────────────
def notion_post(path, payload):
    for attempt in range(4):
        r = requests.post(f"{NOTION_BASE}{path}", headers=HEADERS, json=payload, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 2)))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Notion {r.status_code} {path}: {r.text[:400]}")
        return r.json()
    raise RuntimeError(f"Notion rate limit 초과: {path}")


def notion_get(path, params=None):
    for attempt in range(4):
        r = requests.get(f"{NOTION_BASE}{path}", headers=HEADERS, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 2)))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Notion {r.status_code} {path}: {r.text[:400]}")
        return r.json()
    raise RuntimeError(f"Notion rate limit 초과: {path}")


def query_database(db_id):
    """DB의 모든 행을 페이지네이션으로 수집"""
    rows, cursor = [], None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        data = notion_post(f"/databases/{db_id}/query", payload)
        rows.extend(data["results"])
        if not data.get("has_more"):
            return rows
        cursor = data["next_cursor"]


def rich_text_to_plain(rt):
    return "".join(x.get("plain_text", "") for x in rt or []).strip()


# 본문 블록에서 텍스트를 뽑을 때 접두어를 붙여 구조를 살림
BLOCK_PREFIX = {
    "bulleted_list_item": "- ",
    "numbered_list_item": "- ",
    "to_do": "- ",
    "quote": "> ",
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
}


def fetch_block_text(block_id, depth=0, max_depth=3):
    """페이지 안쪽 본문 블록을 재귀적으로 읽어 평문으로 변환"""
    if depth > max_depth:
        return []

    lines, cursor = [], None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        data = notion_get(f"/blocks/{block_id}/children", params)

        for b in data["results"]:
            btype = b["type"]
            if btype in ("child_page", "child_database", "unsupported"):
                continue

            body = b.get(btype, {})
            text = rich_text_to_plain(body.get("rich_text"))

            if btype == "to_do":
                mark = "[x] " if body.get("checked") else "[ ] "
                text = mark + text
            elif btype == "code":
                text = f"```{body.get('language', '')}\n{text}\n```"

            if text:
                lines.append(("  " * depth) + BLOCK_PREFIX.get(btype, "") + text)

            if b.get("has_children"):
                lines.extend(fetch_block_text(b["id"], depth + 1, max_depth))

        if not data.get("has_more"):
            break
        cursor = data["next_cursor"]

    return lines


def mask_name(name):
    if not name:
        return ""
    if not MASK_PEOPLE:
        return name
    if len(name) <= 1:
        return name
    return name[0] + "*" * (len(name) - 1)


def parse_row(page):
    props = page["properties"]

    def prop(name):
        return props.get(name, {})

    title = rich_text_to_plain(prop(P_TITLE).get("title"))
    desc = rich_text_to_plain(prop(P_DESC).get("rich_text"))

    sel = prop(P_PRIORITY).get("select") or {}
    priority = sel.get("name", "")

    st = prop(P_BU).get("status") or {}
    bu = st.get("name", "미분류")

    category = " · ".join(x["name"] for x in prop(P_CATEGORY).get("multi_select") or [])

    d = prop(P_DUE).get("date") or {}
    due = d.get("start") or ""

    eff = prop(P_EFFORT).get("select") or {}
    effort = eff.get("name", "")

    assignees = [mask_name(u.get("name", "")) for u in prop(P_ASSIGNEE).get("people") or []]

    return {
        "id": page["id"],
        "url": page.get("url", ""),
        "icon": (page.get("icon") or {}).get("emoji", ""),
        "title": title,
        "description": desc,
        "priority": priority,
        "bu": bu,
        "category": category or "미분류",
        "due": due,
        "effort": effort,
        "assignees": assignees,
        "last_edited_time": page["last_edited_time"],
    }


# ── Gemini 요약 ─────────────────────────────────────────────────────
client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(timeout=45_000),  # 45초 넘으면 끊고 재시도
)

# 503(과부하) / 429(쿼터) / 500은 기다리면 풀린다. 나머지는 재시도해도 소용없다.
RETRYABLE = ("503", "429", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "Timeout", "Deadline")
BACKOFF = [2, 6, 15, 30]


def _call(model, prompt, schema):
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            # 생략하면 기본값이 high라 요약 작업에도 과하게 오래 걸린다
            thinking_config=types.ThinkingConfig(thinking_level="low"),
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    return json.loads(resp.text)


def gemini_json(prompt, schema, label=""):
    """503/429는 지수 백오프로 재시도하고, 끝내 안 되면 폴백 모델로 넘어간다."""
    last_err = None

    for model in (GEMINI_MODEL, GEMINI_FALLBACK):
        for attempt, wait in enumerate(BACKOFF, 1):
            t0 = time.time()
            try:
                out = _call(model, prompt, schema)
                print(f"    gemini {label} {model} {time.time() - t0:.1f}s", flush=True)
                return out
            except Exception as e:
                last_err = e
                msg = str(e)
                took = time.time() - t0

                if not any(k in msg for k in RETRYABLE):
                    print(f"    ! 재시도 불가 오류: {msg[:160]}", file=sys.stderr, flush=True)
                    return None

                if attempt == len(BACKOFF):
                    print(f"    {model} 포기 → 다음 모델", file=sys.stderr, flush=True)
                    break

                print(f"    {model} 재시도 {attempt} ({took:.0f}s) → {wait}s 대기",
                      file=sys.stderr, flush=True)
                time.sleep(wait)

    print(f"  ! Gemini 실패: {str(last_err)[:200]}", file=sys.stderr, flush=True)
    return None


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"bullets": {"type": "array", "items": {"type": "string"}}},
    "required": ["bullets"],
}

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {"briefing": {"type": "string"}},
    "required": ["briefing"],
}


def summarize_task(row, body_text):
    source = f"[제목] {row['title']}\n[설명] {row['description']}\n[본문]\n{body_text or '(본문 없음)'}"
    prompt = f"""당신은 리테일 기업 인사팀의 업무 대시보드를 작성합니다.
아래 업무 항목을 읽고 담당자가 30초 안에 파악할 수 있도록 요약하세요.

규칙
- 정확히 3개의 글머리표로 작성
- 각 항목은 한국어 한 문장, 40자 이내
- 문장 끝에 마침표 없음
- "~합니다" 대신 "~함", "~필요" 같은 개조식 어미 사용
- 원문에 없는 내용을 지어내지 말 것
- 설명이 제목과 명백히 무관하면 설명을 무시하고 제목과 본문만 근거로 삼을 것
- 정보가 부족하면 제목과 설명에서 추론 가능한 범위까지만 작성

{source}"""
    out = gemini_json(prompt, SUMMARY_SCHEMA, label="요약")
    if not out:
        return []
    bullets = [re.sub(r"^[-•·]\s*", "", b).strip() for b in out.get("bullets", [])]
    return [b for b in bullets if b][:3]


def make_briefing(bu_label, items):
    if not items:
        return f"{bu_label}에 등록된 안건이 없습니다."

    listed = "\n".join(
        f"- [{i['priority'] or '미지정'}] {i['title']} (구분 {i['category']}, 마감 {i['due'] or '미정'})"
        f"\n  설명: {i['description'] or '없음'}"
        for i in items
    )
    counts = {}
    for i in items:
        counts[i["priority"] or "미지정"] = counts.get(i["priority"] or "미지정", 0) + 1
    dist = ", ".join(f"{k} {v}건" for k, v in counts.items())

    prompt = f"""당신은 리테일 기업 인사팀의 업무 현황판에 들어갈 한 줄 브리핑을 작성합니다.
아래는 '{bu_label}'에 등록된 안건 전체입니다. (총 {len(items)}건 / {dist})

{listed}

규칙
- 1~2문장, 총 120자 이내로 짧게 작성
- 안건이 무엇에 관한 것인지 큰 덩어리로 묶어 말할 것
- 우선순위가 높거나 마감이 가장 이른 안건 하나만 제목 그대로 짚을 것
- 안건 제목을 임의로 줄이거나 다른 말로 바꾸지 말 것
- 설명이 제목과 명백히 무관하면 설명을 무시하고 제목만 근거로 삼을 것
- 사실을 전달하는 설명체로만 작성 ("~입니다", "~있습니다")
- 지시하거나 요청하지 말 것 ("~바랍니다", "~부탁드립니다", "~해야 합니다", "~검토하여" 금지)
- 원문에 없는 배경, 이유, 안건 간의 연관성을 지어내지 말 것"""
    out = gemini_json(prompt, BRIEF_SCHEMA, label=f"브리핑/{bu_label}")
    return (out or {}).get("briefing", "").strip() or "브리핑 생성에 실패했습니다."


# ── 실행 ────────────────────────────────────────────────────────────
def load_cache():
    """직전 결과를 읽어 변경되지 않은 항목의 요약을 재사용 (Gemini 호출 절약)"""
    if not OUT_PATH.exists():
        return {}, {}
    try:
        prev = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}, {}
    if prev.get("_prompt_version") != PROMPT_VERSION:
        print("프롬프트가 바뀌어 캐시를 버리고 전부 새로 생성합니다", flush=True)
        return {}, {}

    summaries = {
        i["id"]: (i.get("last_edited_time"), i.get("summary", []))
        for i in prev.get("items", [])
    }
    return summaries, prev.get("_brief_keys", {})


def main():
    now = datetime.now(KST)
    print(f"기준일자 {now:%Y-%m-%d %H:%M} KST", flush=True)

    pages = query_database(DATABASE_ID)
    rows = [parse_row(p) for p in pages if not p.get("in_trash")]
    print(f"노션 {len(rows)}건 수신", flush=True)

    cached_summaries, cached_brief_keys = load_cache()

    # 1) 항목별 3줄 요약
    for idx, row in enumerate(rows, 1):
        cached = cached_summaries.get(row["id"])
        if cached and cached[0] == row["last_edited_time"] and cached[1]:
            row["summary"] = cached[1]
            continue

        print(f"  [{idx}/{len(rows)}] {row['title'][:24]}", flush=True)
        body = "\n".join(fetch_block_text(row["id"]))
        print(f"    본문 {len(body)}자 수집", flush=True)
        row["summary"] = summarize_task(row, body)
        time.sleep(0.3)  # 무료 티어 RPM 여유

    # 2) 정렬: 우선순위 → 마감일
    rows.sort(key=lambda r: (PRIORITY_ORDER.get(r["priority"], 9), r["due"] or "9999"))

    # 3) BU별 + 전체 브리핑
    bus = sorted({r["bu"] for r in rows})
    briefings, brief_keys = {}, {}

    for label in ["전체"] + bus:
        target = rows if label == "전체" else [r for r in rows if r["bu"] == label]
        key = "|".join(f"{r['id']}:{r['last_edited_time']}" for r in target)
        brief_keys[label] = key

        if cached_brief_keys.get(label) == key and key:
            briefings[label] = None  # 아래에서 직전 값 채움
        else:
            briefings[label] = make_briefing(label, target)
            time.sleep(0.3)

    # 캐시 적중분은 직전 파일에서 그대로 가져오기
    if any(v is None for v in briefings.values()) and OUT_PATH.exists():
        prev = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        for k, v in briefings.items():
            if v is None:
                briefings[k] = prev.get("briefings", {}).get(k, "")

    payload = {
        "generated_at": now.isoformat(),
        "base_date": now.strftime("%Y-%m-%d"),
        "bus": bus,
        "briefings": briefings,
        "_brief_keys": brief_keys,
        "_prompt_version": PROMPT_VERSION,
        "items": rows,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장 완료 → {OUT_PATH} ({len(rows)}건, BU {len(bus)}종)", flush=True)


if __name__ == "__main__":
    main()
