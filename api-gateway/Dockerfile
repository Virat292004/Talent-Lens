FROM python:3.12-slim
WORKDIR /app
COPY requirements-gateway.txt /app/requirements.txt
COPY requirements-runtime.txt /app/requirements-runtime.txt
RUN pip install --default-timeout=120 --no-cache-dir -r requirements.txt
COPY api-gateway/main.py /app/main.py
COPY shared /app/shared
ENV PYTHONPATH=/app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
