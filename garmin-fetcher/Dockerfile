FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY fetcher.py .
COPY historical_import.py .
COPY api.py .
CMD ["sh", "-c", "while true; do python fetcher.py; sleep 86400; done"]

