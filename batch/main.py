import os
import psycopg2
import json
import sys
from tenacity import retry, stop_after_attempt, wait_fixed

JOB_NAME = os.environ.get("JOB_NAME", "default_job")
API_KEY = os.environ["WELFARE_API_KEY"]

# 1. 연결 설정
conn = psycopg2.connect(
    host=os.environ["PGHOST"],
    port=os.environ["PGPORT"],
    dbname=os.environ["PGDATABASE"],
    user=os.environ["PGUSER"],
    password=os.environ["PGPASSWORD"],
)
conn.autocommit = False

# batch_id를 try 밖에서 None으로 초기화 (NameError 방지)
batch_id = None

try:
    cur = conn.cursor()

    # 2. Advisory Lock
    cur.execute("select pg_try_advisory_lock(hashtext(%s))", (JOB_NAME,))
    if not cur.fetchone()[0]:
        print("Another batch is running. Exit.")
        sys.exit(0)

    # 3. 이전 실행 checkpoint 조회
    cur.execute("""
        select checkpoint
        from batch_run
        where job_name = %s and status = 'FAILED'
        order by started_at desc
        limit 1
    """, (JOB_NAME,))
    row = cur.fetchone()

    # 중요: psycopg2는 JSONB를 dict로 가져옵니다. 
    # 따라서 별도의 json.loads()가 필요 없습니다.
    if row and row[0]:
        checkpoint = row[0] 
        # 만약 DB 타입이 JSONB가 아니라 TEXT라면 아래 한 줄이 필요할 수 있습니다.
        # if isinstance(checkpoint, str): checkpoint = json.loads(checkpoint)
    else:
        checkpoint = {"page": 1}

    # 4. batch_run 시작 기록
    cur.execute("""
        insert into batch_run(job_name, checkpoint, status)
        values (%s, %s, 'RUNNING')
        returning id
    """, (JOB_NAME, json.dumps(checkpoint)))
    batch_id = cur.fetchone()[0]
    conn.commit()

    # 5. 실행 로직 (생략된 부분은 기존과 동일)
    page = checkpoint.get("page", 1)
    
    # ... (데이터 페치 및 insert 루프) ...
    # 이 안에서도 cur.execute 시 json.dumps(checkpoint)를 사용하세요.

    # 6. 성공 처리
    cur.execute("""
        update batch_run set status='SUCCESS', finished_at=now() where id=%s
    """, (batch_id,))
    conn.commit()

except Exception as e:
    print(f"Error occurred: {e}")
    if conn:
        conn.rollback()
    
    # batch_id가 있을 때만(성공적으로 insert 되었을 때만) 에러 로그 업데이트
    if batch_id is not None:
        try:
            with conn.cursor() as err_cur:
                err_cur.execute("""
                    update batch_run
                    set status='FAILED', error=%s, finished_at=now()
                    where id=%s
                """, (str(e), batch_id))
            conn.commit()
        except Exception as db_err:
            print(f"Failed to record error in DB: {db_err}")
    raise e

finally:
    if 'cur' in locals() and not cur.closed:
        cur.execute("select pg_advisory_unlock(hashtext(%s))", (JOB_NAME,))
        conn.commit()
        cur.close()
    conn.close()