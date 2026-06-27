import hashlib
import base64
import uuid
import os
from datetime import datetime, timezone
from typing import Tuple

def compute_sha256(file_path: str) -> str:
    """Compute SHA-256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(65536):
            sha256.update(chunk)
    return sha256.hexdigest()

def base64_encode(data: bytes) -> str:
    """Encode bytes to Base64 string."""
    return base64.b64encode(data).decode('utf-8')

def base64_decode(data: str) -> bytes:
    """Decode Base64 string to bytes."""
    return base64.b64decode(data.encode('utf-8'))

def generate_document_id() -> str:
    """Generate a unique document identifier."""
    return str(uuid.uuid4())

def get_iso_timestamp() -> str:
    """Get current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()

def validate_pdf_file(file_path: str) -> None:
    """Validate that a file exists and appears to be a PDF."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"PDF file not found: {file_path}")
    
    if not os.path.isfile(file_path):
        raise ValueError(f"Path is not a file: {file_path}")
    
    with open(file_path, 'rb') as f:
        header = f.read(5)
        if header != b'%PDF-':
            raise ValueError(f"File does not appear to be a valid PDF: {file_path}")

def ensure_dir(directory: str) -> None:
    """Create directory if it doesn't exist."""
    os.makedirs(directory, exist_ok=True)
