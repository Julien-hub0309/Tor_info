# -*- coding: utf-8 -*-
"""
MODÈLE DE MODULE FULL INFO

Comment ajouter un module :
  1. Copie ce fichier dans le dossier modules/ SOUS UN AUTRE NOM, sans le « _ » initial
     (les fichiers commençant par « _ » sont ignorés), par exemple  mon_rss.py
  2. Garde UNE des deux classes ci-dessous (ou les deux), renomme-les et change `name`
  3. Lance l'application (ou clique sur « Recharger les modules » dans l'onglet MODULES)

Règles :
  * `name` doit être unique : c'est l'identifiant du module.
  * Tout accès réseau passe OBLIGATOIREMENT par l'objet `tor` reçu en paramètre
    (tor.get(url, ...) / tor.post(url, ...)) : n'utilise jamais requests directement,
    sinon tu sortirais de Tor.
  * Un module qui plante est simplement signalé dans l'onglet MODULES ; il ne bloque pas le reste.
"""

from full_info import ExporterModule, SourceModule, make_candidate, parse_date, slugify  # noqa: F401


# --------------------------------------------------------------------------- #
# 1) Un module SOURCE : fournit des articles candidats
# --------------------------------------------------------------------------- #
class MaSource(SourceModule):
    name = "ma_source"                       # identifiant unique
    label = "Ma source"                      # nom affiché dans l'interface
    description = "Ce que fait cette source."
    version = "1.0"
    author = "Moi"
    default_enabled = True                   # activée par défaut ?

    def search(self, tor, query, market, limit):
        """
        query  : texte recherché (thème + question + pays)
        market : ex. "fr-FR" (peut être vide pour « monde »)
        limit  : nombre maximum de candidats utiles

        Doit renvoyer une liste de make_candidate(...). Seuls `title` et `url` sont obligatoires ;
        `published` (datetime, via parse_date) permet le classement par date sans attendre
        la lecture de la page.
        """
        resp = tor.get("https://exemple.org/rss", params={"q": query})
        resp.raise_for_status()
        # ... analyse de la réponse ...
        return [
            make_candidate(
                title="Titre de l'article",
                url="https://exemple.org/article",
                source="Exemple.org",
                published=parse_date("2026-11-23T08:00:00Z"),
                snippet="Court extrait (utilisé si la page n'est pas lisible).",
            )
        ]


# --------------------------------------------------------------------------- #
# 2) Un module EXPORT : écrit l'enquête affichée dans un fichier
# --------------------------------------------------------------------------- #
class MonExport(ExporterModule):
    name = "mon_export"
    label = "Mon format (.txt)"
    description = "Exporte l'enquête affichée en texte brut."
    version = "1.0"
    author = "Moi"
    extension = ".txt"

    def export(self, record, articles, out_dir):
        """record : enquête (record.label, record.query...) ; articles : liste d'Article
        (title, url, source, author, published, summary) ; out_dir : dossier de sortie (Path).
        Doit renvoyer le chemin du fichier créé."""
        path = out_dir / f"{slugify(record.label)}{self.extension}"
        path.write_text("\n\n".join(f"{a.title}\n{a.url}" for a in articles), encoding="utf-8")
        return path
