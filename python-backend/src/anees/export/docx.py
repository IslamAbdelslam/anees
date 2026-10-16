"""DOCX Export for reviewed text"""

from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_ORIENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from pathlib import Path
from typing import List, Dict, Any, Optional
import uuid
from anees.models import ChunkData, ExportOptions
import logging

logger = logging.getLogger("anees.export.docx")


class DOCXExporter:
    def __init__(self):
        pass

    def _set_rtl(self, paragraph):
        """Set paragraph to RTL"""
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        pPr = paragraph._p.get_or_add_pPr()
        bidi = OxmlElement('w:bidi')
        pPr.append(bidi)

    def _set_ltr(self, paragraph):
        """Set paragraph to LTR"""
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        pPr = paragraph._p.get_or_add_pPr()
        bidi = OxmlElement('w:bidi')
        bidi.set(qn('w:val'), '0')
        pPr.append(bidi)

    def _add_arabic_run(self, paragraph, text: str, bold=False, italic=False):
        """Add Arabic text run with proper formatting"""
        run = paragraph.add_run(text)
        run.font.name = 'IBM Plex Sans Arabic'
        run.font.size = Pt(14)
        run.font.rtl = True
        run.bold = bold
        run.italic = italic
        # Set complex script font
        rPr = run._r.get_or_add_rPr()
        rFonts = OxmlElement('w:rFonts')
        rFonts.set(qn('w:cs'), 'IBM Plex Sans Arabic')
        rFonts.set(qn('w:ascii'), 'IBM Plex Sans Arabic')
        rFonts.set(qn('w:hAnsi'), 'IBM Plex Sans Arabic')
        rPr.append(rFonts)
        return run

    def _add_english_run(self, paragraph, text: str, bold=False, italic=False):
        """Add English text run"""
        run = paragraph.add_run(text)
        run.font.name = 'IBM Plex Sans Arabic'
        run.font.size = Pt(14)
        run.bold = bold
        run.italic = italic
        return run

    def export(
        self,
        chunks: List[ChunkData],
        metadata: Dict[str, Any],
        options: Optional[ExportOptions] = None
    ) -> str:
        options = options or ExportOptions(
            format="docx",
            include_tashkeel=True,
            rtl=True,
            chapter_navigation=True
        )

        doc = Document()

        # Set default style
        style = doc.styles['Normal']
        style.font.name = 'IBM Plex Sans Arabic'
        style.font.size = Pt(14)
        style.paragraph_format.line_spacing = 1.8
        style.paragraph_format.space_after = Pt(6)

        # Set RTL for the whole document
        for section in doc.sections:
            section.page_width = Cm(21)
            section.page_height = Cm(29.7)
            section.top_margin = Cm(2.5)
            section.bottom_margin = Cm(2.5)
            section.left_margin = Cm(2.5)
            section.right_margin = Cm(2.5)

        # Title
        title = doc.add_heading(metadata.get('title', 'مستند أنيس'), level=0)
        self._set_rtl(title)
        for run in title.runs:
            run.font.name = 'IBM Plex Sans Arabic'
            run.font.color.rgb = RGBColor(0x00, 0x27, 0x41)

        # Metadata
        meta_text = f"{metadata.get('author', '')}  •  {metadata.get('pages', '')} صفحات  •  {metadata.get('lang', 'ar')}"
        meta_para = doc.add_paragraph(meta_text)
        self._set_rtl(meta_para)
        for run in meta_para.runs:
            run.font.size = Pt(11)
            run.font.color.rgb = RGBColor(0x64, 0x74, 0x8b)

        doc.add_paragraph()  # spacer

        # Table of contents placeholder
        if options.chapter_navigation:
            toc_heading = doc.add_heading('فهرس المحتوى', level=1)
            self._set_rtl(toc_heading)
            for i, chunk in enumerate(chunks):
                toc_item = doc.add_paragraph(f'{chunk.page_range[0]}–{chunk.page_range[1]}')
                self._set_rtl(toc_item)
                toc_item.style = 'List Bullet'

        doc.add_page_break()

        # Content
        for chunk_idx, chunk in enumerate(chunks):
            # Chapter heading
            chapter_title = doc.add_heading(f'الصفحات {chunk.page_range[0]}–{chunk.page_range[1]}', level=1)
            self._set_rtl(chapter_title)

            for segment in chunk.segments:
                para = doc.add_paragraph()
                if segment.lang == 'ar' or segment.lang == 'mixed':
                    self._set_rtl(para)
                    # Add Arabic text with tashkeel support
                    self._add_arabic_run(para, segment.text)
                else:
                    self._set_ltr(para)
                    self._add_english_run(para, segment.text)

                # Mark low confidence
                if segment.confidence < 0.8:
                    for run in para.runs:
                        run.font.highlight_color = 7  # Yellow highlight

        # Export info
        doc.add_paragraph()
        export_info = doc.add_paragraph()
        self._set_rtl(export_info)
        run = export_info.add_run('تم تصدير هذا المستند بواسطة Anees')
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(0x42, 0x47, 0x4e)

        # Save
        output_path = Path.home() / "Documents" / "Anees" / "exports" / f"export_{uuid.uuid4().hex[:8]}.docx"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(output_path)
        logger.info(f"DOCX exported to {output_path}")
        return str(output_path)