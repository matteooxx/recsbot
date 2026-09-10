FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py .
COPY static/ static/
COPY templates/ templates/
EXPOSE 5001
# gthread with 16 threads: long-running SSE streams each hold a thread, so the
# rest of the app stays responsive while a chat is mid-flight.
CMD ["gunicorn", "--workers", "1", "--threads", "16", "--worker-class", "gthread", \
     "--bind", "0.0.0.0:5001", "--timeout", "600", "--graceful-timeout", "30", \
     "--max-requests", "5000", "app:app"]
