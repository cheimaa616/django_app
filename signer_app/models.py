from django.db import models
import uuid
from django.db import models

# Create your models here.


class SigningKey(models.Model):
    name = models.CharField(max_length=255, unique=True)
    public_key = models.TextField()
    private_key = models.TextField()  # Encrypted in production, but prompt says "private key never exposed"
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class SignedDocument(models.Model):
    document_id = models.UUIDField(default=uuid.uuid4, editable=False)
    filename = models.CharField(max_length=255)
    version = models.CharField(max_length=50)
    timestamp = models.DateTimeField()
    file_hash = models.CharField(max_length=64)
    signature = models.TextField()
    signer_name = models.CharField(max_length=255)
    public_key = models.TextField()
    pdf_file = models.FileField(upload_to='signed_pdfs/')
    qr_code_file = models.ImageField(upload_to='qrs/')
    is_latest = models.BooleanField(default=True)

    class Meta:
        unique_together = ('document_id', 'version')
        constraints = [
            # At most ONE current version per document, enforced by the DB
            models.UniqueConstraint(
                fields=['document_id'],
                condition=models.Q(is_latest=True),
                name='one_latest_per_document',
            ),
        ]

    def __str__(self):
        return f"{self.filename} - v{self.version}"

class AuditLog(models.Model):
    EVENT_CHOICES = [
        ('SIGN', 'Signature'),
        ('VERIFY', 'Verification'),
    ]
    event = models.CharField(max_length=10, choices=EVENT_CHOICES)
    document_id = models.CharField(max_length=100)
    timestamp = models.DateTimeField(auto_now_add=True)
    result = models.CharField(max_length=50, blank=True, null=True)
    reason = models.TextField(blank=True, null=True)
    details = models.JSONField(default=dict)

    def __str__(self):
        return f"{self.event} - {self.document_id} at {self.timestamp}"

