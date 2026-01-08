FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/mcp_contest

RUN pip install --no-cache-dir mcp uvicorn starlette python-dotenv

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
