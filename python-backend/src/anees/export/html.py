"""HTML Export for reviewed text"""

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path
from typing import List, Dict, Any, Optional
import uuid
from anees.models import ChunkData, ExportOptions
import logging

logger = logging.getLogger("anees.export.html")


class HTMLExporter:
    def __init__(self, template_dir: Optional[Path] = None):
        self.template_dir = template_dir or Path(__file__).parent / "templates"
        self.template_dir.mkdir(exist_ok=True)

        # Create default template if not exists
        template_file = self.template_dir / "document.html"
        if not template_file.exists():
            self._create_default_template(template_file)

        self.env = Environment(
            loader=FileSystemLoader(self.template_dir),
            autoescape=select_autoescape(['html', 'xml']),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def _create_default_template(self, path: Path):
        template = """<!DOCTYPE html>
<html dir="{{ rtl|yesno('rtl,ltr') }}" lang="{{ lang }}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ metadata.title|default('Anees Document') }}</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: 'IBM Plex Sans Arabic', 'Noto Sans Arabic', system-ui, sans-serif;
            line-height: 1.8;
            max-width: 800px;
            margin: 0 auto;
            padding: 2rem;
            background: #f8f9ff;
            color: #0b1c30;
            direction: {{ rtl|yesno('rtl,ltr') }};
        }
        .document-header {
            text-align: center;
            margin-bottom: 3rem;
            padding-bottom: 1.5rem;
            border-bottom: 2px solid #e2e8f0;
        }
        .document-title {
            font-size: 2rem;
            font-weight: 700;
            color: #002741;
            margin-bottom: 0.5rem;
        }
        .document-meta {
            color: #64748b;
            font-size: 0.9rem;
        }
        .chapter {
            margin: 2.5rem 0;
            padding: 1.5rem;
            background: white;
            border-radius: 12px;
            box-shadow: 0 1px 3px rgba(15, 61, 94, 0.04);
        }
        .chapter-title {
            font-size: 1.5rem;
            font-weight: 600;
            color: #002741;
            margin-bottom: 1rem;
            padding-bottom: 0.5rem;
            border-bottom: 1px solid #e2e8f0;
        }
        .page-marker {
            font-size: 0.8rem;
            color: #64748b;
            margin-bottom: 1rem;
        }
        .segment {
            margin: 1rem 0;
            padding: 0.5rem 0;
        }
        .segment.arabic {
            direction: rtl;
            text-align: justify;
        }
        .segment.english {
            direction: ltr;
            text-align: left;
        }
        .segment.mixed {
            direction: rtl;
            text-align: justify;
        }
        .tashkeel {
            font-size: 1.1em;
        }
        .low-confidence {
            border-bottom: 2px solid #b45309;
            background: rgba(180, 83, 9, 0.05);
            cursor: help;
        }
        .toc {
            position: fixed;
            right: 2rem;
            top: 50%;
            transform: translateY(-50%);
            background: white;
            padding: 1rem;
            border-radius: 8px;
            box-shadow: 0 4px 14px rgba(15, 61, 94, 0.07);
            max-height: 70vh;
            overflow-y: auto;
        }
        .toc ul { list-style: none; padding: 0; margin: 0; }
        .toc li { margin: 0.5rem 0; }
        .toc a {
            color: #002741;
            text-decoration: none;
            font-weight: 500;
        }
        .toc a:hover { color: #006a61; }
        @media (max-width: 768px) {
            .toc { display: none; }
            body { padding: 1rem; }
        }
        .export-info {
            margin-top: 3rem;
            padding: 1rem;
            background: #eff4ff;
            border-radius: 8px;
            font-size: 0.85rem;
            color: #42474e;
        }
    </style>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@300;400;500;600;700&display=swap" rel="stylesheet">
</head>
<body>
    <nav class="toc" aria-label="Table of Contents">
        <h3 style="margin-top:0; color:#002741;">فهرس المحتوى</h3>
        <ul>
            {% for chunk in chunks %}
            <li><a href="#chunk-{{ loop.index0 }}">{{ chunk.page_range[0 ]}}–{{ chunk.page_range[1 ]}}</a></li>
            {% endfor %}
        </ul>
    </nav>

    <header class="document-header">
        <h1 class="document-title">{{ metadata.title|default('مستند أنيس') }}</h1>
        <div class="document-meta">
            {{ metadata.author|default('') }} • {{ metadata.pages|default('') }} صفحات • {{ metadata.lang|default('ar') }}
        </div>
    </header>

    <main>
        {% for chunk in chunks %}
        <section class="chapter" id="chunk-{{ loop.index0 }}">
            <div class="page-marker">الصفحات {{ chunk.page_range[0 ]}}–{{ chunk.page_range[1 ]}}</div>
            {% for segment in chunk.segments %}
            <div class="segment {{ segment.lang }}" data-page="{{ segment.page }}" data-confidence="{{ segment.confidence }}">
                {{ segment.text|safe }}
            </div>
            {% endfor %}
        </section>
        {% endfor %}
    </main>

    <footer class="export-info">
        تم تصدير هذا المستند بواسطة <strong>Anees</strong> في {{ export_date }}
        {% if options.include_tashkeel %}• التشكيل مفعل{% endif %}
    </footer>

    <script>
        // Tashkeel toggle
        document.addEventListener('DOMContentLoaded', function() {
            const segments = document.querySelectorAll('.segment.arabic');
            const toggle = document.createElement('button');
            toggle.textContent = 'تبديل التشكيل';
            toggle.style.cssText = 'position:fixed;bottom:1rem;left:1rem;z-index:100;padding:0.5rem 1rem;background:#006a61;color:white;border:none;border-radius:4px;cursor:pointer;';
            document.body.appendChild(toggle);

            let showTashkeel = true;
            toggle.addEventListener('click', function() {
                showTashkeel = !showTashkeel;
                segments.forEach(function(seg) {
                    seg.classList.toggle('tashkeel', showTashkeel);
                });
            });
        });
    </script>
</body>
</html>"""
        path.write_text(template, encoding='utf-8')

    def export(
        self,
        chunks: List[ChunkData],
        metadata: Dict[str, Any],
        options: Optional[ExportOptions] = None
    ) -> str:
        options = options or ExportOptions(
            format="html",
            include_tashkeel=True,
            rtl=True,
            chapter_navigation=True
        )

        template = self.env.get_template("document.html")

        output_path = Path.home() / "Documents" / "Anees" / "exports" / f"export_{uuid.uuid4().hex[:8]}.html"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        from datetime import datetime
        html = template.render(
            chunks=chunks,
            metadata=metadata,
            options=options,
            rtl=options.rtl,
            lang="ar" if options.rtl else "en",
            export_date=datetime.now().strftime("%Y-%m-%d %H:%M")
        )

        output_path.write_text(html, encoding='utf-8')
        logger.info(f"HTML exported to {output_path}")
        return str(output_path)