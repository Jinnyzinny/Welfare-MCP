import os
import psycopg2
import json
import requests
import sys
from tenacity import retry, stop_after_attempt, wait_fixed

import field_mapping

# 1. 환경 변수 및 설정
JOB_NAME = os.environ.get("JOB_NAME", "welfare_sync_job")
API_KEY = os.environ["WELFARE_API_KEY"]

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_page(page: int):
    """OpenAPI에서 데이터를 한 페이지씩 가져옵니다."""
    url = "https://api.odcloud.kr/api/gov24/v3/serviceList"
    params = {
        "serviceKey": API_KEY,
        "pageNo": page,
        "numOfRows": 100,
        "resultType": "JSON"
    }
    # 공공데이터 API는 200 OK를 주면서 메시지로 에러를 주는 경우가 있어 timeout과 검증이 중요합니다.
    res = requests.get(url, params=params, timeout=20)
    res.raise_for_status()
    return res.json()

def run_batch():
    conn = None
    batch_id = None
    
    try:
        # DB 연결
        conn = psycopg2.connect(
            host=os.environ["PGHOST"],
            port=os.environ["PGPORT"],
            dbname=os.environ["PGDATABASE"],
            user=os.environ["PGUSER"],
            password=os.environ["PGPASSWORD"],
        )
        conn.autocommit = False
        cur = conn.cursor()

        # 1. Advisory Lock (중복 실행 방지)
        cur.execute("select pg_try_advisory_lock(hashtext(%s))", (JOB_NAME,))
        if not cur.fetchone()[0]:
            print("Another batch is running. Exit.")
            return

        # 2. Checkpoint 조회 (실패했던 지점 찾기)
        cur.execute("""
            select checkpoint from batch_run 
            where job_name = %s and status = 'FAILED' 
            order by started_at desc limit 1
        """, (JOB_NAME,))
        row = cur.fetchone()
        
        checkpoint = {"page": 1}
        if row and row[0]:
            checkpoint = row[0] if isinstance(row[0], dict) else json.loads(row[0])

        # 3. 신규 배치 실행 기록 생성
        cur.execute("""
            insert into batch_run(job_name, checkpoint, status) 
            values (%s, %s, 'RUNNING') returning id
        """, (JOB_NAME, json.dumps(checkpoint)))
        batch_id = cur.fetchone()[0]
        conn.commit()

        # 4. 데이터 수집 루프 (OpenAPI 읽기)
        current_page = checkpoint.get("page", 1)
        
        while True:
            print(f"Fetching page {current_page}...")
            data = fetch_page(current_page)
            
            # API 응답 구조에 맞춰 'data' 키 확인
            items = data.get("data", [])
            if not items:
                print("No more data. Batch finished.")
                break

            # 한 페이지의 데이터를 DB에 저장 (Upsert)
            for item in items:
            # 1) 매핑 정보를 바탕으로 데이터 추출
            row_data = {db_col: item.get(api_key) for api_key, db_col in FIELD_MAPPING.items()}
            
            # 2) 쿼리 실행 (추출한 데이터를 각 컬럼에 매핑)
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

            # 체크포인트 업데이트 및 페이지 커밋
            current_page += 1
            cur.execute("""
                update batch_run 
                set checkpoint = %s 
                where id = %s
            """, (json.dumps({"page": current_page}), batch_id))
            
            conn.commit()
            print(f"Successfully saved page {current_page - 1}")

        # 5. 최종 성공 상태 기록
        cur.execute("""
            update batch_run 
            set status='SUCCESS', finished_at=now() 
            where id=%s
        """, (batch_id,))
        conn.commit()

    except Exception as e:
        print(f"Batch Failed: {e}")
        if conn:
            conn.rollback()
            if batch_id:
                with conn.cursor() as err_cur:
                    err_cur.execute("""
                        update batch_run set status='FAILED', error=%s where id=%s
                    """, (str(e), batch_id))
                conn.commit()
        raise e
    finally:
        if conn:
            with conn.cursor() as final_cur:
                final_cur.execute("select pg_advisory_unlock(hashtext(%s))", (JOB_NAME,))
            conn.commit()
            conn.close()

if __name__ == "__main__":
    run_batch()