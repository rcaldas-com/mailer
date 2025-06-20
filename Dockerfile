FROM python:3-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates /app/templates

CMD ["python", "-u", "app.py"]