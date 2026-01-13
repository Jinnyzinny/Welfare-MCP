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
    
    # ... 앞부분 동일 ...

    batch_id = None  # NameError 방지를 위해 미리 선언

    try:
        with conn.cursor() as cur:
            # 1. Advisory Lock
            cur.execute("select pg_try_advisory_lock(hashtext(%s))", (JOB_NAME,))
            if not cur.fetchone()[0]:
                print("Another batch is running. Exit.")
                sys.exit(0)
        # 2. 이전 실행 checkpoint 조회
        cur.execute("""
            select checkpoint from batch_run
            where job_name = %s and status = 'FAILED'
            order by started_at desc limit 1
        """, (JOB_NAME,))
        row = cur.fetchone()

        # 중요: row[0]가 이미 dict일 수 있고 str일 수도 있음 (DB 드라이버 설정에 따라)
        if row and row[0]:
            checkpoint = row[0]
            if isinstance(checkpoint, str): # 문자열이라면 변환
                checkpoint = json.loads(checkpoint)
        else:
            checkpoint = {"page": 1}

        # 3. batch_run 시작 기록
        cur.execute("""
            insert into batch_run(job_name, checkpoint)
            values (%s, %s)
            returning id
        """, (JOB_NAME, json.dumps(checkpoint)))
        batch_id = cur.fetchone()[0]
        conn.commit()

        # 4. 데이터 페치 및 저장 루프
        page = checkpoint.get("page", 1)
        while True:
            # ... (중략: 데이터 가져오기 로직) ...
        
            # 페이지 저장 후 체크포인트 업데이트
            page += 1
            with conn.cursor() as cur:
                cur.execute("""
                    update batch_run set checkpoint = %s where id = %s
                """, (json.dumps({"page": page}), batch_id))
                conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
    
        # batch_id가 생성된 경우에만 DB에 에러 로그 기록
        if batch_id is not None:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        update batch_run set status='FAILED', error=%s where id=%s
                    """, (str(e), batch_id))
                    conn.commit()
            except Exception as e2:
                print(f"Failed to log error to DB: {e2}")
    
        print(f"Batch Error: {e}")
        raise e

    finally:
        # ... (생략: 연결 종료 로직) ...
            if conn:
            # Lock 해제 및 연결 종료
                with conn.cursor() as final_cur:
                    final_cur.execute("select pg_advisory_unlock(hashtext(%s))", (JOB_NAME,))
            conn.commit()
            conn.close()

if __name__ == "__main__":
    run_batch()