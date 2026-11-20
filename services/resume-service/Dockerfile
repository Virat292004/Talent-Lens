FROM python:3.12-slim
WORKDIR /app
COPY requirements-runtime.txt requirements.txt
RUN pip install --default-timeout=120 --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY services/resume-service/main.py /app/app.py
ENV PYTHONPATH=/app
EXPOSE 5002
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","5002"]
