"""Persistent customer chat history for the assignment demo."""
import sqlite3
import mimetypes
import uuid
from io import BytesIO
from pathlib import Path
from time_utils import now_ist

DB_PATH = Path("logs/support_history.sqlite3")
_SCHEMA_READY = False

def _connect():
    global _SCHEMA_READY
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    if not _SCHEMA_READY:
        conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, customer TEXT, ticket_id TEXT, role TEXT, content TEXT, created_at TEXT, conversation_id TEXT)")
        try: conn.execute("ALTER TABLE messages ADD COLUMN conversation_id TEXT")
        except sqlite3.OperationalError: pass
        conn.execute("CREATE TABLE IF NOT EXISTS attachments (id INTEGER PRIMARY KEY, customer TEXT, ticket_id TEXT, filename TEXT, mime_type TEXT, file_path TEXT, content BLOB, created_at TEXT, conversation_id TEXT)")
        try: conn.execute("ALTER TABLE attachments ADD COLUMN content BLOB")
        except sqlite3.OperationalError: pass
        try: conn.execute("ALTER TABLE attachments ADD COLUMN conversation_id TEXT")
        except sqlite3.OperationalError: pass
        conn.execute("CREATE TABLE IF NOT EXISTS message_feedback (id INTEGER PRIMARY KEY, customer TEXT, message_id INTEGER, rating TEXT, created_at TEXT)")
        conn.commit(); _SCHEMA_READY = True
    return conn

def save_message(customer, role, content, ticket_id=None, conversation_id=None):
    with _connect() as conn:
        conn.execute("INSERT INTO messages(customer,ticket_id,role,content,created_at,conversation_id) VALUES (?,?,?,?,?,?)", (customer or "Guest", ticket_id, role, content, now_ist().strftime("%Y-%m-%d %H:%M:%S IST"), conversation_id))
        conn.commit()

def list_messages(customer, ticket_id=None, conversation_id=None):
    with _connect() as conn:
        if conversation_id: return conn.execute("SELECT * FROM messages WHERE customer=? AND conversation_id=? ORDER BY id", (customer, conversation_id)).fetchall()
        if ticket_id: return conn.execute("SELECT * FROM messages WHERE customer=? AND ticket_id=? ORDER BY id", (customer, ticket_id)).fetchall()
        return conn.execute("SELECT * FROM messages WHERE customer=? ORDER BY id", (customer,)).fetchall()

def list_conversations(customer):
    with _connect() as conn:
        return conn.execute("SELECT conversation_id, MIN(created_at) AS started_at, MAX(created_at) AS updated_at, COUNT(*) AS message_count, MAX(id) AS last_id FROM messages WHERE customer=? AND conversation_id IS NOT NULL GROUP BY conversation_id ORDER BY last_id DESC", (customer,)).fetchall()

def save_feedback(customer, message_id, rating):
    with _connect() as conn:
        conn.execute("INSERT INTO message_feedback(customer,message_id,rating,created_at) VALUES (?,?,?,?)", (customer, message_id, rating, now_ist().strftime("%Y-%m-%d %H:%M:%S IST")))
        conn.commit()

def _compressed_payload(filename, content, mime_type):
    """Compress evidence before it reaches disk; preserve browser-viewable formats."""
    detected = mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if detected.startswith("image/"):
        from PIL import Image
        image = Image.open(BytesIO(content))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format="JPEG", quality=78, optimize=True, progressive=True)
        return output.getvalue(), ".jpg", "image/jpeg"
    if detected == "application/pdf":
        try:
            from pypdf import PdfReader, PdfWriter
            reader = PdfReader(BytesIO(content)); writer = PdfWriter()
            for page in reader.pages:
                page.compress_content_streams(); writer.add_page(page)
            output = BytesIO(); writer.write(output)
            compressed = output.getvalue()
            if len(compressed) < len(content):
                return compressed, ".pdf", detected
        except Exception:
            pass
    return content, Path(filename).suffix.lower(), detected

def save_attachment(customer, ticket_id, filename, content, mime_type=None, conversation_id=None):
    """Persist an uploaded evidence file and link it to the complaint ticket."""
    safe_name = Path(filename).name
    compressed_content, suffix, detected_type = _compressed_payload(safe_name, content, mime_type)
    attachment_dir = Path("logs") / "attachments"
    attachment_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{ticket_id}_{uuid.uuid4().hex}{suffix}"
    stored_path = attachment_dir / stored_name
    stored_path.write_bytes(compressed_content)
    with _connect() as conn:
        conn.execute("INSERT INTO attachments(customer,ticket_id,filename,mime_type,file_path,content,created_at,conversation_id) VALUES (?,?,?,?,?,?,?,?)", (customer, ticket_id, safe_name, detected_type, str(stored_path), compressed_content, now_ist().strftime("%Y-%m-%d %H:%M:%S IST"), conversation_id))
        conn.commit()
    return stored_path

def list_attachments(customer, ticket_id=None, conversation_id=None):
    with _connect() as conn:
        if ticket_id:
            return conn.execute("SELECT * FROM attachments WHERE customer=? AND ticket_id=? ORDER BY id", (customer, ticket_id)).fetchall()
        if conversation_id:
            return conn.execute("SELECT * FROM attachments WHERE customer=? AND (conversation_id=? OR ticket_id IN (SELECT DISTINCT ticket_id FROM messages WHERE customer=? AND conversation_id=? AND ticket_id IS NOT NULL AND ticket_id != '')) ORDER BY id", (customer, conversation_id, customer, conversation_id)).fetchall()
        return conn.execute("SELECT * FROM attachments WHERE customer=? ORDER BY id", (customer,)).fetchall()

def link_attachment(attachment_id, ticket_id):
    with _connect() as conn:
        conn.execute("UPDATE attachments SET ticket_id=? WHERE id=?", (ticket_id, attachment_id))
        conn.commit()
