FROM python:3.11-slim

WORKDIR /app

# 1. 배치 전용 의존성 파일만 복사
# 빌드 컨텍스트가 batch/ 폴더인 경우
COPY requirements.txt .

# 2. 무거운 패키지 설치 (레이어 캐싱)
RUN pip install --no-cache-dir -r requirements.txt

# 서버 실행 전 모델 캐싱 (권장)
RUN python -c "from sentence-transformers import SentenceTransformer; SentenceTransformer('jhgan/ko-sroberta-multitask')"

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
