FROM python:3.12-slim
WORKDIR /app
COPY orchestrator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY orchestrator/ ./orchestrator/
ENV CACHE_DIR=/data/cache ORCH_PORT=8095
EXPOSE 8095
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8095/health')"
CMD ["sh", "-c", "uvicorn orchestrator.api:app --host 0.0.0.0 --port ${ORCH_PORT}"]
