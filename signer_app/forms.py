from django import forms

from .services.qr_service import parse_page_numbers


class QRPlacementForm(forms.Form):
    PAGE_MODE_CHOICES = [
        ('first', 'Première page'),
        ('last', 'Dernière page'),
        ('specific', 'Pages spécifiques'),
        ('all', 'Toutes les pages'),
    ]
    POSITION_CHOICES = [
        ('top_left', 'En haut à gauche'),
        ('top_right', 'En haut à droite'),
        ('bottom_left', 'En bas à gauche'),
        ('bottom_right', 'En bas à droite'),
        ('center', 'Au centre'),
    ]

    page_mode = forms.ChoiceField(
        label="Sur quelles pages le QR Code doit-il apparaître ?",
        choices=PAGE_MODE_CHOICES,
        initial='all',
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
    )
    specific_pages = forms.CharField(
        label="Numéros de pages",
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex. 1, 3, 5'}),
    )
    position = forms.ChoiceField(
        label="Où le QR Code doit-il être placé ?",
        choices=POSITION_CHOICES,
        initial='bottom_left',
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('page_mode') == 'specific':
            try:
                # Catches empty input and non-numeric / zero values.
                # Whether pages exist in the PDF is checked later in the view.
                parse_page_numbers(cleaned.get('specific_pages', ''))
            except ValueError as exc:
                self.add_error('specific_pages', str(exc))
        return cleaned