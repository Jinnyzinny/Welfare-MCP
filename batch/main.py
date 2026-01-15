import psycopg2
import json
import os
from fetch_page import fetch_page

from parse.parse_target_info import parse_target_info
from parse.parse_welfare_details import parse_welfare_details
from parse.parse_region import parse_region

# 매핑 파일에서 가져오기 (이미지 및 필드 정보를 반영한 mapping)
from field_mapping import FIELD_MAPPING

from parse.get_embedding import get_embedding

# 1. 환경 변수 및 설정
JOB_NAME = os.environ.get("JOB_NAME", "welfare_sync_job")

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
        # Batch 작업 중에는 자동 커밋 비활성화
        conn.autocommit = False
        # DB 커서 생성
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
            
                # 2) [수정] provider_name 기반 지역 추출 (중앙정부 vs 지자체)
                provider = row_data.get("provider_name", "")
                sido, sigungu = parse_region(provider) # 분리된 모듈 호출

                # 2) [추가] 지원 대상 텍스트에서 나이/성별 파
                target_text = row_data.get("support_target", "")
                min_v, max_v, gen_v = parse_target_info(target_text)
                household_type,min_income,max_income=parse_welfare_details(row_data)

                # 여기서 768차원 리스트가 생성됩니다.
                embedding_vector = get_embedding(target_text)

                # 4) row_data 통합 (DB 컬럼명과 일치해야 함)
                row_data.update({
                    "min_age": min_v,
                    "max_age": max_v,
                    "gender": gen_v,
                    "sido": sido,
                    "sigungu": sigungu,
                    "household_type": household_type,
                    "min_income": min_income,
                    "max_income": max_income,
                    "payload": json.dumps(item, ensure_ascii=False),"embedding": embedding_vector,
                    "embedding": embedding_vector,
                })

            # 5) INSERT 실행
            cur.execute("""
                INSERT INTO welfare_service (
                service_id, support_type, service_name, service_purpose,
                apply_deadline, support_target, selection_criteria,
                apply_method, required_documents, apply_org_name, contact_info,
                apply_url, last_modified_time, provider_name, admin_rule, local_rule,
                law_basis, official_required_documents, personal_verification_required_documents,
                min_age, max_age, gender, sido, sigungu, household_type, 
                min_income, max_income, payload
            )
            VALUES (
                %(service_id)s, %(support_type)s, %(service_name)s, %(service_purpose)s,
                %(apply_deadline)s, %(support_target)s, %(selection_criteria)s,
                %(apply_method)s, %(required_documents)s, %(apply_org_name)s, %(contact_info)s,
                %(apply_url)s, %(last_modified_time)s, %(provider_name)s, %(admin_rule)s, %(local_rule)s,
                %(law_basis)s, %(official_required_documents)s, %(personal_verification_required_documents)s,
                %(min_age)s, %(max_age)s, %(gender)s, %(sido)s, %(sigungu)s, %(household_type)s,
                %(min_income)s, %(max_income)s, %(payload)s::jsonb
            )
            ON CONFLICT (service_id)
            DO UPDATE SET
                min_age = EXCLUDED.min_age,
                max_age = EXCLUDED.max_age,
                gender = EXCLUDED.gender,
                sido = EXCLUDED.sido,
                sigungu = EXCLUDED.sigungu,
                household_type = EXCLUDED.household_type,
                min_income = EXCLUDED.min_income,
                max_income = EXCLUDED.max_income,
                payload = EXCLUDED.payload,
                updated_at = NOW(),
                embedding = EXCLUDED.embedding
            """, row_data)

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