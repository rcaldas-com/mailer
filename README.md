# Emailer Worker

Este serviço é responsável por consumir uma fila Redis e enviar e-mails transacionais (ex: redefinição de senha) usando templates Jinja2.

## Como funciona

- O worker fica escutando a fila `email:send` no Redis.
- Ao receber uma mensagem, renderiza o template Jinja2 correspondente e envia o e-mail via SMTP.
- Se não houver configuração SMTP, apenas imprime o e-mail no console (modo desenvolvimento).

## Variáveis de ambiente

Configure as variáveis no `.env` (ou diretamente no ambiente Docker):

| Variável         | Descrição                                 | Exemplo                        |
|------------------|-------------------------------------------|--------------------------------|
| SMTP_HOST        | Host do servidor SMTP                     | smtp.gmail.com                 |
| SMTP_PORT        | Porta do SMTP                             | 587                            |
| SMTP_USER        | Usuário SMTP                              | usuario@gmail.com              |
| SMTP_PASS        | Senha SMTP                                | senha123                       |
| SMTP_SENDER      | E-mail do remetente                       | no-reply@seudominio.com        |
| SMTP_SENDER_NAME | Nome do remetente                         | Car App                        |
| REDIS_URL        | URL do Redis                              | redis://redis:6379/0           |
| TITLE            | Nome do app (usado como remetente)        | Car App                        |

## Templates

- Os templates Jinja2 ficam em `emailer/templates/`.
- Exemplo: `reset-password.html`  
  Use variáveis como `{{ name }}`, `{{ resetUrl }}`, etc.

## Exemplo de payload na fila

```json
{
  "to": "usuario@email.com",
  "subject": "Redefinição de senha",
  "template": "reset-password",
  "variables": {
    "name": "Usuário",
    "resetUrl": "https://carapp.com/reset-password?token=abc123"
  }
}
```

## Rodando com Docker Compose

O serviço já está configurado no `docker-compose.yml`:

```yaml
emailer:
  build: ./emailer
  restart: unless-stopped
  env_file: .env
  depends_on:
    - redis
  volumes:
    - ./emailer/app:/app/app
    - ./emailer/templates:/app/templates
```

## Rodando localmente

1. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```
2. Execute o worker:
   ```bash
   python app.py
   ```

---

**Dica:**  
Você pode adaptar e expandir este worker para outros tipos de e-mail e templates conforme sua necessidade.
