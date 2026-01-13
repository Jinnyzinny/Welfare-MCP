import os
import psycopg2
import json
import requests
import sys
from tenacity import retry, stop_after_attempt, wait_fixed

# 매핑 파일에서 가져오기 (이미지 및 필드 정보를 반영한 mapping)
from field_mapping import FIELD_MAPPING

# 1. 환경 변수 및 설정
JOB_NAME = os.environ.get("JOB_NAME", "welfare_sync_job")
API_KEY = os.environ["WELFARE_API_KEY"]

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_page(page: int):
    # 이미지 명세서에 따른 정확한 Endpoint 및 파라미터 설정
    url = "https://api.odcloud.kr/api/gov24/v3/serviceDetail"
    params = {
        "serviceKey": API_KEY,
        "page": page,          # 이미지 확인 결과: pageNo 아님
        "perPage": 100,        # 페이지당 데이터 수
        "returnType": "JSON"   # 이미지 확인 결과: resultType 아님
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

        # Advisory Lock (중복 실행 방지)
        cur.execute("select pg_try_advisory_lock(hashtext(%s))", (JOB_NAME,))
        if not cur.fetchone()[0]:
            print("Another batch is running. Exit.")
            return

        # Checkpoint 확인
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
                # 1) FIELD_MAPPING에 정의된 모든 컬럼 추출
                row_data = {db_col: (item.get(api_key) or "") for api_key, db_col in FIELD_MAPPING.items()}
            
                # 2) 모든 컬럼 매칭 쿼리 (19개 주요 필드 + payload)
                cur.execute("""
                    INSERT INTO welfare_service (
                        service_id, support_type, service_name, service_purpose,
                        apply_deadline, support_target, selection_criteria,
                        apply_method, required_documents, apply_org_name, contact_info,
                        apply_url, last_modified_time, provider_name, admin_rule, local_rule,
                        law_basis, official_required_documents, personal_verification_required_documents,
                        payload
                    )
                    VALUES (
                        %(service_id)s, %(support_type)s, %(service_name)s, %(service_purpose)s,
                        %(apply_deadline)s, %(support_target)s, %(selection_criteria)s,
                        %(apply_method)s, %(required_documents)s, %(apply_org_name)s, %(contact_info)s,
                        %(apply_url)s, %(last_modified_time)s, %(provider_name)s, %(admin_rule)s, %(local_rule)s,
                        %(law_basis)s, %(official_required_documents)s, %(personal_verification_required_documents)s,
                        %(payload)s::jsonb
                    )
                    ON CONFLICT (service_id)
                    DO UPDATE SET
                        support_type = EXCLUDED.support_type,
                        service_name = EXCLUDED.service_name,
                        service_purpose = EXCLUDED.service_purpose,
                        apply_deadline = EXCLUDED.apply_deadline,
                        support_target = EXCLUDED.support_target,
                        selection_criteria = EXCLUDED.selection_criteria,
                        apply_method = EXCLUDED.apply_method,
                        required_documents = EXCLUDED.required_documents,
                        apply_org_name = EXCLUDED.apply_org_name,
                        contact_info = EXCLUDED.contact_info,
                        apply_url = EXCLUDED.apply_url,
                        last_modified_time = EXCLUDED.last_modified_time,
                        provider_name = EXCLUDED.provider_name,
                        admin_rule = EXCLUDED.admin_rule,
                        local_rule = EXCLUDED.local_rule,
                        law_basis = EXCLUDED.law_basis,
                        official_required_documents = EXCLUDED.official_required_documents,
                        personal_verification_required_documents = EXCLUDED.personal_verification_required_documents,
                        payload = EXCLUDED.payload,
                        updated_at = NOW()
                """, {
                    **row_data, 
                    "payload": json.dumps(item, ensure_ascii=False)
                })

            current_page += 1
            cur.execute("update batch_run set checkpoint = %s where id = %s", 
                       (json.dumps({"page": current_page}), batch_id))
            conn.commit()
            
            # 페이지 전환 시 로그 출력하여 데이터 변화 감시
            if items:
                print(f"Page {current_page - 1} saved. First ID: {items[0].get('서비스ID') or items[0].get('SVC_ID')}")

        cur.execute("update batch_run set status='SUCCESS', finished_at=now() where id=%s", (batch_id,))
        conn.commit()

    except Exception as e:
        print(f"Batch Failed: {e}")
        if conn:
            conn.rollback()
            if batch_id:
                try:
                    with conn.cursor() as err_cur:
                        err_cur.execute("update batch_run set status='FAILED', error=%s where id=%s", (str(e), batch_id))
                    conn.commit()
                except: pass
        raise e
    finally:
        if conn:
            with conn.cursor() as final_cur:
                final_cur.execute("select pg_advisory_unlock(hashtext(%s))", (JOB_NAME,))
            conn.commit()
            conn.close()

if __name__ == "__main__":
    run_batch()