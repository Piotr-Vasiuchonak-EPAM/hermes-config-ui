FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir fastapi==0.115.* uvicorn==0.32.* pyyaml==6.*
WORKDIR /app
COPY app/ /app/
USER 1000:1000
ENV HOME=/tmp
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
