# -*- coding: utf-8 -*-
"""Module export : tableau CSV (ouvrable dans Excel / LibreOffice)."""

import csv
from datetime import datetime

from full_info import ExporterModule, slugify


class CsvExport(ExporterModule):
    name = "export_csv"
    label = "Tableur (.csv)"
    description = "Exporte l'enquête affichée en CSV : titre, source, auteur, date, résumé, lien."
    version = "1.0"
    author = "Full Info"
    extension = ".csv"

    def export(self, record, articles, out_dir):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"{slugify(record.label)}-{stamp}{self.extension}"
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:  # utf-8-sig : accents OK dans Excel
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(["titre", "source", "auteur", "date_utc", "resume", "lien"])
            for a in articles:
                writer.writerow([
                    a.title, a.source, a.author,
                    a.published.isoformat() if a.published else "",
                    a.summary, a.url,
                ])
        return path
