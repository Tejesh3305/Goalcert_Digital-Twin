FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY nextxr-ontology/ ./nextxr-ontology/

WORKDIR /app/nextxr-ontology

EXPOSE 8000

CMD ["python", "-m", "server.main"]
