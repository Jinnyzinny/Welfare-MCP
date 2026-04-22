"""
welfare_service 테이블의 지원 대상·선정 기준 텍스트를
Anthropic Message Batches API 로 정규화하여
welfare_target / welfare_criteria 테이블에 저장합니다.

실행:
    python normalize_with_claude.py            # 미처리 전체
    python normalize_with_claude.py --force    # 이미 처리된 것도 재처리
"""

import argparse
import asyncio
import json
import time
import traceback

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from dotenv import load_dotenv

from DB_Connection import get_db_pool, close_db_pool

load_dotenv()

# ── 설정 ──────────────────────────────────────────────────────────────────────
MODEL            = "claude-haiku-4-5-20251001"
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


# ── DB 조회 ────────────────────────────────────────────────────────────────────
async def fetch_services(conn, force: bool) -> list[tuple]:
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
    print(f"  [DB] 쿼리 실행 중 (force={force})…")
    try:
        async with conn.cursor() as cur:
            await cur.execute(sql)
            rows = await cur.fetchall()
            print(f"  [DB] 조회 완료: {len(rows):,}건")
            return rows
    except Exception as e:
        print(f"  [DB ERROR] fetch_services 실패: {e}")
        print(traceback.format_exc())
        raise


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
                custom_id=str(service_id),
                params=MessageCreateParamsNonStreaming(
                    model=MODEL,
                    max_tokens=512,
                    system=[
                        {
                            "type": "text",
                            "text": SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user_content}],
                ),
            )
        )
    return requests

# ── 배치 제출 & 폴링 ───────────────────────────────────────────────────────────

def submit_and_wait(client: anthropic.Anthropic, requests: list[Request]):
    """배치 제출 후 완료까지 폴링"""
    print(f"  [API] Batch 제출 중 ({len(requests):,}건)…")
    try:
        batch = client.messages.batches.create(requests=requests)
    except anthropic.APIConnectionError as e:
        print(f"  [API ERROR] 배치 제출 실패 — 네트워크 연결 오류: {e}")
        raise
    except anthropic.AuthenticationError as e:
        print(f"  [API ERROR] 배치 제출 실패 — 인증 오류 (ANTHROPIC_API_KEY 확인): {e}")
        raise
    except anthropic.RateLimitError as e:
        print(f"  [API ERROR] 배치 제출 실패 — Rate limit 초과: {e}")
        raise
    except anthropic.APIStatusError as e:
        print(f"  [API ERROR] 배치 제출 실패 — HTTP {e.status_code}: {e.message}")
        raise

    print(f"  [API] Batch ID: {batch.id} | 초기 상태: {batch.processing_status}")

    poll_count = 0
    while batch.processing_status != "ended":
        poll_count += 1
        time.sleep(POLL_INTERVAL)
        try:
            batch = client.messages.batches.retrieve(batch.id)
        except anthropic.APIConnectionError as e:
            print(f"  [API ERROR] 폴링 #{poll_count} 실패 — 네트워크 오류: {e} (재시도 대기)")
            continue
        except anthropic.APIStatusError as e:
            print(f"  [API ERROR] 폴링 #{poll_count} 실패 — HTTP {e.status_code}: {e.message}")
            raise

        rc = batch.request_counts
        total = rc.processing + rc.succeeded + rc.errored + rc.canceled + rc.expired
        print(
            f"  [폴링 #{poll_count}] 상태: {batch.processing_status} | "
            f"성공: {rc.succeeded} / 오류: {rc.errored} / 처리중: {rc.processing} / 전체: {total}",
            flush=True,
        )

    rc = batch.request_counts
    print(
        f"  [API] Batch 완료 — 성공: {rc.succeeded} / 오류: {rc.errored} / "
        f"취소: {rc.canceled} / 만료: {rc.expired}"
    )
    return batch

# ── 결과 파싱 ──────────────────────────────────────────────────────────────────

def parse_claude_response(text: str) -> dict | None:
    """Claude 응답 텍스트에서 JSON 추출"""
    original = text
    text = text.strip()
    if "```" in text:
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start >= 0 and end > start:
            text = text[start:end]
        else:
            print(f"  [경고] 코드 펜스 있으나 JSON 중괄호 미발견: {original[:120]!r}")
            return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  [경고] JSON 디코딩 실패 ({e}): {text[:120]!r}")
        return None


def safe_int(val, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default

# ── DB 저장 ────────────────────────────────────────────────────────────────────

async def upsert_results(conn, results: list[dict]) -> None:
    """파싱된 결과를 welfare_target / welfare_criteria 에 UPSERT"""
    print(f"  [DB] UPSERT 시작 ({len(results):,}건)…")
    success_count = 0
    fail_count = 0

    async with conn.cursor() as cur:
        for r in results:
            sid = r["service_id"]
            t   = r.get("target",   {})
            c   = r.get("criteria", {})

            try:
                # SAVEPOINT로 개별 레코드 실패 시 트랜잭션 aborted 상태 방지
                await cur.execute("SAVEPOINT row_save")

                await cur.execute("""
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

                await cur.execute("""
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
                    safe_int(c.get("asset_limit_krw"), None) if c.get("asset_limit_krw") is not None else None,
                    c.get("other_conditions") or [],
                ))

                await cur.execute("RELEASE SAVEPOINT row_save")
                success_count += 1

            except Exception as e:
                print(f"  [DB ERROR] service_id={sid} UPSERT 실패: {e}")
                print(f"    target 데이터: {t}")
                print(f"    criteria 데이터: {c}")
                print(traceback.format_exc())
                try:
                    await cur.execute("ROLLBACK TO SAVEPOINT row_save")
                except Exception as rb_e:
                    print(f"  [DB ERROR] SAVEPOINT rollback 실패 (연결 불안정): {rb_e}")
                    raise
                fail_count += 1

    try:
        await conn.commit()
        print(f"  [DB] 커밋 완료 — 성공: {success_count:,}건 / 실패: {fail_count:,}건")
    except Exception as e:
        print(f"  [DB ERROR] 커밋 실패: {e}")
        print(traceback.format_exc())
        raise

# ── 메인 ──────────────────────────────────────────────────────────────────────

async def run(force: bool = False) -> None:
    print(f"[시작] force={force}, 모델={MODEL}")

    print("[API] Anthropic 클라이언트 초기화…")
    try:
        client = anthropic.Anthropic()
    except Exception as e:
        print(f"[API ERROR] Anthropic 클라이언트 초기화 실패: {e}")
        raise

    print("[DB] 커넥션 풀 초기화…")
    try:
        pool = await get_db_pool()
    except Exception as e:
        print(f"[DB ERROR] 커넥션 풀 초기화 실패: {e}")
        print(traceback.format_exc())
        raise

    try:
        async with pool.connection() as conn:
            print("[DB] 연결 성공")

            print(f"[DB] 정규화 대상 조회 중 (force={force})…")
            rows = await fetch_services(conn, force=force)

            if not rows:
                print("[완료] 처리할 레코드가 없습니다.")
                return

            print(f"[시작] 총 {len(rows):,}건 처리 예정")

            total_saved  = 0
            total_failed = 0

            for chunk_idx, chunk_start in enumerate(range(0, len(rows), BATCH_CHUNK_SIZE), start=1):
                chunk     = rows[chunk_start : chunk_start + BATCH_CHUNK_SIZE]
                chunk_end = chunk_start + len(chunk)
                print(f"\n[청크 {chunk_idx}] {chunk_start + 1}~{chunk_end}건 → Batch 제출")

                try:
                    requests = build_requests(chunk)
                    print(f"  [청크 {chunk_idx}] 요청 객체 생성 완료: {len(requests):,}건")
                except Exception as e:
                    print(f"  [ERROR] 청크 {chunk_idx} 요청 생성 실패: {e}")
                    print(traceback.format_exc())
                    total_failed += len(chunk)
                    continue

                try:
                    batch = submit_and_wait(client, requests)
                except Exception as e:
                    print(f"  [ERROR] 청크 {chunk_idx} Batch 처리 실패: {e}")
                    total_failed += len(chunk)
                    continue

                parsed = []
                failed = 0

                print(f"  [청크 {chunk_idx}] 결과 수집 중…")
                try:
                    for result in client.messages.batches.results(batch.id):
                        if result.result.type == "succeeded":
                            text = next(
                                (b.text for b in result.result.message.content if b.type == "text"),
                                "",
                            )
                            if not text:
                                print(f"  [경고] 빈 응답 텍스트: service_id={result.custom_id}")
                                failed += 1
                                continue

                            data = parse_claude_response(text)
                            if data:
                                parsed.append({
                                    "service_id": result.custom_id,
                                    "target":     data.get("target",   {}),
                                    "criteria":   data.get("criteria", {}),
                                })
                            else:
                                print(f"  [경고] JSON 파싱 실패 (service_id={result.custom_id}): {text[:120]!r}")
                                failed += 1
                        else:
                            error_detail = getattr(result.result, "error", None)
                            print(
                                f"  [API 오류] service_id={result.custom_id} | "
                                f"type={result.result.type} | detail={error_detail}"
                            )
                            failed += 1
                except anthropic.APIConnectionError as e:
                    print(f"  [API ERROR] 결과 수집 중 네트워크 오류: {e}")
                    total_failed += len(chunk)
                    continue
                except anthropic.APIStatusError as e:
                    print(f"  [API ERROR] 결과 수집 중 HTTP {e.status_code}: {e.message}")
                    total_failed += len(chunk)
                    continue
                except Exception as e:
                    print(f"  [ERROR] 결과 수집 중 예외: {e}")
                    print(traceback.format_exc())
                    total_failed += len(chunk)
                    continue

                print(f"  [청크 {chunk_idx}] 파싱 완료 — 성공: {len(parsed):,}건 / 실패: {failed:,}건")

                if parsed:
                    try:
                        await upsert_results(conn, parsed)
                    except Exception as e:
                        print(f"  [DB ERROR] 청크 {chunk_idx} DB 저장 실패: {e}")
                        print(traceback.format_exc())
                        total_failed += len(parsed)
                        continue
                else:
                    print(f"  [청크 {chunk_idx}] 저장할 데이터 없음 (파싱 성공 0건)")

                total_saved  += len(parsed)
                total_failed += failed

    except Exception as e:
        print(f"[FATAL ERROR] 예상치 못한 오류 발생: {e}")
        print(traceback.format_exc())
        raise
    finally:
        print("[DB] 커넥션 풀 종료 중…")
        await close_db_pool()
        print("[DB] 커넥션 풀 종료 완료")

    print(f"\n[완료] 저장: {total_saved:,}건 / 실패: {total_failed:,}건")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Claude Batch 정규화 실행기")
    parser.add_argument(
        "--force",
        action="store_true",
        help="이미 처리된 레코드도 재정규화",
    )
    args = parser.parse_args()
    asyncio.run(run(force=args.force))
