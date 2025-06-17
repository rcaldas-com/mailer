import os
import json
import smtplib
import redis
from datetime import datetime
from email.mime.text import MIMEText
from jinja2 import Environment, FileSystemLoader

# Configurações do SMTP
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
SMTP_SENDER_NAME = os.environ.get("TITLE", "Emailer")

# Configuração do Redis
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis")
QUEUE_NAME = "email:send"

# Configuração dos templates
env = Environment(loader=FileSystemLoader("./templates"))
env.globals['now'] = datetime.now 

def send_email(to, subject, html):
    msg = MIMEText(html, "html")
    msg["Subject"] = subject
    msg["From"] = f"{SMTP_SENDER_NAME} <{SMTP_USER}>"
    msg["To"] = to
    print(f"[DEV EMAIL] To: {to}\nSubject: {subject}\nFrom: {SMTP_SENDER_NAME} <{SMTP_USER}>\n\n{html}")
    if not SMTP_HOST:
        print(f"[DEV EMAIL] To: {to}\nSubject: {subject}\nFrom: {SMTP_SENDER_NAME} <{SMTP_USER}>\n\n{html}")
        return
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [to], msg.as_string())

def main():
    r = redis.Redis.from_url(REDIS_URL)
    print("Email worker iniciado. Aguardando mensagens...")

    while True:
        _, raw = r.blpop(QUEUE_NAME)
        print(f"Received raw message from queue: {raw}")
        payload = json.loads(raw)
        to = payload["to"]
        subject = payload["subject"]
        template_name = payload["template"]
        variables = payload.get("variables", {})

        # Renderiza o template
        template = env.get_template(f"{template_name}.html")
        html = template.render(**variables)

        try:
            send_email(to, subject, html)
            print(f"E-mail enviado para {to} ({subject})")
        except Exception as e:
            print(f"Erro ao enviar e-mail para {to}: {e}")

if __name__ == "__main__":
    main()