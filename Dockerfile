FROM python:3.12-slim
WORKDIR /app
RUN useradd --create-home --uid 10001 leabee
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=leabee:leabee . .
ENV PYTHONUNBUFFERED=1
USER leabee
EXPOSE 8765
# The app refuses to listen on 0.0.0.0 without a strong LEABEE_PASSWORD (or LEABEE_PASSWORD_HASH), and serves with waitress.
# Put it behind HTTPS (a reverse proxy such as Caddy or nginx) before exposing it, and set LEABEE_ALLOWED_HOSTS to your domain.
CMD ["python", "-m", "leabee", "serve", "--host", "0.0.0.0", "--port", "8765"]
