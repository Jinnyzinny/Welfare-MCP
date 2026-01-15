import psycopg2
import json
import os
from fetch_page import fetch_page

from parse.parse_target_info import parse_target_info
from parse.parse_welfare_details import parse_welfare_details
from parse.parse_region import parse_region
from parse.get_embedding import get_embedding

# 매핑 파일에서 가져오기 (이미지 및 필드 정보를 반영한 mapping)
from field_mapping import FIELD_MAPPING

# DB 연결
from DB_Connection import dbConn

from sentence_transformers import SentenceTransformer

# 환경 변수 로드
from dotenv import load_dotenv
load_dotenv()

# 1. 환경 변수 및 설정
JOB_NAME = os.getenv("JOB_NAME", "welfare_sync_job")
MODEL_NAME = 'jhgan/ko-sroberta-multitask'

def run_batch():
    conn = None
    batch_id = None

    # [최적화 1] 모델을 루프 밖에서 한 번만 로드 (메모리 절약 및 속도 향상)
    print(f"Loading Embedding Model ({MODEL_NAME})...")
    try:
        model = SentenceTransformer(MODEL_NAME)
        print("Model loaded successfully.")
    except Exception as e:
        print(f"Model Load Failed: {e}")
        return
    
    try:
        # DB 연결
        conn = dbConn()
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

            # --- [최적화 2] 데이터 전처리 및 배치 수집 단계 ---
            target_texts = []   # 임베딩할 텍스트들을 모아둘 리스트
            parsed_rows = []    # DB에 넣을 데이터를 모아둘 리스트

            for item in items:
                # 1) FIELD_MAPPING에 정의된 모든 컬럼 추출
                row_data = {db_col: (item.get(api_key) or "") for api_key, db_col in FIELD_MAPPING.items()}
            
                # 2) [수정] provider_name 기반 지역 추출 (중앙정부 vs 지자체)
                provider = row_data.get("provider_name", "")
                sido, sigungu = parse_region(provider) # 분리된 모듈 호출

                # 2) [추가] 지원 대상 텍스트에서 나이/성별 파
                target_text = row_data.get("support_target", "")
                target_texts.append(target_text)
                # 3) 나이/성별 파싱
                min_v, max_v, gen_v = parse_target_info(target_text)
                # 4) 가구형태 및 소득 파싱
                household_type,min_income,max_income=parse_welfare_details(row_data)
                
                # 5) row_data 통합 (DB 컬럼명과 일치해야 함)
                row_data.update({
                    "min_age": min_v,
                    "max_age": max_v,
                    "gender": gen_v,
                    "sido": sido,
                    "sigungu": sigungu,
                    "household_type": household_type,
                    "min_income": min_income,
                    "max_income": max_income,
                    "payload": json.dumps(item, ensure_ascii=False)
                })
                parsed_rows.append(row_data)

            if target_texts:
                # 한 번에 임베딩 벡터들 생성
                embeddings = model.encode(target_texts, show_progress_bar=True).tolist()

                # 생성된 벡터를 각 데이터에 할당
                for row, emb in zip(parsed_rows, embeddings):
                    row["embedding"] = emb

            # 5) INSERT 실행
            cur.execute("""
                INSERT INTO welfare_service (
                service_id, support_type, service_name, service_purpose,
                apply_deadline, support_target, selection_criteria,
                apply_method, required_documents, apply_org_name, contact_info,
                apply_url, last_modified_time, provider_name, admin_rule, local_rule,
                law_basis, official_required_documents, personal_verification_required_documents,
                min_age, max_age, gender, sido, sigungu, household_type, 
                min_income, max_income, payload, embedding
            )
            VALUES (
                %(service_id)s, %(support_type)s, %(service_name)s, %(service_purpose)s,
                %(apply_deadline)s, %(support_target)s, %(selection_criteria)s,
                %(apply_method)s, %(required_documents)s, %(apply_org_name)s, %(contact_info)s,
                %(apply_url)s, %(last_modified_time)s, %(provider_name)s, %(admin_rule)s, %(local_rule)s,
                %(law_basis)s, %(official_required_documents)s, %(personal_verification_required_documents)s,
                %(min_age)s, %(max_age)s, %(gender)s, %(sido)s, %(sigungu)s, %(household_type)s,
                %(min_income)s, %(max_income)s, %(payload)s::jsonb, %(embedding)s
            )
            ON CONFLICT (service_id)
            DO UPDATE SET
                -- [수정] 원문 데이터도 모두 업데이트 (데이터 동기화)
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
                        
                        -- 파싱 데이터 업데이트
                        min_age = EXCLUDED.min_age,
                        max_age = EXCLUDED.max_age,
                        gender = EXCLUDED.gender,
                        sido = EXCLUDED.sido,
                        sigungu = EXCLUDED.sigungu,
                        household_type = EXCLUDED.household_type,
                        min_income = EXCLUDED.min_income,
                        max_income = EXCLUDED.max_income,
                        payload = EXCLUDED.payload,
                        embedding = EXCLUDED.embedding,
                        updated_at = NOW()
            """, row_data)

            current_page += 1
            cur.execute("update batch_run set checkpoint = %s where id = %s", 
                (json.dumps({"page": current_page}), batch_id))
            conn.commit()
            
            # 페이지 전환 시 로그 출력하여 데이터 변화 감시
            if items:
                print(f"Page {current_page} saved {len(items)} items).")

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