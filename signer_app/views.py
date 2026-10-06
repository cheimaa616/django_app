import os
import uuid

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.conf import settings
from django.db import IntegrityError

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from .models import SigningKey, SignedDocument, AuditLog
from .serializers import SignedDocumentSerializer, AuditLogSerializer
from .services.crypto import CryptoService
from .forms import QRPlacementForm
from .services.qr_service import QRService, get_pdf_page_count, resolve_pages

import re
from django.db import IntegrityError, transaction
from django.db.models import Q
from .services.crypto import CryptoService, VersionExistsError

VERSION_RE = re.compile(r'^[A-Za-z0-9._-]{1,50}$')   # must be URL-safe: it's in the QR URL

crypto_service = CryptoService()
qr_service = QRService()


def _sign_context(form, selected_parent=''):
    return {
        'form': form,
        'existing_docs': SignedDocument.objects.filter(is_latest=True).order_by('filename'),
        'selected_parent': selected_parent,
    }

# ─────────────────────────────────────────────
# Tableau de bord
# ─────────────────────────────────────────────

def dashboard(request):
    context = {
        'doc_count': SignedDocument.objects.count(),
        'key_count': SigningKey.objects.count(),
        'verify_count': AuditLog.objects.filter(event='VERIFY').count(),
        'recent_docs': SignedDocument.objects.order_by('-timestamp')[:5],
        'recent_logs': AuditLog.objects.order_by('-timestamp')[:5],
    }
    return render(request, 'dashboard.html', context)


# ─────────────────────────────────────────────
# Signature — clé générée automatiquement
# ─────────────────────────────────────────────

def sign_document(request):
    if request.method == 'POST':
        pdf_file = request.FILES.get('pdf_file')
        signer_name = request.POST.get('signer_name', '').strip()
        version = request.POST.get('version', '1.0').strip()
        parent_id = request.POST.get('parent_document', '').strip()
        form = QRPlacementForm(request.POST)

        if not pdf_file:
            messages.error(request, "Veuillez sélectionner un fichier PDF.")
            return redirect('sign_document')
        if not signer_name:
            messages.error(request, "Le nom du signataire est obligatoire.")
            return redirect('sign_document')
        if not VERSION_RE.match(version):
            messages.error(request, "Version invalide : lettres, chiffres, '.', '_' et '-' uniquement (ex. 1.0, 2.1).")
            return render(request, 'sign_doc.html', _sign_context(form, parent_id))
        if parent_id:
            try:
                parent_id = str(uuid.UUID(parent_id))
            except ValueError:
                messages.error(request, "Document parent invalide.")
                return render(request, 'sign_doc.html', _sign_context(form))
        if not form.is_valid():
            return render(request, 'sign_doc.html', _sign_context(form, parent_id))

        page_mode = form.cleaned_data['page_mode']
        specific_pages = form.cleaned_data['specific_pages']
        position = form.cleaned_data['position']

        original_name = os.path.basename(pdf_file.name)
        # Unique temp name: no collision with leftovers, never renamed by storage
        temp_path = default_storage.save(f"temp/{uuid.uuid4().hex}.pdf", ContentFile(pdf_file.read()))
        full_temp_path = os.path.join(settings.MEDIA_ROOT, temp_path)
        created_files = []

        try:
            total_pages = get_pdf_page_count(full_temp_path)
            try:
                page_indices = resolve_pages(page_mode, specific_pages, total_pages)
            except ValueError as exc:
                messages.error(request, str(exc))
                return render(request, 'sign_doc.html', _sign_context(form, parent_id))

            # Everything below is ONE transaction: if the PDF/QR step fails,
            # the old version is NOT demoted and no half-built row remains.
            with transaction.atomic():
                signed_doc = crypto_service.sign_pdf(
                    pdf_file_path=full_temp_path,
                    signer_name=signer_name,
                    version=version,
                    document_id=parent_id or None,
                    key_obj=None,
                    filename=original_name,
                )

                qr_rel_path = f"qrs/{signed_doc.document_id}_v{version}_qr.png"
                qr_full_path = os.path.join(settings.MEDIA_ROOT, qr_rel_path)
                os.makedirs(os.path.dirname(qr_full_path), exist_ok=True)
                qr_service.generate_verification_qr(str(signed_doc.document_id), version, qr_full_path)
                created_files.append(qr_full_path)
                signed_doc.qr_code_file = qr_rel_path

                signed_pdf_rel_path = f"signed_pdfs/{signed_doc.document_id}_v{version}_signed.pdf"
                signed_pdf_full_path = os.path.join(settings.MEDIA_ROOT, signed_pdf_rel_path)
                os.makedirs(os.path.dirname(signed_pdf_full_path), exist_ok=True)
                qr_service.embed_qr_in_pdf(
                    full_temp_path, qr_full_path, signed_pdf_full_path,
                    page_indices=page_indices, position=position,
                )
                created_files.append(signed_pdf_full_path)
                signed_doc.pdf_file = signed_pdf_rel_path
                signed_doc.save()

            messages.success(
                request,
                f"✅ Document « {signed_doc.filename} » signé avec succès (v{version}). "
                f"QR Code ajouté sur {len(page_indices)} page(s)."
                + (" Les versions précédentes sont désormais obsolètes." if parent_id else "")
            )
            return redirect('doc_detail_version', doc_id=signed_doc.document_id, version=signed_doc.version)

        except (VersionExistsError, IntegrityError):
            messages.error(request, f"La version « {version} » existe déjà pour ce document. "
                                    "Veuillez incrémenter le numéro de version.")
        except Exception as e:
            messages.error(request, f"Erreur lors de la signature : {e}")
        finally:
            if os.path.exists(full_temp_path):
                os.remove(full_temp_path)

        # Failure: remove files created by this attempt (DB already rolled back)
        for p in created_files:
            if os.path.exists(p):
                os.remove(p)
        return redirect('sign_document')

    return render(request, 'sign_doc.html',
                  _sign_context(QRPlacementForm(), request.GET.get('doc', '')))






# ─────────────────────────────────────────────
# Vérification via QR Code (scan téléphone)
# ─────────────────────────────────────────────

def verify_qr(request, document_id, version=None):
    result_data = crypto_service.verify_document_by_id(str(document_id), version)
    doc = result_data.get('document')
    if doc is None:
        return render(request, 'verify_result.html', {
            'result': 'INVALID', 'reason': result_data['reason'], 'doc': None,
        })

    latest_doc = result_data.get('latest_doc')
    return render(request, 'verify_result.html', {
        'doc': doc,
        'history': SignedDocument.objects.filter(document_id=document_id).order_by('-timestamp'),
        'result': result_data['result'],
        'reason': result_data['reason'],
        'latest_doc': latest_doc,
        'is_latest': result_data['result'] != 'OBSOLETE',
        'latest_version': latest_doc.version if latest_doc else None,
    })


# ─────────────────────────────────────────────
# Liste & détail des documents
# ─────────────────────────────────────────────

def document_list(request):
    docs = SignedDocument.objects.all().order_by('-timestamp')
    return render(request, 'doc_list.html', {'docs': docs})


def document_detail(request, doc_id, version=None):
    history = SignedDocument.objects.filter(document_id=doc_id).order_by('-timestamp')
    if version:
        doc = get_object_or_404(history, version=version)
    else:
        doc = get_object_or_404(history, is_latest=True)
    latest = history.filter(is_latest=True).first() or history.first()

    audit = AuditLog.objects.filter(document_id=str(doc_id), details__version=doc.version) \
                            .order_by('-timestamp')[:10]
    return render(request, 'doc_detail.html', {
        'doc': doc,
        'history': history,
        'is_latest': doc.pk == latest.pk,
        'latest_version': latest.version,
        'audit_logs': audit,
    })


# ─────────────────────────────────────────────
# Journaux d'audit
# ─────────────────────────────────────────────

def audit_logs(request):
    logs = AuditLog.objects.all().order_by('-timestamp')
    return render(request, 'audit_log.html', {'logs': logs})


# ─────────────────────────────────────────────
# API REST
# ─────────────────────────────────────────────

@api_view(['GET'])
def api_verify(request, document_id, version=None):
    result = crypto_service.verify_document_by_id(str(document_id), version)
    doc = result.get('document')
    if not doc:
        return Response({"error": result['reason']}, status=status.HTTP_404_NOT_FOUND)
    data = SignedDocumentSerializer(doc).data
    data['verification_result'] = result['result']
    data['verification_reason'] = result['reason']
    return Response(data)


@api_view(['GET'])
def api_document(request, document_id):
    docs = SignedDocument.objects.filter(document_id=document_id)
    return Response(SignedDocumentSerializer(docs, many=True).data)


@api_view(['GET'])
def api_latest(request, document_id):
    latest = SignedDocument.objects.filter(document_id=document_id, is_latest=True).first()
    if not latest:
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
    return Response({"latest_version": latest.version, "timestamp": latest.timestamp})