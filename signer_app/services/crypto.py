"""
CryptoService — gestion ECDSA P-256.

Changement clé : sign_pdf() génère automatiquement une paire de clés
éphémère pour chaque acte de signature. La clé publique est persistée
en base (SigningKey) sous le nom du signataire ; la clé privée est
utilisée immédiatement puis n'est plus conservée nulle part.
"""

import os
import uuid
from typing import Dict, Any, Optional

from django.db import transaction

import cryptography.hazmat.primitives
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature

from django.utils import timezone

from .utils import compute_sha256, base64_encode, base64_decode
from ..models import SigningKey, SignedDocument, AuditLog

CURVE = ec.SECP256R1()


class VersionExistsError(Exception):
    pass

class CryptoService:
    """Gestion des clés ECDSA, signature et vérification."""

    # ──────────────────────────────────────────────
    # Génération de clés (usage interne uniquement)
    # ──────────────────────────────────────────────

    def _generate_raw_key_pair(self):
        """
        Génère une paire de clés ECDSA P-256.
        Retourne (private_key_object, public_pem_str, private_pem_str).
        La clé privée NE doit pas sortir de cette méthode dans la réponse HTTP.
        """
        private_key = ec.generate_private_key(CURVE, default_backend())

        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode('utf-8')

        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode('utf-8')

        return private_key, public_pem, private_pem

    def generate_key_pair(self, name: str) -> SigningKey:
        """
        Génère et persiste une paire de clés nommée.
        Conservé pour compatibilité (admin, API éventuelle).
        """
        private_key, public_pem, private_pem = self._generate_raw_key_pair()
        return SigningKey.objects.create(
            name=name,
            public_key=public_pem,
            private_key=private_pem,
        )

    # ──────────────────────────────────────────────
    # Signature — auto-génération de clé intégrée
    # ──────────────────────────────────────────────

    def sign_pdf(
        self,
        pdf_file_path: str,
        signer_name: str,
        version: str = "1.0",
        document_id: Optional[str] = None,   # given => new version of that document
        key_obj: Optional[SigningKey] = None,
        filename: Optional[str] = None,      # real uploaded name (not the temp name)
    ) -> SignedDocument:
        file_hash = compute_sha256(pdf_file_path)
        filename = filename or os.path.basename(pdf_file_path)

        with transaction.atomic():
            if key_obj is None:
                key_name = signer_name.strip().lower()
                key_obj = SigningKey.objects.filter(name=key_name).first()
                if key_obj is None:
                    _, public_pem, private_pem = self._generate_raw_key_pair()
                    key_obj = SigningKey.objects.create(
                        name=key_name, public_key=public_pem, private_key=private_pem,
                    )

            private_key_obj = serialization.load_pem_private_key(
                key_obj.private_key.encode('utf-8'),
                password=None,
                backend=default_backend(),
            )
            signature_b64 = base64_encode(
                private_key_obj.sign(file_hash.encode('utf-8'), ec.ECDSA(hashes.SHA256()))
            )

            # — Versioning: explicit identity, old version demoted atomically —
            if document_id:
                document_id = str(document_id)
                siblings = SignedDocument.objects.select_for_update().filter(document_id=document_id)
                if not siblings.exists():
                    raise ValueError("Le document parent est introuvable.")
                if siblings.filter(version=version).exists():
                    raise VersionExistsError(version)
                siblings.filter(is_latest=True).update(is_latest=False)   # V(n-1) -> OBSOLETE
            else:
                document_id = str(uuid.uuid4())

            signed_doc = SignedDocument.objects.create(
                document_id=document_id,
                filename=filename,
                version=version,
                timestamp=timezone.now(),
                file_hash=file_hash,
                signature=signature_b64,
                signer_name=signer_name,
                public_key=key_obj.public_key,
                is_latest=True,
            )

            AuditLog.objects.create(
                event='SIGN',
                document_id=str(document_id),
                details={
                    "filename": signed_doc.filename,
                    "version": version,
                    "signer": signer_name,
                    "hash": file_hash,
                    "key_name": key_obj.name,
                },
            )
        return signed_doc
    # ──────────────────────────────────────────────
    # Vérification
    # ──────────────────────────────────────────────

    def verify_signature(self, file_hash: str, signature_b64: str, public_key_pem: str) -> bool:
        """Vérifie une signature ECDSA."""
        try:
            public_key = serialization.load_pem_public_key(
                public_key_pem.encode('utf-8'),
                backend=default_backend(),
            )
            public_key.verify(
                base64_decode(signature_b64),
                file_hash.encode('utf-8'),
                ec.ECDSA(hashes.SHA256()),
            )
            return True
        except (InvalidSignature, Exception):
            return False

    def verify_document_by_id(self, doc_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        """
        Verifies ONE specific version (the one in the QR). Status comes from the
        is_latest flag in the DB, never from comparing file hashes.
        Without `version` (legacy QR codes) the current version is checked.
        """
        try:
            versions = SignedDocument.objects.filter(document_id=doc_id)
            if version:
                doc = versions.filter(version=version).first()
            else:
                doc = versions.filter(is_latest=True).first() or versions.order_by('-timestamp').first()
            if not doc:
                return {"result": "INVALID", "reason": "Document introuvable en base de données."}

            latest_doc = versions.filter(is_latest=True).first()
            sig_valid = self.verify_signature(doc.file_hash, doc.signature, doc.public_key)

            if not sig_valid:
                result, reason = "INVALID", "La signature numérique est invalide ou corrompue."
            elif latest_doc and latest_doc.pk != doc.pk:
                result = "OBSOLETE"
                reason = (f"La version v{doc.version} a été remplacée par la "
                          f"version v{latest_doc.version}.")
            else:
                result, reason = "VALID", "Document authentique — signature et version vérifiées."

            AuditLog.objects.create(
                event='VERIFY',
                document_id=str(doc_id),
                result=result,
                reason=reason,
                details={"method": "qr_scan", "version": doc.version},
            )
            return {"result": result, "reason": reason, "document": doc, "latest_doc": latest_doc}
        except Exception as e:
            return {"result": "INVALID", "reason": str(e)}

    def verify_document(self, pdf_file_path: str, doc_id: str) -> Dict[str, Any]:
        """
        Vérification complète avec re-calcul du hash à partir du fichier physique.
        Conservé pour l'API ou un usage futur avec upload.
        """
        try:
            doc = SignedDocument.objects.filter(document_id=doc_id).order_by('-timestamp').first()
            if not doc:
                return {"result": "INVALID", "reason": "Document introuvable en base de données."}

            current_hash = compute_sha256(pdf_file_path)
            integrity = (current_hash == doc.file_hash)
            sig_valid = self.verify_signature(doc.file_hash, doc.signature, doc.public_key)

            latest_doc = SignedDocument.objects.filter(document_id=doc_id, is_latest=True).first()
            is_latest = latest_doc and (doc.version == latest_doc.version)

            if not integrity or not sig_valid:
                result = "INVALID"
                reason = "Hash invalide." if not integrity else "Signature invalide."
            elif not is_latest:
                result = "OBSOLETE"
                reason = f"Nouvelle version disponible : v{latest_doc.version}"
            else:
                result = "VALID"
                reason = "Toutes les vérifications ont réussi."

            AuditLog.objects.create(
                event='VERIFY',
                document_id=str(doc_id),
                result=result,
                reason=reason,
                details={"method": "file_upload"},
            )

            return {
                "result": result,
                "reason": reason,
                "document": doc,
                "latest_version": latest_doc.version if not is_latest else None,
            }
        except Exception as e:
            return {"result": "INVALID", "reason": str(e)}