import os
import json
import smtplib
import redis
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from jinja2 import Environment, FileSystemLoader, ChoiceLoader

# Configurações do SMTP
SMTP_HOST = os.environ.get("SMTP_HOST") or os.environ.get("MAIL_HOST") or os.environ.get("MAIL_SERVER")
SMTP_PORT = int(os.environ.get("SMTP_PORT") or os.environ.get("MAIL_PORT") or 587)
SMTP_USER = os.environ.get("SMTP_USER") or os.environ.get("MAIL_USERNAME") or os.environ.get("MAIL_USER")
SMTP_PASS = os.environ.get("SMTP_PASS") or os.environ.get("MAIL_PASSWORD") or os.environ.get("MAIL_PASS")
SMTP_USE_SSL = (os.environ.get("SMTP_SSL") or os.environ.get("MAIL_USE_SSL") or "false").lower() in ("1", "true", "yes")
SMTP_USE_TLS = (os.environ.get("SMTP_TLS") or os.environ.get("MAIL_USE_TLS") or "true").lower() in ("1", "true", "yes")
SMTP_SENDER_NAME = os.environ.get("TITLE", "Emailer")
SMTP_FROM = os.environ.get("SMTP_FROM") or os.environ.get("MAIL_FROM") or SMTP_USER
SMTP_TIMEOUT = int(os.environ.get("SMTP_TIMEOUT", 30))
EMAIL_DEBUG = os.environ.get("EMAIL_DEBUG", "false").lower() in ("1", "true", "yes")

# Configuração do Redis
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis")
QUEUE_NAME = "email:send"
PROCESSING_QUEUE = "email:processing"
ERROR_QUEUE = "email:error"
TEMPLATE_ALIASES = {
    "verification": "verify-email",
}

# Template prefix para templates e logo específicos do projeto (ex: "car", "rcaldas")
TEMPLATE_PREFIX = os.environ.get("TEMPLATE_PREFIX", "")

# ChoiceLoader: templates do projeto primeiro, templates compartilhados como fallback
_loaders = [FileSystemLoader("./templates")]
if TEMPLATE_PREFIX:
    _loaders.insert(0, FileSystemLoader(f"./templates/{TEMPLATE_PREFIX}"))

env = Environment(loader=ChoiceLoader(_loaders))
env.globals['now'] = datetime.now

# Logo via URL pública do Next.js (public/logo.png) — funciona em todos os clientes incluindo Gmail
_app_url = (
    os.environ.get("AUTH_TRUST_HOST") or
    os.environ.get("NEXTAUTH_URL") or
    os.environ.get("APP_URL") or
    ""
).rstrip("/")

env.globals['logo_url'] = f"{_app_url}/logo.png" if _app_url else None
if env.globals['logo_url']:
    print(f"✅ Logo URL: {env.globals['logo_url']}")
else:
    print("⚠️  AUTH_TRUST_HOST não configurado: emails sem logo")

def mask(value):
    if not value:
        return "(vazio)"
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}***{value[-2:]}"

def log_config():
    mode = "SMTP" if SMTP_HOST else "DRY-RUN"
    print(f"Email worker iniciado em modo {mode}")
    print(f"SMTP host: {SMTP_HOST or '(nao configurado)'}")
    print(f"SMTP port: {SMTP_PORT}")
    print(f"SMTP TLS: {SMTP_USE_TLS} | SSL: {SMTP_USE_SSL}")
    print(f"SMTP user: {mask(SMTP_USER)}")
    print(f"SMTP from: {SMTP_FROM or '(nao configurado)'}")
    if not SMTP_HOST:
        print("⚠️  SMTP_HOST/MAIL_HOST/MAIL_SERVER nao configurado: emails serao apenas impressos no log.")
    if SMTP_HOST and (not SMTP_USER or not SMTP_PASS):
        print("⚠️  SMTP configurado sem usuario/senha. Login sera ignorado.")

def send_email(to, subject, html):
    """Envia email via SMTP ou imprime no console quando SMTP nao estiver configurado."""
    from_addr = SMTP_FROM or "no-reply@localhost"
    domain = from_addr.split("@")[-1] if "@" in from_addr else "localhost"

    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((SMTP_SENDER_NAME, from_addr))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    
    if not SMTP_HOST:
        print(f"[DRY-RUN EMAIL] To: {to}\nSubject: {subject}\nFrom: {msg['From']}\n\n{html}")
        return "dry-run"
        
    smtp_class = smtplib.SMTP_SSL if SMTP_USE_SSL else smtplib.SMTP
    with smtp_class(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as server:
        if EMAIL_DEBUG:
            server.set_debuglevel(1)
        server.ehlo()
        if SMTP_USE_TLS and not SMTP_USE_SSL:
            server.starttls()
            server.ehlo()
        if SMTP_USER and SMTP_PASS:
            server.login(SMTP_USER, SMTP_PASS)
        refused = server.sendmail(SMTP_FROM or SMTP_USER, [to], msg.as_string())
        if refused:
            raise RuntimeError(f"SMTP recusou destinatarios: {refused}")
    return "smtp"

def process_email(r, email_data):
    """Processa um email individual com tratamento de erro"""
    try:
        payload = json.loads(email_data)
        to = payload["to"]
        subject = payload["subject"]
        template_name = TEMPLATE_ALIASES.get(payload["template"], payload["template"])
        variables = payload.get("variables", {})
        if "verificationUrl" in variables and "verifyUrl" not in variables:
            variables["verifyUrl"] = variables["verificationUrl"]
        if "verifyUrl" in variables and "verificationUrl" not in variables:
            variables["verificationUrl"] = variables["verifyUrl"]

        # Renderiza o template
        template = env.get_template(f"{template_name}.html")
        html = template.render(**variables)

        # Envia o email
        delivery_mode = send_email(to, subject, html)
        if delivery_mode == "smtp":
            print(f"✅ SMTP aceitou email para {to} ({subject})")
        else:
            print(f"🧪 Email para {to} ({subject}) ficou em dry-run; nada foi enviado.")
        return True
        
    except Exception as e:
        print(f"❌ Erro ao processar email: {e}")
        # Move para fila de erro com timestamp
        error_payload = {
            "original_data": email_data.decode() if isinstance(email_data, bytes) else email_data,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
            "retry_count": 0
        }
        r.lpush(ERROR_QUEUE, json.dumps(error_payload))
        return False

def recover_processing_queue(r):
    """Recupera emails que estavam sendo processados quando o worker foi reiniciado"""
    recovered_count = 0
    while True:
        email_data = r.rpop(PROCESSING_QUEUE)
        if not email_data:
            break
        # Move de volta para a fila principal
        r.lpush(QUEUE_NAME, email_data)
        recovered_count += 1
    
    if recovered_count > 0:
        print(f"🔄 Recuperados {recovered_count} emails da fila de processamento")

def show_queue_status(r):
    """Mostra o status das filas"""
    pending = r.llen(QUEUE_NAME)
    processing = r.llen(PROCESSING_QUEUE)
    errors = r.llen(ERROR_QUEUE)
    
    print(f"📊 Status das filas:")
    print(f"   📥 Pendentes: {pending}")
    print(f"   ⚡ Processando: {processing}")
    print(f"   ❌ Erros: {errors}")
    
    return {"pending": pending, "processing": processing, "errors": errors}

def retry_failed_emails(r, max_retries=3):
    """Reprocessa emails que falharam"""
    retried_count = 0
    
    while True:
        error_data = r.rpop(ERROR_QUEUE)
        if not error_data:
            break
            
        try:
            error_payload = json.loads(error_data)
            retry_count = error_payload.get("retry_count", 0)
            
            if retry_count < max_retries:
                # Incrementa contador de tentativas
                error_payload["retry_count"] = retry_count + 1
                error_payload["last_retry"] = datetime.now().isoformat()
                
                # Move de volta para fila principal
                r.lpush(QUEUE_NAME, error_payload["original_data"])
                retried_count += 1
                print(f"🔄 Reenviando email (tentativa {retry_count + 1}/{max_retries})")
            else:
                # Muitas tentativas, mantém na fila de erro
                r.lpush(ERROR_QUEUE, error_data)
                print(f"⚠️  Email descartado após {max_retries} tentativas")
                
        except Exception as e:
            print(f"Erro ao processar email da fila de erro: {e}")
            # Recoloca na fila de erro
            r.lpush(ERROR_QUEUE, error_data)
    
    if retried_count > 0:
        print(f"🔄 {retried_count} emails reenviados para processamento")

def main():
    r = redis.Redis.from_url(REDIS_URL, socket_timeout=None, socket_connect_timeout=30, socket_keepalive=True)
    log_config()
    print("Aguardando mensagens...")
    
    # Recupera emails que estavam sendo processados
    recover_processing_queue(r)
    
    # Mostra status inicial das filas
    show_queue_status(r)
    
    # Tenta reprocessar emails com erro (máximo 3 tentativas)
    retry_failed_emails(r, max_retries=3)
    
    while True:
        try:
            # Move email da fila principal para fila de processamento (atomico)
            # blmove substitui brpoplpush removido no Redis 7+
            email_data = r.blmove(QUEUE_NAME, PROCESSING_QUEUE, 0, 'RIGHT', 'LEFT')
            print(f"📧 Processando email: {email_data}")
            
            # Processa o email
            success = process_email(r, email_data)
            
            if success:
                # Remove da fila de processamento apenas se enviado com sucesso
                r.lrem(PROCESSING_QUEUE, 1, email_data)
                print("✅ Email removido da fila de processamento")
            else:
                # Remove da fila de processamento (já foi movido para error_queue)
                r.lrem(PROCESSING_QUEUE, 1, email_data)
                print("❌ Email movido para fila de erro")
                
        except KeyboardInterrupt:
            print("\n🛑 Worker interrompido pelo usuário")
            break
        except Exception as e:
            print(f"💥 Erro crítico no worker: {e}")
            time.sleep(5)  # Aguarda antes de tentar novamente

if __name__ == "__main__":
    main()