import os
import json
import smtplib
import redis
import time
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
PROCESSING_QUEUE = "email:processing"
ERROR_QUEUE = "email:error"

# Configuração dos templates
env = Environment(loader=FileSystemLoader("./templates"))
env.globals['now'] = datetime.now 

def send_email(to, subject, html):
    """Envia email via SMTP ou imprime no console em desenvolvimento"""
    msg = MIMEText(html, "html")
    msg["Subject"] = subject
    msg["From"] = f"{SMTP_SENDER_NAME} <{SMTP_USER}>"
    msg["To"] = to
    
    if not SMTP_HOST:
        print(f"[DEV EMAIL] To: {to}\nSubject: {subject}\nFrom: {SMTP_SENDER_NAME} <{SMTP_USER}>\n\n{html}")
        return
        
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [to], msg.as_string())

def process_email(r, email_data):
    """Processa um email individual com tratamento de erro"""
    try:
        payload = json.loads(email_data)
        to = payload["to"]
        subject = payload["subject"]
        template_name = payload["template"]
        variables = payload.get("variables", {})

        # Renderiza o template
        template = env.get_template(f"{template_name}.html")
        html = template.render(**variables)

        # Envia o email
        send_email(to, subject, html)
        print(f"✅ E-mail enviado para {to} ({subject})")
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
    r = redis.Redis.from_url(REDIS_URL)
    print("Email worker iniciado. Aguardando mensagens...")
    
    # Recupera emails que estavam sendo processados
    recover_processing_queue(r)
    
    # Mostra status inicial das filas
    show_queue_status(r)
    
    # Tenta reprocessar emails com erro (máximo 3 tentativas)
    retry_failed_emails(r, max_retries=3)
    
    while True:
        try:
            # Move email da fila principal para fila de processamento (atomico)
            email_data = r.brpoplpush(QUEUE_NAME, PROCESSING_QUEUE, timeout=0)
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