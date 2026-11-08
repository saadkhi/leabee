FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
EXPOSE 8765
# Always set LEABEE_PASSWORD when exposing this beyond localhost.
CMD ["python", "-m", "leabee", "serve", "--host", "0.0.0.0", "--port", "8765"]
