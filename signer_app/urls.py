from django.urls import path
from . import views

urlpatterns = [
    # Pages principales
    path('', views.dashboard, name='dashboard'),
    path('sign/', views.sign_document, name='sign_document'),
    path('documents/', views.document_list, name='document_list'),
    path('document/<uuid:doc_id>/', views.document_detail, name='doc_detail'),
    path('document/<uuid:doc_id>/<str:version>/', views.document_detail, name='doc_detail_version'),

    # Vérification par QR Code
    # Anciens QR (sans version) -> résolus vers la version courante
    path('verify/<uuid:document_id>/', views.verify_qr, name='verify_qr'),
    # Nouveaux QR : une URL par version
    path('verify/<uuid:document_id>/<str:version>/', views.verify_qr, name='verify_qr_version'),

    # Journaux
    path('audit/', views.audit_logs, name='audit_logs'),

    # API REST
    path('api/verify/<uuid:document_id>/', views.api_verify, name='api_verify'),
    path('api/verify/<uuid:document_id>/<str:version>/', views.api_verify, name='api_verify_version'),
    path('api/document/<uuid:document_id>/', views.api_document, name='api_document'),
    path('api/latest/<uuid:document_id>/', views.api_latest, name='api_latest'),
]