import asyncio
import os

from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USERNAME = os.getenv("DB_USERNAME")
DB_PASSWORD = os.getenv("DB_PASSWORD")


# 데이터베이스 연결 설정
def dbConn():
    conn = asyncio.connect(
        host=DB_HOST, database=DB_NAME, user=DB_USERNAME, password=DB_PASSWORD
    )
    return conn
