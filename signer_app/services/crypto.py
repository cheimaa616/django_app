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

import cryptography.hazmat.primitives
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature

from django.utils import timezone

from .utils import compute_sha256, base64_encode, base64_decode
from ..models import SigningKey, SignedDocument, AuditLog

CURVE = ec.SECP256R1()


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
        document_id: Optional[str] = None,
        key_obj: Optional[SigningKey] = None,
    ) -> SignedDocument:
        """
        Signe un PDF et retourne l'enregistrement SignedDocument.

        Si `key_obj` est None (usage normal), une paire de clés ECDSA est
        générée automatiquement :
          - La clé publique est enregistrée dans SigningKey sous le nom du signataire.
          - La clé privée est utilisée pour signer puis immédiatement détruite.

        Si `key_obj` est fourni (rétro-compatibilité), il est utilisé tel quel.
        """
        file_hash = compute_sha256(pdf_file_path)

        # — Résolution de la clé —
        if key_obj is None:
            # Auto-génération : une clé par acte de signature
            private_key_obj, public_pem, private_pem = self._generate_raw_key_pair()

            # Nom unique : "signataire — horodatage"
            key_name = f"{signer_name} — {timezone.now().strftime('%Y%m%d-%H%M%S')}"
            key_obj = SigningKey.objects.create(
                name=key_name,
                public_key=public_pem,
                # La clé privée est stockée pour permettre un re-signing
                # si nécessaire (optionnel : mettre '' pour ne jamais la stocker)
                private_key=private_pem,
            )
        else:
            # Clé fournie explicitement — chargement depuis le PEM stocké
            private_key_obj = serialization.load_pem_private_key(
                key_obj.private_key.encode('utf-8'),
                password=None,
                backend=default_backend(),
            )

        # — Signature ECDSA —
        signature_bytes = private_key_obj.sign(
            file_hash.encode('utf-8'),
            ec.ECDSA(hashes.SHA256()),
        )
        signature_b64 = base64_encode(signature_bytes)

        # — Gestion des versions —
        if not document_id:
            filename = os.path.basename(pdf_file_path)
            existing = SignedDocument.objects.filter(
                filename=filename, is_latest=True
            ).first()
            if existing:
                document_id = str(existing.document_id)
                existing.is_latest = False
                existing.save()
            else:
                document_id = str(uuid.uuid4())
        else:
            SignedDocument.objects.filter(document_id=document_id).update(is_latest=False)

        # — Création de l'enregistrement —
        signed_doc = SignedDocument.objects.create(
            document_id=document_id,
            filename=os.path.basename(pdf_file_path),
            version=version,
            timestamp=timezone.now(),
            file_hash=file_hash,
            signature=signature_b64,
            signer_name=signer_name,
            public_key=key_obj.public_key,
            is_latest=True,
        )

        # — Journal d'audit —
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

    def verify_document_by_id(self, doc_id: str) -> Dict[str, Any]:
        """
        Vérifie un document à partir de son identifiant en base.
        Utilisé lors du scan QR : pas besoin de re-uploader le PDF.
        Vérifie la cohérence de la signature stockée et le statut de version.
        """
        try:
            doc = SignedDocument.objects.filter(document_id=doc_id).order_by('-timestamp').first()
            if not doc:
                return {"result": "INVALID", "reason": "Document introuvable en base de données."}

            # Vérification de la signature cryptographique (hash stocké vs signature stockée)
            sig_valid = self.verify_signature(doc.file_hash, doc.signature, doc.public_key)

            # Vérification de version
            latest_doc = SignedDocument.objects.filter(document_id=doc_id, is_latest=True).first()
            is_latest = latest_doc and (doc.version == latest_doc.version)

            if not sig_valid:
                result = "INVALID"
                reason = "La signature numérique est invalide ou corrompue."
            elif not is_latest:
                result = "OBSOLETE"
                reason = f"Une version plus récente existe : v{latest_doc.version}"
            else:
                result = "VALID"
                reason = "Document authentique — signature et version vérifiées."

            # Journal
            AuditLog.objects.create(
                event='VERIFY',
                document_id=str(doc_id),
                result=result,
                reason=reason,
                details={"method": "qr_scan"},
            )

            return {
                "result": result,
                "reason": reason,
                "document": doc,
                "latest_doc": latest_doc,
            }

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