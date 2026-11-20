FROM python:3.12-slim
WORKDIR /app
COPY requirements-matching.txt requirements.txt
COPY requirements-runtime.txt requirements-runtime.txt
RUN pip install --default-timeout=120 --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY services/matching-service/main.py /app/app.py
ENV PYTHONPATH=/app
ENV DISABLE_SEMANTIC_MODEL=true
EXPOSE 5005
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","5005"]
