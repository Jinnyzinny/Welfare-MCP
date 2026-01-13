import os
import psycopg2
from psycopg2 import sql
from tenacity import retry, stop_after_attempt, wait_fixed
import json
import requests


JOB_NAME = os.environ["JOB_NAME"]
API_KEY = os.environ["OPENAPI_KEY"]

conn = psycopg2.connect(
    host=os.environ["PGHOST"],
    port=os.environ["PGPORT"],
    dbname=os.environ["PGDATABASE"],
    user=os.environ["PGUSER"],
    password=os.environ["PGPASSWORD"],
)

conn.autocommit = False
cur=conn.cursor()

cur.execute("select pg_try_advisory_lock(hashtext(%s))", (JOB_NAME,))
locked = cur.fetchone()[0]
if not locked:
    print("Another batch is running. Exit.")
    exit(0)

# 2️⃣ 이전 실행 checkpoint 조회
cur.execute("""
    select id, checkpoint
    from batch_run
    where job_name = %s and status = 'FAILED'
    order by started_at desc
    limit 1
""", (JOB_NAME,))
row = cur.fetchone()

checkpoint = row[1] if row else {"page": 1}


# 3️⃣ batch_run 시작 기록
cur.execute("""
    insert into batch_run(job_name, checkpoint)
    values (%s, %s)
    returning id
""", (JOB_NAME, json.dumps(checkpoint)))
batch_id = cur.fetchone()[0]
conn.commit()

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_page(page: int):
    url = "https://api.odcloud.kr/api/gov24/v3/serviceList"
    params = {
        "serviceKey": API_KEY,
        "page": page,
        "perPage":1000,
    }
    res = requests.get(url, params=params, timeout=10)
    res.raise_for_status()
    return res.json()

try:
    page = checkpoint.get("page", 1)

    while True:
        data = fetch_page(page)
        items = data.get("data", [])
        if not items:
            break

        for item in items:
            cur.execute("""
                insert into welfare_service(service_id, service_name, payload)
                values (%s, %s, %s::jsonb)
                on conflict (service_id)
                do update set
                  service_name = excluded.service_name,
                  payload = excluded.payload,
                  updated_at = now()
            """, (
                item["서비스ID"],
                item["서비스명"],
                json.dumps(item)
            ))

        # checkpoint 저장
        page += 1
        cur.execute("""
            update batch_run
            set checkpoint = %s
            where id = %s
        """, (json.dumps({"page": page}), batch_id))

        conn.commit()
        # ✅ 끝 판단 (정석)
        if (page - 1) * 1000 >= total_count:
            break

    # 성공 처리
    cur.execute("""
        update batch_run
        set status='SUCCESS', finished_at=now()
        where id=%s
    """, (batch_id,))
    conn.commit()

except Exception as e:
    conn.rollback()
    cur.execute("""
        update batch_run
        set status='FAILED', error=%s
        where id=%s
    """, (str(e), batch_id))
    conn.commit()
    raise

finally:
    cur.execute("select pg_advisory_unlock(hashtext(%s))", (JOB_NAME,))
    conn.commit()
    cur.close()
    conn.close()
