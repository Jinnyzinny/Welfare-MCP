import os
import psycopg2
import json
import requests
import sys
from tenacity import retry, stop_after_attempt, wait_fixed

# 매핑 파일에서 가져오기
from field_mapping import FIELD_MAPPING

# 1. 환경 변수 및 설정
JOB_NAME = os.environ.get("JOB_NAME", "welfare_sync_job")
API_KEY = os.environ["WELFARE_API_KEY"]

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_page(page: int):
    url = "https://api.odcloud.kr/api/gov24/v3/serviceList"
    params = {
        "serviceKey": API_KEY,
        "pageNo": page,
        "numOfRows": 100,
        "resultType": "JSON"
    }
    res = requests.get(url, params=params, timeout=20)
    res.raise_for_status()
    return res.json()

def run_batch():
    conn = None
    batch_id = None
    
    try:
        conn = psycopg2.connect(
            host=os.environ["PGHOST"],
            port=os.environ["PGPORT"],
            dbname=os.environ["PGDATABASE"],
            user=os.environ["PGUSER"],
            password=os.environ["PGPASSWORD"],
        )
        conn.autocommit = False
        cur = conn.cursor()

        cur.execute("select pg_try_advisory_lock(hashtext(%s))", (JOB_NAME,))
        if not cur.fetchone()[0]:
            print("Another batch is running. Exit.")
            return

        cur.execute("""
            select checkpoint from batch_run 
            where job_name = %s and status = 'FAILED' 
            order by started_at desc limit 1
        """, (JOB_NAME,))
        row = cur.fetchone()
        
        checkpoint = {"page": 1}
        if row and row[0]:
            checkpoint = row[0] if isinstance(row[0], dict) else json.loads(row[0])

        cur.execute("""
            insert into batch_run(job_name, checkpoint, status) 
            values (%s, %s, 'RUNNING') returning id
        """, (JOB_NAME, json.dumps(checkpoint)))
        batch_id = cur.fetchone()[0]
        conn.commit()

        current_page = checkpoint.get("page", 1)
        
        while True:
            print(f"Fetching page {current_page}...")
            data = fetch_page(current_page)
            items = data.get("data", [])
            
            if not items:
                print("No more data. Batch finished.")
                break

            for item in items:
                # 1) 매핑 정보를 바탕으로 데이터 추출 (None 방지를 위해 or "" 추가)
                row_data = {db_col: (item.get(api_key) or "") for api_key, db_col in FIELD_MAPPING.items()}
            
                # 2) 쿼리 실행
                cur.execute("""
                    INSERT INTO welfare_service (
                        service_id, service_name, service_purpose, support_type,
                        provider_name, apply_org_name, contact_info,
                        apply_period, apply_method, apply_url,
                        law_basis, admin_rule, local_rule, 
                        payload
                    )
                    VALUES (
                        %(service_id)s, %(service_name)s, %(service_purpose)s, %(support_type)s,
                        %(provider_name)s, %(apply_org_name)s, %(contact_info)s,
                        %(apply_period)s, %(apply_method)s, %(apply_url)s,
                        %(law_basis)s, %(admin_rule)s, %(local_rule)s,
                        %(payload)s::jsonb
                    )
                    ON CONFLICT (service_id)
                    DO UPDATE SET
                        service_name = EXCLUDED.service_name,
                        service_purpose = EXCLUDED.service_purpose,
                        support_type = EXCLUDED.support_type,
                        provider_name = EXCLUDED.provider_name,
                        apply_org_name = EXCLUDED.apply_org_name,
                        contact_info = EXCLUDED.contact_info,
                        apply_period = EXCLUDED.apply_period,
                        apply_method = EXCLUDED.apply_method,
                        apply_url = EXCLUDED.apply_url,
                        law_basis = EXCLUDED.law_basis,
                        admin_rule = EXCLUDED.admin_rule,
                        local_rule = EXCLUDED.local_rule,
                        payload = EXCLUDED.payload,
                        updated_at = NOW()
                """, {
                    **row_data, 
                    "payload": json.dumps(item, ensure_ascii=False)
                })

            current_page += 1
            cur.execute("""
                update batch_run set checkpoint = %s where id = %s
            """, (json.dumps({"page": current_page}), batch_id))
            
            conn.commit()
            print(f"Successfully saved page {current_page - 1}")

        cur.execute("""
            update batch_run set status='SUCCESS', finished_at=now() where id=%s
        """, (batch_id,))
        conn.commit()

    except Exception as e:
        print(f"Batch Failed: {e}")
        if conn:
            conn.rollback()
            if batch_id:
                try:
                    with conn.cursor() as err_cur:
                        err_cur.execute("""
                            update batch_run set status='FAILED', error=%s where id=%s
                        """, (str(e), batch_id))
                    conn.commit()
                except:
                    pass
        raise e
    finally:
        if conn:
            with conn.cursor() as final_cur:
                final_cur.execute("select pg_advisory_unlock(hashtext(%s))", (JOB_NAME,))
            conn.commit()
            conn.close()

if __name__ == "__main__":
    run_batch()