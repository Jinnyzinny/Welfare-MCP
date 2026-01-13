FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    requests \
    python-dotenv \
    psycopg2-binary \
    tenacity

COPY . .

CMD ["python", "main.py"]
