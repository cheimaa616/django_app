import os
import json
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import cryptography.hazmat.primitives
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature

from django.core.files.base import ContentFile
from django.utils import timezone
from django.conf import settings

from .utils import compute_sha256, base64_encode, base64_decode, get_iso_timestamp
from ..models import SigningKey, SignedDocument, AuditLog

CURVE = ec.SECP256R1()

class CryptoService:
    """Manages ECDSA keys, signing, and verification."""

    def generate_key_pair(self, name: str) -> SigningKey:
        """Generate a new ECDSA key pair and save to DB."""
        private_key = ec.generate_private_key(CURVE, default_backend())
        
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        
        public_key = private_key.public_key()
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        key_obj = SigningKey.objects.create(
            name=name,
            public_key=public_pem.decode('utf-8'),
            private_key=private_pem.decode('utf-8')
        )
        return key_obj

    def sign_pdf(self, pdf_file_path: str, key_obj: SigningKey, signer_name: str, version: str = "1.0", document_id: Optional[str] = None) -> SignedDocument:
        """Sign a PDF document."""
        file_hash = compute_sha256(pdf_file_path)
        
        # Load private key
        private_key = serialization.load_pem_private_key(
            key_obj.private_key.encode('utf-8'),
            password=None,
            backend=default_backend()
        )
        
        # Sign the hash
        signature = private_key.sign(
            file_hash.encode('utf-8'),
            ec.ECDSA(cryptography.hazmat.primitives.hashes.SHA256())
        )
        signature_b64 = base64_encode(signature)
        
        if not document_id:
            # Try to find existing document by filename to manage versioning (Approche C)
            filename = os.path.basename(pdf_file_path)
            existing = SignedDocument.objects.filter(filename=filename, is_latest=True).first()
            if existing:
                document_id = existing.document_id
                existing.is_latest = False
                existing.save()
            else:
                document_id = str(uuid.uuid4())
        else:
            # Mark previous versions as not latest
            SignedDocument.objects.filter(document_id=document_id).update(is_latest=False)

        # Create SignedDocument record
        signed_doc = SignedDocument.objects.create(
            document_id=document_id,
            filename=os.path.basename(pdf_file_path),
            version=version,
            timestamp=timezone.now(),
            file_hash=file_hash,
            signature=signature_b64,
            signer_name=signer_name,
            public_key=key_obj.public_key,
            is_latest=True
        )
        
        # Log event
        AuditLog.objects.create(
            event='SIGN',
            document_id=str(document_id),
            details={
                "filename": signed_doc.filename,
                "version": version,
                "signer": signer_name,
                "hash": file_hash
            }
        )
        
        return signed_doc

    def verify_signature(self, file_hash: str, signature_b64: str, public_key_pem: str) -> bool:
        """Verify ECDSA signature."""
        try:
            public_key = cryptography.hazmat.primitives.serialization.load_pem_public_key(
                public_key_pem.encode('utf-8'),
                backend=default_backend()
            )
            signature_bytes = base64_decode(signature_b64)
            
            public_key.verify(
                signature_bytes,
                file_hash.encode('utf-8'),
                ec.ECDSA(cryptography.hazmat.primitives.hashes.SHA256())
            )
            return True
        except (InvalidSignature, Exception):
            return False

    def verify_document(self, pdf_file_path: str, doc_id: str) -> Dict[str, Any]:
        """Verify a document's integrity and signature using DB metadata."""
        try:
            doc = SignedDocument.objects.filter(document_id=doc_id).order_by('-timestamp').first()
            if not doc:
                return {"result": "INVALID", "reason": "Document not found in database"}

            current_hash = compute_sha256(pdf_file_path)
            
            # 1. Integrity check
            integrity = (current_hash == doc.file_hash)
            
            # 2. Signature check
            sig_valid = self.verify_signature(doc.file_hash, doc.signature, doc.public_key)
            
            # 3. Version check
            latest_doc = SignedDocument.objects.filter(document_id=doc_id, is_latest=True).first()
            is_latest = (doc.version == latest_doc.version)
            
            result = "VALID"
            if not integrity or not sig_valid:
                result = "INVALID"
            elif not is_latest:
                result = "OBSOLETE"
            
            reason = "All checks passed"
            if not integrity: reason = "File hash mismatch"
            elif not sig_valid: reason = "Invalid digital signature"
            elif not is_latest: reason = f"Newer version exists: v{latest_doc.version}"

            # Log verification
            AuditLog.objects.create(
                event='VERIFY',
                document_id=str(doc_id),
                result=result,
                reason=reason
            )

            return {
                "result": result,
                "reason": reason,
                "document": doc,
                "latest_version": latest_doc.version if not is_latest else None
            }
        except Exception as e:
            return {"result": "INVALID", "reason": str(e)}
