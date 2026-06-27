from django.contrib import admin
from .models import SigningKey, SignedDocument, AuditLog


@admin.register(SigningKey)
class SigningKeyAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name',)
    # La clé privée est visible dans l'admin mais masquée en lecture seule
    readonly_fields = ('created_at',)


@admin.register(SignedDocument)
class SignedDocumentAdmin(admin.ModelAdmin):
    list_display = ('filename', 'version', 'signer_name', 'timestamp', 'is_latest')
    list_filter = ('is_latest', 'signer_name')
    search_fields = ('filename', 'signer_name', 'document_id')
    readonly_fields = ('document_id', 'timestamp', 'file_hash', 'signature', 'public_key')


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('event', 'document_id', 'timestamp', 'result')
    list_filter = ('event', 'result')
    search_fields = ('document_id',)
    readonly_fields = ('timestamp',)
