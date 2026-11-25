# -*- coding: utf-8 -*-
"""Module export : revue de presse au format Markdown (.md)."""

from datetime import datetime

from full_info import ExporterModule, fmt_date, slugify


class MarkdownExport(ExporterModule):
    name = "export_markdown"
    label = "Markdown (.md)"
    description = "Exporte l'enquête affichée sous forme de revue de presse Markdown."
    version = "1.0"
    author = "Full Info"
    extension = ".md"

    def export(self, record, articles, out_dir):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"{slugify(record.label)}-{stamp}{self.extension}"

        lines = [f"# Full Info — {record.label}", "",
                 f"*{len(articles)} article(s), du plus récent au plus ancien*", ""]
        for art in articles:
            by = f"Par {art.author}" if art.author else "Auteur non indiqué"
            lines += [
                f"## {art.title}",
                f"**{art.source}** · {by} · {fmt_date(art.published)}",
                "",
                art.summary,
                "",
                f"Source : <{art.url}>",
                "",
                "---",
                "",
            ]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
