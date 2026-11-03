FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUTF8=1 PYTHONUNBUFFERED=1 HF_HOME=/models
COPY requirements.txt .
# CPU wheels keep the image small; mount a GPU build instead if you have one.
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
COPY . .
EXPOSE 8000
# Per-IP limits need the real client IP. uvicorn trusts X-Forwarded-For only from the addresses in
# FORWARDED_ALLOW_IPS: set it to your reverse proxy's address (not "*" when the port is public).
ENV FORWARDED_ALLOW_IPS=127.0.0.1
CMD ["uvicorn", "agent_server.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
