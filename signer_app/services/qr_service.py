import os
import tempfile
from pathlib import Path
from typing import Optional

import qrcode
from qrcode.constants import ERROR_CORRECT_H
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from PyPDF2 import PdfReader, PdfWriter
from django.conf import settings

class QRService:
    """Generates QR codes and embeds them into PDFs."""
    
    def _create_qr_image(self, data: str, box_size: int = 10) -> Image.Image:
        """Create a QR code image from data."""
        qr = qrcode.QRCode(
            version=None,
            error_correction=ERROR_CORRECT_H,
            box_size=box_size,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white")
    
    def generate_verification_qr(self, document_id: str, output_path: str) -> str:
        """
        Generate a QR code containing the verification URL.
        URL format: {SITE_URL}/verify/<document_id>/
        """
        site_url = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')
        verify_url = f"{site_url}/verify/{document_id}/"
        
        img = self._create_qr_image(verify_url)
        img.save(output_path)
        return output_path
    
    def embed_qr_in_pdf(self, pdf_path: str, qr_image_path: str, output_path: str) -> str:
        """Embed QR code into ALL pages of a PDF at the bottom-left corner."""
        reader = PdfReader(pdf_path)
        writer = PdfWriter()
        
        qr_size = 120
        margin = 20
        
        for i, page in enumerate(reader.pages):
            page_width = float(page.mediabox.width)
            page_height = float(page.mediabox.height)
            
            x_pos = margin
            y_pos = margin
            
            packet = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
            packet.close()
            
            c = canvas.Canvas(packet.name, pagesize=(page_width, page_height))
            c.drawImage(qr_image_path, x_pos, y_pos, width=qr_size, height=qr_size)
            c.save()
            
            overlay_reader = PdfReader(packet.name)
            overlay_page = overlay_reader.pages[0]
            page.merge_page(overlay_page)
            writer.add_page(page)
            
            os.unlink(packet.name)
        
        if reader.metadata:
            writer.add_metadata(reader.metadata)
        
        with open(output_path, 'wb') as f:
            writer.write(f)
        
        return output_path
