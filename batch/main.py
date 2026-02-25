import json
import os
import asyncio
from fetch_page import fetch_page

from parse.parse_target_info import parse_target_info
from parse.parse_welfare_details import parse_welfare_details
from parse.parse_region import parse_region
from parse.get_embedding import get_embedding
from parse.clean_text import clean_text

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
MODEL_NAME = "jhgan/ko-sroberta-multitask"


# Batch 작업의 효율성을 위해 비동기로 작업 전환
async def run_batch():
    conn = None
    batch_id = None

    # [최적화 1] 모델을 루프 밖에서 한 번만 로드
    print(f"Loading Embedding Model ({MODEL_NAME})...")
    try:
        model = SentenceTransformer(MODEL_NAME)
        print("Model loaded successfully.")
    except Exception as e:
        print(f"Model Load Failed: {e}")
        return

    try:
        # 비동기로 DB 연결
        conn = await dbConn()
        # autoCommit 비활성화
        conn.autocommit = False
        cur = conn.cursor()

        # Advisory Lock (중복 실행 방지)
        cur.execute("select pg_try_advisory_lock(hashtext(%s))", (JOB_NAME,))
        if not cur.fetchone()[0]:
            print("Another batch is running. Exit.")
            return

        # Checkpoint 확인
        cur.execute(
            """
            select checkpoint from batch_run 
            where job_name = %s and status = 'FAILED' 
            order by started_at desc limit 1
        """,
            (JOB_NAME,),
        )
        row = cur.fetchone()

        checkpoint = {"page": 1}
        if row and row[0]:
            checkpoint = row[0] if isinstance(row[0], dict) else json.loads(row[0])

        cur.execute(
            """
            insert into batch_run(job_name, checkpoint, status) 
            values (%s, %s, 'RUNNING') returning id
        """,
            (JOB_NAME, json.dumps(checkpoint)),
        )
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
            target_texts = []  # 임베딩할 텍스트 리스트
            parsed_rows = []  # DB에 넣을 데이터 리스트

            for item in items:
                # 1) 필드 매핑
                row_data = {
                    db_col: (item.get(api_key) or "")
                    for api_key, db_col in FIELD_MAPPING.items()
                }

                # 2) 지역 추출
                provider = row_data.get("provider_name", "")
                sido, sigungu = parse_region(provider)

                # 3) 지원 대상 텍스트 추출 및 파싱
                target_raw = row_data.get("support_target", "")
                target_text = clean_text(target_raw)
                target_texts.append(target_text)

                min_age, max_age, gender = parse_target_info(target_text)
                household_type, min_income, max_income = parse_welfare_details(row_data)

                # 4) row_data 통합
                row_data.update(
                    {
                        "min_age": min_age,
                        "max_age": max_age,
                        "gender": gender,
                        "sido": sido,
                        "sigungu": sigungu,
                        "household_type": household_type,
                        "min_income": min_income,
                        "max_income": max_income,
                        "payload": json.dumps(item, ensure_ascii=False),
                    }
                )
                parsed_rows.append(row_data)

            # --- [최적화 3] 배치 임베딩 생성 (for문 밖) ---
            if target_texts:
                embeddings = model.encode(target_texts, show_progress_bar=True).tolist()
                for row, emb in zip(parsed_rows, embeddings):
                    row["embedding"] = emb

            # --- [디버깅용 로그] 저장하기 전에 ID들을 눈으로 확인 ---
            print(f"--- [DEBUG] Page {current_page} ID Check ---")
            id_list = [r.get("service_id") for r in parsed_rows[:5]]  # 앞의 5개만 확인
            print(f"IDs to insert: {id_list}")

            print(min_age,max_age,gender,sido,sigungu,household_type,min_income,max_income)

            # --- [✅ 수정] 5) INSERT 실행 (루프 구조 주의) ---
            for row in parsed_rows:
                cur.execute(
                    """
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
                """,
                    row,
                )

            # --- [✅ 핵심 수정] 100개 저장 완료 후 페이지 업데이트 및 커밋 ---
            current_page += 1
            cur.execute(
                "update batch_run set checkpoint = %s where id = %s",
                (json.dumps({"page": current_page}), batch_id),
            )
            conn.commit()

            print(f"Page {current_page - 1} saved ({len(items)} items).")

        # --- 모든 페이지(while)가 끝난 후 성공 처리 ---
        cur.execute(
            "update batch_run set status='SUCCESS', finished_at=now() where id=%s",
            (batch_id,),
        )
        conn.commit()

    # Batch 작업이 실패할 경우 log 처리와 DB Rollback
    except Exception as e:
        print(f"Batch Failed: {e}")
        if conn:
            conn.rollback()
            if batch_id:
                try:
                    with conn.cursor() as err_cur:
                        err_cur.execute(
                            "update batch_run set status='FAILED', error=%s where id=%s",
                            (str(e), batch_id),
                        )
                    conn.commit()
                except:
                    pass
        raise e
    # 마지막으로 Advisory Lock 해제 및 COmmit 후 연결 종료
    finally:
        if conn:
            with conn.cursor() as final_cur:
                final_cur.execute(
                    "select pg_advisory_unlock(hashtext(%s))", (JOB_NAME,)
                )
            conn.commit()
            conn.close()


if __name__ == "__main__":
    asyncio.run(run_batch())
