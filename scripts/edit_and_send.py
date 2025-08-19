import os
import logging
import json
import base64
import tempfile
import stat
from PyPDF2 import PdfReader
from email.message import EmailMessage
from google.oauth2 import service_account
from googleapiclient.discovery import build


# ───── Logging Setup ─────
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ───── Gmail API Auth Setup ─────
GMAIL_SENDER = os.getenv("GMAIL_SENDER")
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

service_account_json = os.getenv("GSA_CREDENTIALS")
if not service_account_json:
    raise ValueError("GSA_CREDENTIALS environment variable not set.")

# Create a temp file with restricted permissions
with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as temp:
    temp.write(service_account_json)
    temp_path = temp.name

# Restrict permissions (owner read/write only) - optional in Git but safer in other environments
os.chmod(temp_path, stat.S_IRUSR | stat.S_IWUSR)

try:
    creds = service_account.Credentials.from_service_account_file(
        temp_path, scopes=SCOPES
    )
    delegated_creds = creds.with_subject(GMAIL_SENDER)
    service = build("gmail", "v1", credentials=delegated_creds)
finally:
    os.remove(temp_path)  # Always delete the temp file

# ───── Email Mapping ─────
# Load email mapping from GitHub secret
encoded_map = os.getenv("EMAIL_MAP_BASE64")
email_map = {}

if encoded_map:
    try:
        decoded = base64.b64decode(encoded_map).decode("utf-8")
        email_map = json.loads(decoded)
    except Exception as e:
        raise RuntimeError(f"Failed to load email_map: {e}")
else:
    raise ValueError("EMAIL_MAP_BASE64 environment variable not set.")

# ───── Constants ─────
pdf_folder = '/home/runner/work/rel_pos/rel_pos/'
text_to_check = "Nenhum relatório encontrado para os filtros selecionados"
remaining_files = []

# ───── PDF Filtering ─────
for filename in os.listdir(pdf_folder):
    if filename.endswith(".pdf") and filename.startswith("filial"):
        pdf_path = os.path.join(pdf_folder, filename)
        try:
            reader = PdfReader(pdf_path)
            full_text = "".join(page.extract_text() or "" for page in reader.pages)

            if text_to_check in full_text:
                os.remove(pdf_path)
                logging.info(f"Deleted '{filename}' — matched filter text.")
            else:
                logging.info(f"Kept '{filename}' — report content found.")
                remaining_files.append(filename)

        except Exception as e:
            logging.error(f"Failed to read '{filename}': {e}")

# ───── Gmail API Send Emails ─────
for filename in remaining_files:
    filial_key = filename.replace(".pdf", "")
    receiver_email = email_map.get(filial_key)

    if not receiver_email:
        logging.warning(f"No email mapping found for {filial_key}. Skipping.")
        continue

    # Read the email body from file
    raw_email_body = os.getenv("EMAIL_BODY")
    if not raw_email_body:
        raise ValueError("EMAIL_BODY secret not set.")
    email_body = raw_email_body.format(filial_key=filial_key)

    # Compose Email
    msg = EmailMessage()
    msg["Subject"] = "Relatório POS – Teste"
    msg["From"] = GMAIL_SENDER
    msg["To"] = receiver_email
    # msg.set_content(f"Segue relatório teste para {filial_key}.")
    msg.set_content(email_body)

    file_path = os.path.join(pdf_folder, filename)
    try:
        with open(file_path, "rb") as f:
            file_data = f.read()
            msg.add_attachment(file_data, maintype="application", subtype="pdf", filename=filename)

        raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        send_result = service.users().messages().send(userId="me", body={"raw": raw_message}).execute()

        logging.info(f"Sent '{filename}' to {receiver_email} (message ID: {send_result['id']}).")

    except Exception as e:
        logging.error(f"Failed to send '{filename}' to {receiver_email}: {e}")
