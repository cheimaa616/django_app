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

crypto_service = CryptoService()
qr_service = QRService()


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
    """
    L'utilisateur fournit : le PDF, son nom, la version, et les options de
    placement du QR Code (pages + position).
    """
    if request.method == 'POST':
        pdf_file = request.FILES.get('pdf_file')
        signer_name = request.POST.get('signer_name', '').strip()
        version = request.POST.get('version', '1.0').strip()
        form = QRPlacementForm(request.POST)

        if not pdf_file:
            messages.error(request, "Veuillez sélectionner un fichier PDF.")
            return redirect('sign_document')
        if not signer_name:
            messages.error(request, "Le nom du signataire est obligatoire.")
            return redirect('sign_document')
        if not form.is_valid():
            # Re-render (no redirect) so the user's choices and errors stay visible
            return render(request, 'sign_doc.html', {'form': form})

        page_mode = form.cleaned_data['page_mode']
        specific_pages = form.cleaned_data['specific_pages']
        position = form.cleaned_data['position']

        temp_path = default_storage.save('temp/' + pdf_file.name, ContentFile(pdf_file.read()))
        full_temp_path = os.path.join(settings.MEDIA_ROOT, temp_path)

        try:
            # 0. Validate pages BEFORE signing, so a bad page number
            #    never leaves orphan keys / documents / audit rows.
            total_pages = get_pdf_page_count(full_temp_path)
            try:
                page_indices = resolve_pages(page_mode, specific_pages, total_pages)
            except ValueError as exc:
                messages.error(request, str(exc))
                return render(request, 'sign_doc.html', {'form': form})

            # 1. Signature + auto-génération de clé (inchangé)
            signed_doc = crypto_service.sign_pdf(
                pdf_file_path=full_temp_path,
                signer_name=signer_name,
                version=version,
                key_obj=None,
            )

            # 2. QR Code (inchangé)
            qr_filename = f"{signed_doc.document_id}_v{version}_qr.png"
            qr_rel_path = f"qrs/{qr_filename}"
            qr_full_path = os.path.join(settings.MEDIA_ROOT, qr_rel_path)
            os.makedirs(os.path.dirname(qr_full_path), exist_ok=True)

            qr_service.generate_verification_qr(str(signed_doc.document_id), qr_full_path)
            signed_doc.qr_code_file = qr_rel_path

            # 3. Intégration du QR dans les pages choisies, à la position choisie
            signed_pdf_name = f"{signed_doc.document_id}_v{version}_signed.pdf"
            signed_pdf_rel_path = f"signed_pdfs/{signed_pdf_name}"
            signed_pdf_full_path = os.path.join(settings.MEDIA_ROOT, signed_pdf_rel_path)
            os.makedirs(os.path.dirname(signed_pdf_full_path), exist_ok=True)

            qr_service.embed_qr_in_pdf(
                full_temp_path, qr_full_path, signed_pdf_full_path,
                page_indices=page_indices,
                position=position,
            )
            signed_doc.pdf_file = signed_pdf_rel_path
            signed_doc.save()

            messages.success(
                request,
                f"✅ Document « {signed_doc.filename} » signé avec succès (v{version}). "
                f"QR Code ajouté sur {len(page_indices)} page(s). "
                f"Une paire de clés a été générée automatiquement."
            )
            return redirect('doc_detail_version', doc_id=signed_doc.document_id, version=signed_doc.version)

        except IntegrityError:
            messages.error(
                request,
                f"La version « {version} » existe déjà pour ce document. "
                "Veuillez incrémenter le numéro de version."
            )
        except Exception as e:
            messages.error(request, f"Erreur lors de la signature : {str(e)}")
        finally:
            if os.path.exists(full_temp_path):
                os.remove(full_temp_path)

        return redirect('sign_document')

    return render(request, 'sign_doc.html', {'form': QRPlacementForm()})


# ─────────────────────────────────────────────
# Vérification via QR Code (scan téléphone)
# ─────────────────────────────────────────────

def verify_qr(request, document_id):
    """
    Déclenché par le scan du QR Code.
    URL : /verify/<uuid>/
    Aucun upload requis : toute la vérification se fait depuis la base.
    """
    result_data = crypto_service.verify_document_by_id(str(document_id))

    if result_data['result'] == 'INVALID' and 'introuvable' in result_data.get('reason', ''):
        return render(request, 'verify_result.html', {
            'result': 'INVALID',
            'reason': result_data['reason'],
            'doc': None,
        })

    doc = result_data.get('document')
    history = SignedDocument.objects.filter(document_id=document_id).order_by('-timestamp')

    context = {
        'doc': doc,
        'history': history,
        'result': result_data['result'],
        'reason': result_data['reason'],
        'latest_doc': result_data.get('latest_doc'),
        'is_latest': result_data['result'] != 'OBSOLETE',
        'latest_version': result_data.get('latest_doc').version if result_data.get('latest_doc') else None,
    }
    return render(request, 'verify_result.html', context)


# ─────────────────────────────────────────────
# Liste & détail des documents
# ─────────────────────────────────────────────

def document_list(request):
    docs = SignedDocument.objects.all().order_by('-timestamp')
    return render(request, 'doc_list.html', {'docs': docs})


def document_detail(request, doc_id, version=None):
    if version:
        doc = get_object_or_404(SignedDocument, document_id=doc_id, version=version)
    else:
        doc = get_object_or_404(SignedDocument, document_id=doc_id, is_latest=True)

    history = SignedDocument.objects.filter(document_id=doc_id).order_by('-timestamp')
    latest = history.first()

    context = {
        'doc': doc,
        'history': history,
        'is_latest': (doc.version == latest.version),
        'latest_version': latest.version,
    }
    return render(request, 'doc_detail.html', context)


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
def api_verify(request, document_id):
    result = crypto_service.verify_document_by_id(str(document_id))
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