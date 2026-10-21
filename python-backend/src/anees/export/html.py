"""HTML Export for reviewed text"""

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from anees.models import ExportOptions

logger = logging.getLogger("anees.export.html")

_AUDIO_KEYS = ("audio_path", "audio_url", "mp3_path", "audio")


class HTMLExporter:
    def __init__(self, template_dir: Path | None = None):
        self.template_dir = template_dir or Path(__file__).parent / "templates"
        self.template_dir.mkdir(exist_ok=True)

        template_file = self.template_dir / "document.html"
        marker = "<!-- anees-template:v2 -->"
        if not template_file.exists() or marker not in template_file.read_text(encoding="utf-8"):
            self._create_default_template(template_file)

        self.env = Environment(
            loader=FileSystemLoader(self.template_dir),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def _create_default_template(self, path: Path) -> None:
        template = """<!-- anees-template:v2 -->
<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ 'rtl' if rtl else 'ltr' }}" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ metadata.title|default('Anees Document') }}</title>
    <style>
        :root { --bg:#f8f9ff; --fg:#0b1c30; --card:#ffffff; --accent:#002741; --muted:#64748b; --border:#e2e8f0; }
        @media (prefers-color-scheme: dark) { :root { --bg:#0b1c30; --fg:#e2e8f0; --card:#1e293b; --accent:#93c5fd; --muted:#94a3b8; --border:#334155; } }
        html[data-theme="dark"] { --bg: #0b1c30; --fg: #e2e8f0; --card: #1e293b; --accent: #93c5fd; --muted: #94a3b8; --border: #334155; }
        html[data-theme="light"] { --bg: #f8f9ff; --fg: #0b1c30; --card: #ffffff; --accent: #002741; --muted: #64748b; --border: #e2e8f0; }
        * { box-sizing: border-box; }
        html { scroll-behavior: smooth; }
        body { font-family: 'IBM Plex Sans Arabic', 'Noto Sans Arabic', system-ui, sans-serif; line-height: 1.8; max-width: 900px; margin: 0 auto; padding: 2rem; background: var(--bg); color: var(--fg); direction: {{ 'rtl' if rtl else 'ltr' }}; }
        nav.toc { position: fixed; top: 1rem; right: 1rem; background: var(--card); padding: 1rem; border-radius: 8px; max-height: 80vh; overflow-y: auto; z-index: 10; }
        nav.toc ul { list-style: none; padding: 0; margin: 0; }
        nav.toc li { margin: 0.4rem 0; }
        nav.toc a { color: var(--accent); text-decoration: none; font-weight: 500; }
        section.chapter { margin: 2rem 0; padding: 1.5rem; background: var(--card); border-radius: 12px; scroll-margin-top: 1rem; }
        .segment { margin: 0.8rem 0; padding: 0.4rem 0; }
        .segment.arabic { direction: rtl; text-align: justify; }
        .segment.english { direction: ltr; text-align: left; }
        .segment.mixed { direction: rtl; text-align: justify; }
        .tashkeel { font-size: 1.1em; }
        .low-confidence { border-bottom: 2px solid #b45309; background: #cd7f32; cursor: help; /* bronze */ }
        .tashkeel-toggle, .theme-toggle { position: fixed; bottom: 1rem; z-index: 20; padding: 0.5rem 1rem; border: none; border-radius: 4px; cursor: pointer; color: #fff; }
        .tashkeel-toggle { left: 1rem; background: #006a61; }
        .theme-toggle { right: 1rem; background: var(--accent); }
        footer.export-info { margin-top: 2rem; padding: 1rem; background: var(--card); border-radius: 8px; font-size: 0.85rem; color: var(--muted); }
        @media (max-width: 1024px) { body { padding: 1.5rem; } nav.toc { max-height: 60vh; } }
        @media (max-width: 768px) { nav.toc { position: static; max-height: none; margin-bottom: 1rem; } body { padding: 1rem; } }
        @media print { nav.toc, .tashkeel-toggle, .theme-toggle, .audio-embed { display: none; } body { background: #fff; color: #000; max-width: none; } section.chapter { box-shadow: none; border: 1px solid #ccc; } }
    </style>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@300;400;500;600;700&display=swap" rel="stylesheet">
</head>
<body>
    <nav class="toc" aria-label="Table of Contents">
        <h3 style="margin-top:0; color:var(--accent);">Contents</h3>
        <ul>
            {% for chunk in chunks %}
            <li><a href="#chunk-{{ loop.index0 }}">Pages {{ chunk.page_range[0] }}–{{ chunk.page_range[1] }}</a></li>
            {% endfor %}
        </ul>
    </nav>
    <button class="tashkeel-toggle" type="button">Toggle Tashkeel</button>
    <button class="theme-toggle" type="button">Toggle Theme</button>
    {% if audio_path %}
    <div class="audio-embed"><audio controls src="{{ audio_path }}"></audio></div>
    {% endif %}
    <header class="document-header">
        <h1 class="document-title">{{ metadata.title|default('Anees Document') }}</h1>
        <div class="document-meta">
            {{ metadata.author|default('') }}
            {% if metadata.language or metadata.lang %} • {{ metadata.language|default(metadata.lang) }}{% endif %}
        </div>
    </header>
    <main>
        <article>
            {% for chunk in chunks %}
            <section class="chapter" id="chunk-{{ loop.index0 }}" dir="{{ 'rtl' if rtl else 'ltr' }}">
                <h2 class="chapter-title">Pages {{ chunk.page_range[0] }}–{{ chunk.page_range[1] }}</h2>
                <div class="page-marker">Pages {{ chunk.page_range[0] }}–{{ chunk.page_range[1] }}</div>
                {% for segment in chunk.segments %}
                <div class="segment {{ segment.lang }}{% if segment.confidence|default(1.0) < 0.8 %} low-confidence{% endif %}{% if include_tashkeel %} tashkeel{% endif %}" dir="{% if segment.lang in ['ar', 'mixed'] %}rtl{% else %}ltr{% endif %}" data-text="{{ segment.text }}" data-page="{{ segment.page }}" data-confidence="{{ segment.confidence|default(1.0) }}">
                    {{ segment.text }}
                </div>
                {% endfor %}
            </section>
            {% endfor %}
        </article>
    </main>
    <footer class="export-info">
        Exported by <strong>Anees</strong> on {{ export_date }}
        {% if include_tashkeel %} • Tashkeel enabled{% endif %}
    </footer>
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            var tashkeelToggle = document.querySelector('.tashkeel-toggle');
            var diacritics = /[\u064b-\u065f\u0670]/g;
            function stripDiacritics(value) { return (value || '').replace(diacritics, ''); }
            var showTashkeel = true;
            if (tashkeelToggle) {
                tashkeelToggle.addEventListener('click', function() {
                    showTashkeel = !showTashkeel;
                    document.querySelectorAll('.segment').forEach(function(seg) {
                        var original = seg.getAttribute('data-text') || seg.textContent;
                        seg.textContent = showTashkeel ? original : stripDiacritics(original);
                        seg.classList.toggle('tashkeel', showTashkeel);
                    });
                });
            }
            var themeToggle = document.querySelector('.theme-toggle');
            if (themeToggle) {
                themeToggle.addEventListener('click', function() {
                    var html = document.documentElement;
                    var current = html.getAttribute('data-theme') || 'light';
                    html.setAttribute('data-theme', current === 'light' ? 'dark' : 'light');
                });
            }
        });
    </script>
</body>
</html>"""
        path.write_text(template, encoding="utf-8")

    def _normalize_options(self, options: Any) -> dict[str, Any]:
        if isinstance(options, ExportOptions):
            opts = options.model_dump()
        elif isinstance(options, dict):
            opts = dict(options)
        else:
            opts = {}
        if "include_tashkeel" not in opts and "tashkeel" in opts:
            opts["include_tashkeel"] = bool(opts["tashkeel"])
        if "include_tashkeel" not in opts:
            opts["include_tashkeel"] = True
        if "tashkeel" not in opts:
            opts["tashkeel"] = bool(opts["include_tashkeel"])
        if "rtl" not in opts:
            opts["rtl"] = True
        return opts

    def _audio_path(self, metadata: Any, options: Any) -> str | None:
        candidates: list[Any] = []
        if isinstance(metadata, dict):
            for key in _AUDIO_KEYS:
                if metadata.get(key):
                    candidates.append(metadata.get(key))
        if isinstance(options, dict):
            for key in _AUDIO_KEYS:
                if options.get(key):
                    candidates.append(options.get(key))
        if isinstance(options, ExportOptions):
            for key in _AUDIO_KEYS:
                value = getattr(options, key, None)
                if value:
                    candidates.append(value)
        return str(candidates[0]) if candidates else None

    def export(
        self,
        chunks: list[Any],
        metadata: dict[str, Any] | None = None,
        options: Any = None,
    ) -> str:
        metadata = metadata or {}
        chunks = chunks or []
        opts = self._normalize_options(options)

        output_path = (
            Path.home() / "Documents" / "Anees" / "exports" / f"export_{uuid.uuid4().hex[:8]}.html"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        template = self.env.get_template("document.html")

        html = template.render(
            chunks=chunks,
            metadata=metadata,
            rtl=opts["rtl"],
            lang="ar" if opts["rtl"] else "en",
            include_tashkeel=opts["include_tashkeel"],
            audio_path=self._audio_path(metadata, options),
            export_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

        output_path.write_text(html, encoding="utf-8")
        logger.info(f"HTML exported to {output_path}")
        return str(output_path)
