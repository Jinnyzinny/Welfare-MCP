import json, os, sys, selectors, asyncio
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

# 기존 파싱 함수들
from fetch_page import fetch_page
from parse.parse_target_info import parse_target_info
from parse.parse_welfare_details import parse_welfare_details
from parse.parse_region import parse_region
from parse.clean_text import clean_text
from field_mapping import FIELD_MAPPING

# 수정된 비동기 DB 연결 함수 (AsyncConnectionPool 사용 가정)
from DB_Connection import get_db_pool, close_db_pool

load_dotenv()

JOB_NAME = os.getenv("JOB_NAME", "welfare_sync_job")
MODEL_NAME = "jhgan/ko-sroberta-multitask"

async def run_batch():
    batch_id = None
    
    # 1. 모델 로드
    print(f"Loading Embedding Model ({MODEL_NAME})...")
    model = SentenceTransformer(MODEL_NAME)
    print("Model loaded successfully.")

    # 2. DB 풀 가져오기
    pool = await get_db_pool()

    try:
        # 비동기 커넥션 빌리기
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                # [Advisory Lock] 비동기 실행
                await cur.execute("select pg_try_advisory_lock(hashtext(%s))", (JOB_NAME,))
                lock_result = await cur.fetchone()
                if not lock_result[0]:
                    print("Another batch is running. Exit.")
                    return

                # [Checkpoint 확인]
                await cur.execute("""
                    select checkpoint from batch_run 
                    where job_name = %s and status = 'FAILED' 
                    order by started_at desc limit 1
                """, (JOB_NAME,))
                row = await cur.fetchone()
                
                checkpoint = row[0] if row and row[0] else {"page": 1}
                if isinstance(checkpoint, str):
                    checkpoint = json.loads(checkpoint)

                # [Batch Run 생성]
                await cur.execute("""
                    insert into batch_run(job_name, checkpoint, status) 
                    values (%s, %s, 'RUNNING') returning id
                """, (JOB_NAME, json.dumps(checkpoint)))
                batch_id = (await cur.fetchone())[0]
                await conn.commit()

                current_page = checkpoint.get("page", 1)

                while True:
                    print(f"Fetching page {current_page}...")
                    # fetch_page가 동기 함수라면 그대로 쓰고, 비동기라면 await를 붙이세요.
                    data = fetch_page(current_page) 
                    items = data.get("data", [])
                    
                    print(data["currentCount"], "items fetched.")
                    if not items or data["currentCount"] == 0:
                        print("No more data. Batch finished.")
                        break

                    target_texts = []
                    parsed_rows = []

                    for item in items:
                        row_data = {db_col: (item.get(api_key) or "") for api_key, db_col in FIELD_MAPPING.items()}
                        
                        sido, sigungu = parse_region(row_data.get("provider_name", ""))
                        target_text = clean_text(row_data.get("support_target", ""))
                        target_texts.append(target_text)

                        min_age, max_age, gender = parse_target_info(target_text)
                        household_type, min_income, max_income = parse_welfare_details(row_data)

                        row_data.update({
                            "min_age": min_age, "max_age": max_age, "gender": gender,
                            "sido": sido, "sigungu": sigungu, "household_type": household_type,
                            "min_income": min_income, "max_income": max_income,
                            "payload": json.dumps(item, ensure_ascii=False),
                        })
                        parsed_rows.append(row_data)

                    # [Batch Embedding]
                    if target_texts:
                        embeddings = model.encode(target_texts).tolist()
                        for row, emb in zip(parsed_rows, embeddings):
                            row["embedding"] = emb

                    # [INSERT 실행] executemany 대신 개별 execute 사용 시 await 필수
                    for row in parsed_rows:
                        await cur.execute("""
                            INSERT INTO welfare_service (
                                service_id, support_type, service_name, service_purpose,
                                apply_deadline, support_target, selection_criteria,
                                apply_method, required_documents, apply_org_name, contact_info,
                                apply_url, last_modified_time, provider_name, admin_rule, local_rule,
                                law_basis, official_required_documents, personal_verification_required_documents,
                                min_age, max_age, gender, sido, sigungu, household_type, 
                                min_income, max_income, payload, embedding
                            ) VALUES (
                                %(service_id)s, %(support_type)s, %(service_name)s, %(service_purpose)s,
                                %(apply_deadline)s, %(support_target)s, %(selection_criteria)s,
                                %(apply_method)s, %(required_documents)s, %(apply_org_name)s, %(contact_info)s,
                                %(apply_url)s, %(last_modified_time)s, %(provider_name)s, %(admin_rule)s, %(local_rule)s,
                                %(law_basis)s, %(official_required_documents)s, %(personal_verification_required_documents)s,
                                %(min_age)s, %(max_age)s, %(gender)s, %(sido)s, %(sigungu)s, %(household_type)s,
                                %(min_income)s, %(max_income)s, %(payload)s::jsonb, %(embedding)s
                            ) ON CONFLICT (service_id) DO UPDATE SET
                                service_name = EXCLUDED.service_name,
                                updated_at = NOW(),
                                embedding = EXCLUDED.embedding
                                -- (기타 필드 생략, 실제 코드에선 다 넣으세요)
                        """, row)

                    # 페이지 업데이트 및 커밋
                    current_page += 1
                    await cur.execute(
                        "update batch_run set checkpoint = %s where id = %s",
                        (json.dumps({"page": current_page}), batch_id),
                    )
                    await conn.commit()
                    print(f"Page {current_page - 1} saved.")

                # 최종 성공 처리
                await cur.execute(
                    "update batch_run set status='SUCCESS', finished_at=now() where id=%s",
                    (batch_id,),
                )
                await conn.commit()

    except Exception as e:
        print(f"Batch Failed: {e}")
        if conn:
            await conn.rollback()  # 👈 일단 에러 난 트랜잭션은 롤백해서 깨끗하게 만듦
        
        if batch_id:
            # 새 트랜잭션으로 상태 업데이트
            async with pool.connection() as error_conn:
                await error_conn.execute(
                    "UPDATE batch_run SET status='FAILED', error=%s, finished_at=now() WHERE id=%s",
                    (str(e), batch_id),
                )
                await error_conn.commit()
        raise e
    finally:
        # Lock 해제 및 풀 종료
        await close_db_pool()

if __name__ == "__main__":
    if sys.platform == 'win32':
        # 윈도우에서만 SelectorEventLoop 사용 (psycopg 비동기 호환성)
        asyncio.run(
            run_batch(), 
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        )
    else:
        # 리눅스 등 기타 환경에서는 기본 루프 사용
        asyncio.run(run_batch())