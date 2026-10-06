import os
import re
from io import BytesIO
from typing import List, Optional, Tuple

import qrcode
from qrcode.constants import ERROR_CORRECT_H
from PIL import Image
from reportlab.pdfgen import canvas
from PyPDF2 import PdfReader, PdfWriter
from django.conf import settings

from urllib.parse import quote

QR_SIZE = 120   # unchanged
MARGIN = 20     # unchanged

POSITIONS = ('top_left', 'top_right', 'bottom_left', 'bottom_right', 'center')


# ─────────────────────────────────────────────
# Page selection helpers
# ─────────────────────────────────────────────

def get_pdf_page_count(pdf_path: str) -> int:
    return len(PdfReader(pdf_path).pages)


def parse_page_numbers(raw: str) -> List[int]:
    """
    "1, 3, 5" -> [1, 3, 5] (1-based, sorted, duplicates removed).
    Raises ValueError with a user-friendly message.
    """
    raw = (raw or '').strip()
    if not raw:
        raise ValueError("Veuillez saisir au moins un numéro de page (ex. 1, 3, 5).")

    numbers = set()
    for token in re.split(r'[,;\s]+', raw):
        if not token:
            continue
        if not re.fullmatch(r'[0-9]+', token):
            raise ValueError(f"« {token} » n'est pas un numéro de page valide.")
        number = int(token)
        if number < 1:
            raise ValueError("Les numéros de page commencent à 1.")
        numbers.add(number)       # a set: "1, 2, 2, 3" -> {1, 2, 3}

    if not numbers:
        raise ValueError("Veuillez saisir au moins un numéro de page (ex. 1, 3, 5).")
    return sorted(numbers)


def resolve_pages(mode: str, raw_specific: str, total_pages: int) -> List[int]:
    """Return the 0-based page indices to stamp, validated against the PDF."""
    if total_pages < 1:
        raise ValueError("Le PDF ne contient aucune page.")
    if mode == 'first':
        return [0]
    if mode == 'last':
        return [total_pages - 1]
    if mode == 'all':
        return list(range(total_pages))
    if mode == 'specific':
        numbers = parse_page_numbers(raw_specific)
        missing = [n for n in numbers if n > total_pages]
        if missing:
            pages = ', '.join(str(n) for n in missing)
            label = "La page" if len(missing) == 1 else "Les pages"
            raise ValueError(
                f"{label} {pages} n'existe{'nt' if len(missing) > 1 else ''} pas : "
                f"le PDF contient {total_pages} page(s)."
            )
        return [n - 1 for n in numbers]
    raise ValueError("Mode de sélection de pages inconnu.")


# ─────────────────────────────────────────────
# Position helper (works in the page as the user SEES it)
# ─────────────────────────────────────────────

def compute_qr_origin(view_w: float, view_h: float, size: float,
                      margin: float, position: str) -> Tuple[float, float]:
    """Lower-left corner of the QR in visual coordinates (origin bottom-left)."""
    left = margin
    right = view_w - margin - size
    bottom = margin
    top = view_h - margin - size
    return {
        'top_left': (left, top),
        'top_right': (right, top),
        'bottom_left': (left, bottom),
        'bottom_right': (right, bottom),
        'center': ((view_w - size) / 2, (view_h - size) / 2),
    }[position]


class QRService:
    """Generates QR codes and embeds them into PDFs."""

    def _create_qr_image(self, data: str, box_size: int = 10) -> Image.Image:
        qr = qrcode.QRCode(
            version=None,
            error_correction=ERROR_CORRECT_H,
            box_size=box_size,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white")

    def generate_verification_qr(self, document_id: str, version: str, output_path: str) -> str:
        site_url = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')
        verify_url = f"{site_url}/verify/{document_id}/{quote(str(version), safe='')}/"
        img = self._create_qr_image(verify_url)
        img.save(output_path)
        return output_path

    def _stamp_page(self, page, qr_image_path: str, position: str) -> None:
        """Draw the QR on one page, honouring mediabox offset and /Rotate."""
        box = page.mediabox
        left, bottom = float(box.left), float(box.bottom)
        W, H = float(box.width), float(box.height)          # unrotated size
        rotation = int(page['/Rotate']) % 360 if '/Rotate' in page else 0

        # Size of the page as displayed
        view_w, view_h = (H, W) if rotation in (90, 270) else (W, H)

        # Keep the existing size; only shrink on tiny pages so it always fits
        shortest = min(view_w, view_h)
        size = min(QR_SIZE, shortest / 2)
        margin = min(MARGIN, (shortest - size) / 2)

        vx, vy = compute_qr_origin(view_w, view_h, size, margin, position)

        # Convert visual position to the page's raw coordinate space and
        # counter-rotate the image so it looks upright when displayed.
        if rotation == 90:
            px, py, angle = W - vy, vx, 90
        elif rotation == 180:
            px, py, angle = W - vx, H - vy, 180
        elif rotation == 270:
            px, py, angle = vy, H - vx, -90
        else:
            px, py, angle = vx, vy, 0

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=(left + W, bottom + H))
        c.saveState()
        c.translate(left + px, bottom + py)
        c.rotate(angle)
        c.drawImage(qr_image_path, 0, 0, width=size, height=size)
        c.restoreState()
        c.save()
        buf.seek(0)

        page.merge_page(PdfReader(buf).pages[0])

    def embed_qr_in_pdf(
        self,
        pdf_path: str,
        qr_image_path: str,
        output_path: str,
        page_indices: Optional[List[int]] = None,   # 0-based; None = all pages
        position: str = 'bottom_left',
    ) -> str:
        """Embed the QR on the selected pages (default: all, bottom-left)."""
        if position not in POSITIONS:
            raise ValueError(f"Position inconnue : {position}")

        reader = PdfReader(pdf_path)
        writer = PdfWriter()
        targets = set(range(len(reader.pages))) if page_indices is None else set(page_indices)

        for i, page in enumerate(reader.pages):
            if i in targets:
                self._stamp_page(page, qr_image_path, position)
            writer.add_page(page)

        if reader.metadata:
            writer.add_metadata(reader.metadata)

        with open(output_path, 'wb') as f:
            writer.write(f)

        return output_path