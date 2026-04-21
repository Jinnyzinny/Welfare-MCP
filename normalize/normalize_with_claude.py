"""
welfare_service 테이블의 지원 대상·선정 기준 텍스트를
Anthropic Message Batches API 로 정규화하여
welfare_target / welfare_criteria 테이블에 저장합니다.

실행:
    python normalize_with_claude.py            # 미처리 전체
    python normalize_with_claude.py --force    # 이미 처리된 것도 재처리
"""

import argparse
import json
import os
import sys
import time

import anthropic
import psycopg
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from dotenv import load_dotenv

load_dotenv()

# ── 설정 ──────────────────────────────────────────────────────────────────────
MODEL            = "claude-opus-4-6"   # 비용 절감 시 "claude-haiku-4-5" 로 교체 가능
BATCH_CHUNK_SIZE = 10_000              # 배치당 최대 요청 수 (API 상한: 100,000)
POLL_INTERVAL    = 60                  # 완료 폴링 주기 (초)

# ── 시스템 프롬프트 (요청 전체에 공유 → cache_control 로 캐싱) ──────────────
SYSTEM_PROMPT = """당신은 한국 복지 서비스 데이터를 구조화하는 전문가입니다.
복지 서비스의 지원 대상·선정 기준 텍스트를 분석해 아래 JSON 스키마로 추출하세요.
설명 없이 JSON 만 반환하세요. 확실하지 않은 필드는 기본값을 사용하세요.

출력 스키마:
{
  "target": {
    "min_age": <int, 최소 나이, 기본값 0>,
    "max_age": <int, 최대 나이, 기본값 200>,
    "gender": <"A"|"M"|"F">,
    "sido": <string|null>,
    "sigungu": <string|null>,
    "household_types": <string[]>,
    "employment_statuses": <string[]>,
    "special_conditions": <string[]>
  },
  "criteria": {
    "income_min_pct": <int, 중위소득 하한 %, 기본값 0>,
    "income_max_pct": <int, 중위소득 상한 %, 기본값 999>,
    "asset_limit_krw": <int|null, 재산 상한 만원 단위>,
    "other_conditions": <string[]>
  }
}

규칙:
- gender: 여성/여자 → "F", 남성/남자 → "M", 명시 없음/전체 → "A"
- sido/sigungu: 전국·보건복지부·중앙정부 등 특정 지역 없으면 null
  소관기관명이 시/도·시/군/구 단위 지자체이면 해당 지역으로 설정
- household_types 허용값:
    다자녀 한부모 조손 1인가구 신혼부부 다문화가족 임산부 저소득
- employment_statuses 허용값:
    EMPLOYED UNEMPLOYED SELF_EMPLOYED STUDENT FARMER
- special_conditions 허용값:
    장애인 국가유공자 북한이탈주민 결혼이민자 노숙인 기초생활수급자 차상위계층 위기가구
- other_conditions 허용값:
    무주택 차량미보유 건강보험료기준 금융재산기준
- income_max_pct: "중위소득 50% 이하" → 50 / "기준 중위소득 120%" → 120
- asset_limit_krw (만원 단위): "3억" → 30000 / "5천만원" → 5000 / "3,000만원" → 3000
- 정보가 없거나 불분명하면 기본값 사용, 허용값 외의 태그는 사용 금지"""


# ── DB 연결 ────────────────────────────────────────────────────────────────────

def get_conninfo() -> str:

    
    return (
        f"host={os.getenv('DB_HOST')} "
        f"port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} "
        f"user={os.getenv('DB_USERNAME')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )


# ── DB 조회 ────────────────────────────────────────────────────────────────────

def fetch_services(conn, force: bool) -> list[tuple]:
    """
    정규화 대상 레코드 조회.
    force=False  → welfare_target 에 없는 것만 (미처리)
    force=True   → welfare_service 전체 (재처리)
    """
    if force:
        sql = """
            SELECT service_id, service_name, provider_name,
                   support_target, selection_criteria
            FROM welfare_service
            ORDER BY service_id
        """
    else:
        sql = """
            SELECT ws.service_id, ws.service_name, ws.provider_name,
                   ws.support_target, ws.selection_criteria
            FROM welfare_service ws
            LEFT JOIN welfare_target wt ON wt.service_id = ws.service_id
            WHERE wt.service_id IS NULL
            ORDER BY ws.service_id
        """
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


# ── Batch 요청 생성 ────────────────────────────────────────────────────────────

def build_requests(rows: list[tuple]) -> list[Request]:
    """DB 레코드 → Anthropic Batch Request 목록"""
    requests = []
    for service_id, service_name, provider_name, support_target, selection_criteria in rows:
        user_content = (
            f"서비스명: {service_name or '알 수 없음'}\n"
            f"소관기관: {provider_name or '알 수 없음'}\n\n"
            f"[지원 대상]\n{support_target or '정보 없음'}\n\n"
            f"[선정 기준]\n{selection_criteria or '정보 없음'}"
        )
        requests.append(
            Request(
                custom_id=service_id,
                params=MessageCreateParamsNonStreaming(
                    model=MODEL,
                    max_tokens=512,
                    system=[
                        {
                            "type": "text",
                            "text": SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},  # 시스템 프롬프트 캐싱
                        }
                    ],
                    messages=[{"role": "user", "content": user_content}],
                ),
            )
        )
    return requests


# ── 결과 파싱 ──────────────────────────────────────────────────────────────────

def parse_claude_response(text: str) -> dict | None:
    """Claude 응답 텍스트에서 JSON 추출"""
    text = text.strip()
    # 마크다운 코드 펜스 처리
    if "```" in text:
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start >= 0 and end > start:
            text = text[start:end]
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def safe_int(val, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ── DB 저장 ────────────────────────────────────────────────────────────────────

def upsert_results(conn, results: list[dict]) -> None:
    """파싱된 결과를 welfare_target / welfare_criteria 에 UPSERT"""
    with conn.cursor() as cur:
        for r in results:
            sid = r["service_id"]
            t   = r.get("target",   {})
            c   = r.get("criteria", {})

            cur.execute("""
                INSERT INTO welfare_target (
                    service_id, min_age, max_age, gender,
                    sido, sigungu,
                    household_types, employment_statuses, special_conditions
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (service_id) DO UPDATE SET
                    min_age              = EXCLUDED.min_age,
                    max_age              = EXCLUDED.max_age,
                    gender               = EXCLUDED.gender,
                    sido                 = EXCLUDED.sido,
                    sigungu              = EXCLUDED.sigungu,
                    household_types      = EXCLUDED.household_types,
                    employment_statuses  = EXCLUDED.employment_statuses,
                    special_conditions   = EXCLUDED.special_conditions
            """, (
                sid,
                safe_int(t.get("min_age"), 0),
                safe_int(t.get("max_age"), 200),
                t.get("gender", "A") or "A",
                t.get("sido") or None,
                t.get("sigungu") or None,
                t.get("household_types")    or [],
                t.get("employment_statuses") or [],
                t.get("special_conditions") or [],
            ))

            cur.execute("""
                INSERT INTO welfare_criteria (
                    service_id,
                    income_min_pct, income_max_pct,
                    asset_limit_krw, other_conditions
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (service_id) DO UPDATE SET
                    income_min_pct   = EXCLUDED.income_min_pct,
                    income_max_pct   = EXCLUDED.income_max_pct,
                    asset_limit_krw  = EXCLUDED.asset_limit_krw,
                    other_conditions = EXCLUDED.other_conditions
            """, (
                sid,
                safe_int(c.get("income_min_pct"), 0),
                safe_int(c.get("income_max_pct"), 999),
                c.get("asset_limit_krw") or None,
                c.get("other_conditions") or [],
            ))

    conn.commit()


# ── 배치 제출 & 폴링 ───────────────────────────────────────────────────────────

def submit_and_wait(client: anthropic.Anthropic, requests: list[Request]) -> anthropic.types.Batch:
    """배치 제출 후 완료까지 폴링"""
    batch = client.messages.batches.create(requests=requests)
    print(f"  Batch ID: {batch.id} | 상태: {batch.processing_status}")

    while batch.processing_status != "ended":
        time.sleep(POLL_INTERVAL)
        batch = client.messages.batches.retrieve(batch.id)
        rc = batch.request_counts
        total = rc.processing + rc.succeeded + rc.errored + rc.canceled + rc.expired
        print(f"  진행 중… 성공: {rc.succeeded} / 오류: {rc.errored} / 전체: {total}")

    return batch


# ── 메인 ──────────────────────────────────────────────────────────────────────

def run(force: bool = False) -> None:
    client = anthropic.Anthropic()

    print("DB 연결 중…")
    with psycopg.connect(get_conninfo()) as conn:
        print(f"정규화 대상 조회 중 (force={force})…")
        rows = fetch_services(conn, force=force)

        if not rows:
            print("처리할 레코드가 없습니다.")
            return

        print(f"총 {len(rows):,}건 처리 예정 (모델: {MODEL})")

        total_saved   = 0
        total_failed  = 0

        # BATCH_CHUNK_SIZE 단위로 분할 처리
        for chunk_idx, chunk_start in enumerate(range(0, len(rows), BATCH_CHUNK_SIZE), start=1):
            chunk     = rows[chunk_start : chunk_start + BATCH_CHUNK_SIZE]
            chunk_end = chunk_start + len(chunk)
            print(f"\n[청크 {chunk_idx}] {chunk_start + 1}~{chunk_end}건 → Batch 제출")

            requests = build_requests(chunk)
            batch    = submit_and_wait(client, requests)

            rc = batch.request_counts
            print(f"  완료 — 성공: {rc.succeeded} / 오류: {rc.errored}")

            # 결과 파싱
            parsed  = []
            failed  = 0

            for result in client.messages.batches.results(batch.id):
                if result.result.type == "succeeded":
                    text = next(
                        (b.text for b in result.result.message.content if b.type == "text"),
                        "",
                    )
                    data = parse_claude_response(text)
                    if data:
                        parsed.append({
                            "service_id": result.custom_id,
                            "target":     data.get("target",   {}),
                            "criteria":   data.get("criteria", {}),
                        })
                    else:
                        print(f"  [경고] JSON 파싱 실패 ({result.custom_id}): {text[:80]}")
                        failed += 1
                else:
                    error_type = getattr(result.result, "error", result.result.type)
                    print(f"  [오류] {result.custom_id}: {error_type}")
                    failed += 1

            # DB 저장
            if parsed:
                print(f"  DB 저장 중… ({len(parsed):,}건)")
                upsert_results(conn, parsed)

            total_saved  += len(parsed)
            total_failed += failed

        print(f"\n정규화 완료 — 저장: {total_saved:,}건 / 실패: {total_failed:,}건")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Claude Batch 정규화 실행기")
    parser.add_argument(
        "--force",
        action="store_true",
        help="이미 처리된 레코드도 재정규화",
    )
    args = parser.parse_args()
    run(force=args.force)
