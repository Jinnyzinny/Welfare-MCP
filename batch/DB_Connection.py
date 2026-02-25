import psycopg
import os
from psycopg_pool import ConnectionPool
import threading
from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()

db_pool: ConnectionPool | None = None
_init_lock = threading.Lock()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USERNAME = os.getenv("DB_USERNAME")
DB_PASSWORD = os.getenv("DB_PASSWORD")


# 데이터베이스 연결 설정
def dbConn():
    conn = ConnectionPool(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USERNAME,
        password=DB_PASSWORD,
        min_size=1,
        max_size=2,
        timeout=10.0,
    )
    return conn
