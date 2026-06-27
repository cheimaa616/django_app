from rest_framework import serializers
from .models import SignedDocument, AuditLog

class SignedDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SignedDocument
        fields = '__all__'

class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = '__all__'


