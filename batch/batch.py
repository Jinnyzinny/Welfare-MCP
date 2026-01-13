import os
import psycopg2
from psycopg2 import sql
from tenacity import retry, stop_after_attempt, wait_fixed
import json
import requests
import sys

# 환경 변수 로드
JOB_NAME = os.environ.get("JOB_NAME", "welfare_sync_job")
API_KEY = os.environ["OPENAPI_KEY"]

def get_connection():
    return psycopg2.connect(
        host=os.environ["PGHOST"],
        port=os.environ["PGPORT"],
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
    )

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_page(page: int):
    url = "https://api.odcloud.kr/api/gov24/v3/serviceList"
    params = {
        "serviceKey": API_KEY,
        "pageNo": page,
        "numOfRows": 100,
        "resultType": "JSON" # API 명세에 따라 필요한 경우 추가
    }
    res = requests.get(url, params=params, timeout=15)
    res.raise_for_status()
    return res.json()

def run_batch():
    conn = get_connection()
    conn.autocommit = False # 트랜잭션 수동 제어
    
    try:
        with conn.cursor() as cur:
            # 1. 중복 실행 방지 (Advisory Lock)
            cur.execute("select pg_try_advisory_lock(hashtext(%s))", (JOB_NAME,))
            if not cur.fetchone()[0]:
                print(f"[{JOB_NAME}] Another batch is already running. Exit.")
                return

            # 2. 이전 실패 기록 확인 (Checkpoint 로드)
            cur.execute("""
                select checkpoint from batch_run 
                where job_name = %s and status = 'FAILED' 
                order by started_at desc limit 1
            """, (JOB_NAME,))
            row = cur.fetchone()
            checkpoint = row[0] if row else {"page": 1}
            
            # 3. 새로운 배치 실행 기록 생성
            cur.execute("""
                insert into batch_run(job_name, checkpoint, status) 
                values (%s, %s, 'RUNNING') 
                returning id
            """, (JOB_NAME, json.dumps(checkpoint)))
            batch_id = cur.fetchone()[0]
            conn.commit()

            print(f"Batch started. ID: {batch_id}, Start Page: {checkpoint.get('page')}")

            # 4. 루프 시작
            current_page = checkpoint.get("page", 1)
            while True:
                data = fetch_page(current_page)
                items = data.get("data", [])
                
                if not items:
                    print("No more data found. Finishing...")
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
                    """, (item["서비스ID"], item["서비스명"], json.dumps(item)))

                # 페이지 단위 커밋 및 체크포인트 갱신
                current_page += 1
                cur.execute("""
                    update batch_run 
                    set checkpoint = %s 
                    where id = %s
                """, (json.dumps({"page": current_page}), batch_id))
                
                conn.commit()
                print(f"Page {current_page-1} processed and committed.")

            # 5. 최종 성공 처리
            cur.execute("""
                update batch_run 
                set status='SUCCESS', finished_at=now() 
                where id=%s
            """, (batch_id,))
            conn.commit()
            print("Batch completed successfully.")

    except Exception as e:
        if conn:
            conn.rollback()
            # 에러 기록 보존을 위해 별도 커밋
            try:
                with conn.cursor() as error_cur:
                    error_cur.execute("""
                        update batch_run set status='FAILED', error=%s, finished_at=now() where id=%s
                    """, (str(e), batch_id))
                conn.commit()
            except:
                pass 
        print(f"Error occurred: {e}")
        raise e

    finally:
        if conn:
            # Lock 해제 및 연결 종료
            with conn.cursor() as final_cur:
                final_cur.execute("select pg_advisory_unlock(hashtext(%s))", (JOB_NAME,))
            conn.commit()
            conn.close()

if __name__ == "__main__":
    run_batch()