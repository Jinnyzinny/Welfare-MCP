import os
from psycopg_pool import AsyncConnectionPool  # 비동기 풀 사용
from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()

# 전역 변수로 관리 (싱글톤 패턴)
db_pool: AsyncConnectionPool | None = None

DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USERNAME = os.getenv("DB_USERNAME")
DB_PASSWORD = os.getenv("DB_PASSWORD")

def get_conninfo():
    """공식 문서(libpq) 키워드를 활용한 연결 문자열 생성"""
    return f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USERNAME} password={DB_PASSWORD}"

async def get_db_pool():
    """비동기 DB 풀을 초기화하고 반환"""
    global db_pool
    if db_pool is None:
        db_pool = AsyncConnectionPool(
            conninfo=get_conninfo(),
            min_size=1,
            max_size=3,
            timeout=5.0,
            open=False # 필요할 때 열도록 설정
        )
        await db_pool.open() # 비동기로 풀 오픈
    return db_pool

# 배치가 끝날 때 풀을 닫아주는 함수 (필수)
async def close_db_pool():
    global db_pool
    if db_pool:
        await db_pool.close()
        db_pool = None