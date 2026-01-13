import os
import json
import requests
import psycopg2
from tenacity import retry, stop_after_attempt, wait_fixed


# =========================
# 환경 변수
# =========================
JOB_NAME = os.environ["JOB_NAME"]
API_KEY = os.environ["WELFARE_API_KEY"]

PG_CONN = psycopg2.connect(
    host=os.environ["PGHOST"],
    port=os.environ["PGPORT"],
    dbname=os.environ["PGDATABASE"],
    user=os.environ["PGUSER"],
    password=os.environ["PGPASSWORD"],
)
PG_CONN.autocommit = False
cur = PG_CONN.cursor()


# =========================
# OpenAPI → DB 컬럼 매핑
# =========================
FIELD_MAPPING = {
    "서비스ID": "service_id",
    "서비스명": "service_name",
    "서비스목적": "service_purpose",
    "지원유형": "support_type",

    "소관기관명": "provider_name",      # NOT NULL
    "접수기관명": "apply_org_name",
    "문의처": "contact_info",

    "신청기한": "apply_period",
    "신청방법": "apply_method",
    "온라인신청사이트URL": "apply_url",

    "법령": "law_basis",
    "행정규칙": "admin_rule",
    "자치법규": "local_rule",
}


def map_openapi_item(item: dict) -> dict:
    row = {}

    for kr, en in FIELD_MAPPING.items():
        row[en] = item.get(kr)

    # NOT NULL 보정
    row["provider_name"] = row.get("provider_name") or "UNKNOWN"

    # payload는 원본 그대로
    row["payload"] = json.dumps(item, ensure_ascii=False)

    return row


# =========================
# API 호출
# =========================
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_page(page: int):
    url = "https://api.odcloud.kr/api/gov24/v3/serviceList"
    params = {
        "serviceKey": API_KEY,
        "page": page,
        "perPage": 1000,
    }
    res = requests.get(url, params=params, timeout=10)
    res.raise_for_status()
    return res.json()


# =========================
# 배치 시작
# =========================
try:
    # 1️⃣ 중복 실행 방지
    cur.execute("select pg_try_advisory_lock(hashtext(%s))", (JOB_NAME,))
    if not cur.fetchone()[0]:
        print("Another batch is running. Exit.")
        exit(0)

    # 2️⃣ 마지막 실패 checkpoint 조회
    cur.execute("""
        select checkpoint
        from batch_run
        where job_name = %s and status = 'FAILED'
        order by started_at desc
        limit 1
    """, (JOB_NAME,))
    row = cur.fetchone()
    checkpoint = json.loads(row[0]) if row and row[0] else {"page": 1}

    # 3️⃣ batch_run 시작 기록
    cur.execute("""
        insert into batch_run(job_name, checkpoint)
        values (%s, %s)
        returning id
    """, (JOB_NAME, json.dumps(checkpoint)))
    batch_id = cur.fetchone()[0]
    PG_CONN.commit()

    # =========================
    # 메인 루프
    # =========================
    page = checkpoint.get("page", 1)
    total_count = None
    per_page = 1000

    while True:
        result = fetch_page(page)

        items = result.get("data", [])
        if not items:
            break

        # 첫 페이지에서만 totalCount 확보
        if total_count is None:
            total_count = result.get("totalCount", 0)

        for item in items:
            row = map_openapi_item(item)

            cur.execute("""
                insert into welfare_service (
                    service_id,
                    service_name,
                    provider_name,
                    service_purpose,
                    support_type,
                    apply_org_name,
                    contact_info,
                    apply_period,
                    apply_method,
                    apply_url,
                    law_basis,
                    admin_rule,
                    local_rule,
                    payload
                )
                values (
                    %(service_id)s,
                    %(service_name)s,
                    %(provider_name)s,
                    %(service_purpose)s,
                    %(support_type)s,
                    %(apply_org_name)s,
                    %(contact_info)s,
                    %(apply_period)s,
                    %(apply_method)s,
                    %(apply_url)s,
                    %(law_basis)s,
                    %(admin_rule)s,
                    %(local_rule)s,
                    %(payload)s::jsonb
                )
                on conflict (service_id)
                do update set
                    service_name = excluded.service_name,
                    provider_name = excluded.provider_name,
                    payload = excluded.payload,
                    updated_at = now()
            """, row)

        # checkpoint 갱신
        page += 1
        cur.execute("""
            update batch_run
            set checkpoint = %s
            where id = %s
        """, (json.dumps({"page": page}), batch_id))

        PG_CONN.commit()

        # ✅ 종료 조건
        if (page - 1) * per_page >= total_count:
            break

    # 성공 처리
    cur.execute("""
        update batch_run
        set status='SUCCESS', finished_at=now()
        where id=%s
    """, (batch_id,))
    PG_CONN.commit()

except Exception as e:
    PG_CONN.rollback()
    cur.execute("""
        update batch_run
        set status='FAILED', error=%s
        where id=%s
    """, (str(e), batch_id))
    PG_CONN.commit()
    raise

finally:
    cur.execute("select pg_advisory_unlock(hashtext(%s))", (JOB_NAME,))
    PG_CONN.commit()
    cur.close()
    PG_CONN.close()
