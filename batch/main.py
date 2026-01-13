import os
import psycopg2
import json
import requests
import sys
from tenacity import retry, stop_after_attempt, wait_fixed

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
                cur.execute("""
                    insert into welfare_service(service_id, service_name, payload)
                    values (%s, %s, %s::jsonb)
                    on conflict (service_id)
                    do update set
                      service_name = excluded.service_name,
                      payload = excluded.payload,
                      updated_at = now()
                """, (
                    item.get("서비스ID"), 
                    item.get("서비스명"), 
                    json.dumps(item, ensure_ascii=False)
                ))

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