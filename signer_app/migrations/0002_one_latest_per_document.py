from django.db import migrations, models


def normalize_latest(apps, schema_editor):
    SD = apps.get_model('signer_app', 'SignedDocument')
    for did in set(SD.objects.values_list('document_id', flat=True)):
        rows = SD.objects.filter(document_id=did).order_by('-timestamp', '-id')
        keep = rows.filter(is_latest=True).first() or rows.first()
        rows.exclude(pk=keep.pk).update(is_latest=False)
        if not keep.is_latest:
            SD.objects.filter(pk=keep.pk).update(is_latest=True)


class Migration(migrations.Migration):
    dependencies = [('signer_app', '0001_initial')]

    operations = [
        migrations.RunPython(normalize_latest, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='signeddocument',
            constraint=models.UniqueConstraint(
                fields=['document_id'],
                condition=models.Q(is_latest=True),
                name='one_latest_per_document',
            ),
        ),
    ]