from __future__ import annotations

import time
import webbrowser
from typing import Any

from rich.text import Text

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from full_info import (
    APP_NAME,
    APP_VERSION,
    MAX_ARTICLES,
    MODULES_DIR,
    PAYS,
    PERIODS,
    SearchRequest,
    domain_of,
    fmt_date,
    lang_name,
    sort_articles,
    today_long,
)

try:
    import pyperclip  # type: ignore
except ImportError:
    pyperclip = None


WORLD = "(monde)"

# Palette éditoriale sobre et reposante (accent cuivré/doré doux au lieu du rouge agressif)
PALETTES: dict[str, dict[str, str]] = {
    "clair": {
        "paper": "#f5f1e8",
        "card": "#fcfaf5",
        "elevated": "#eae4d7",
        "ink": "#1c1b18",
        "accent": "#8c3b2b",
        "muted": "#666156",
        "green": "#2b6e3f",
        "amber": "#9e621c",
        "border": "#c8c0b0",
    },
    "sombre": {
        "paper": "#131210",
        "card": "#1c1a17",
        "elevated": "#24211d",
        "ink": "#e8e3d9",
        "accent": "#c98a5b",
        "muted": "#8f897d",
        "green": "#6cc47a",
        "amber": "#d49b54",
        "border": "#36322b",
    },
}


SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class LoadingBar(Widget):
    """Barre de chargement pleine largeur, colorée selon le thème."""

    DEFAULT_CSS = """
    LoadingBar { height: 1; width: 1fr; }
    """

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.value = 0.0  # 0..1
        self.fill = "#c98a5b"
        self.empty = "#36322b"
        self.fg = "#e8e3d9"

    def set_value(self, value: float) -> None:
        self.value = max(0.0, min(1.0, value))
        self.refresh()

    def render(self) -> Text:
        pct = f" {int(self.value * 100):3d}%"
        width = max(4, self.size.width - len(pct))
        filled = int(round(width * self.value))
        out = Text()
        out.append("█" * filled, style=self.fill)
        out.append("░" * (width - filled), style=self.empty)
        out.append(pct, style=f"bold {self.fg}")
        return out


class Phase:
    """Étape de la recherche et plage de la barre globale qu'elle occupe."""

    def __init__(self, key: str, label: str, start: float, end: float):
        self.key, self.label, self.start, self.end = key, label, start, end


def build_phases(translate: bool) -> list[Phase]:
    if translate:
        spec = [("analyse", "Analyse", 0.00, 0.05), ("sources", "Sources", 0.05, 0.25),
                ("extract", "Extraction", 0.25, 0.78), ("translate", "Traduction", 0.78, 0.95),
                ("save", "Archivage", 0.95, 1.0)]
    else:
        spec = [("analyse", "Analyse", 0.00, 0.05), ("sources", "Sources", 0.05, 0.28),
                ("extract", "Extraction", 0.28, 0.95), ("save", "Archivage", 0.95, 1.0)]
    return [Phase(*x) for x in spec]


def phase_key_of(text: str) -> str:
    low = text.lower()
    if low.startswith("traduction"):
        return "translate"
    if "extraction" in low:
        return "extract"
    if low.startswith("interrogation"):
        return "sources"
    return "analyse"


def _copy_to_clipboard(text: str) -> bool:
    if pyperclip is None:
        return False
    try:
        pyperclip.copy(text)
        return True
    except Exception:
        return False


class ConfirmModal(ModalScreen[bool]):
    CSS = """
    ConfirmModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.72);
    }
    #modal-box {
        width: 64;
        height: auto;
        padding: 1 2;
        border: tall $accent;
        background: $surface;
    }
    #modal-title { text-style: bold; margin-bottom: 1; }
    #modal-msg { margin-bottom: 1; }
    #modal-btns { height: auto; align: right middle; }
    #modal-btns Button { margin-left: 1; }
    """

    def __init__(self, title: str, message: str, yes_label: str = "Confirmer", no_label: str = "Annuler"):
        super().__init__()
        self._title = title
        self._message = message
        self._yes_label = yes_label
        self._no_label = no_label

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-box"):
            yield Label(self._title, id="modal-title")
            yield Static(self._message, id="modal-msg")
            with Horizontal(id="modal-btns"):
                yield Button(self._no_label, id="btn-no", variant="default")
                yield Button(self._yes_label, id="btn-yes", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-yes")


class InfoModal(ModalScreen[None]):
    CSS = """
    InfoModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.72);
    }
    #info-box {
        width: 66;
        height: auto;
        padding: 1 2;
        border: tall $accent;
        background: $surface;
    }
    #info-title { text-style: bold; margin-bottom: 1; }
    #info-msg { margin-bottom: 1; }
    #info-actions { height: auto; align: right middle; }
    """

    def __init__(self, title: str, message: str):
        super().__init__()
        self._title = title
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="info-box"):
            yield Label(self._title, id="info-title")
            yield Static(self._message, id="info-msg")
            with Horizontal(id="info-actions"):
                yield Button("Fermer", id="btn-ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-ok":
            self.dismiss(None)


class ModuleConfigModal(ModalScreen[dict[str, Any] | None]):
    CSS = """
    ModuleConfigModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.72);
    }
    #cfg-box {
        width: 68;
        height: auto;
        padding: 1 2;
        border: tall $accent;
        background: $surface;
    }
    #cfg-title { text-style: bold; margin-bottom: 1; }
    .cfg-label { text-style: bold; margin-top: 1; }
    #cfg-actions { height: auto; align: right middle; margin-top: 2; }
    #cfg-actions Button { margin-left: 1; }
    """

    def __init__(self, mod_label: str, schema: dict[str, dict[str, Any]], current: dict[str, Any]):
        super().__init__()
        self._mod_label = mod_label
        self._schema = schema
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="cfg-box"):
            yield Label(f"Configuration · {self._mod_label}", id="cfg-title")
            for key, meta in self._schema.items():
                yield Label(meta.get("label", key), classes="cfg-label")
                val = str(self._current.get(key, meta.get("default", "")))
                yield Input(
                    value=val,
                    password=bool(meta.get("secret", False)),
                    placeholder=str(meta.get("placeholder", "")),
                    id=f"cfg-in-{key}",
                )
            with Horizontal(id="cfg-actions"):
                yield Button("Annuler", id="cfg-cancel", variant="default")
                yield Button("Enregistrer", id="cfg-save", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cfg-cancel":
            self.dismiss(None)
        elif event.button.id == "cfg-save":
            result = {key: self.query_one(f"#cfg-in-{key}", Input).value.strip() for key in self._schema}
            self.dismiss(result)


class ArticleCard(Vertical):
    """Carte d'article compacte : hauteur automatique, texte qui passe à la ligne."""

    DEFAULT_CSS = """
    ArticleCard {
        height: auto;
        width: 100%;
        padding: 0 2;
        margin-bottom: 1;
    }
    .card-top { height: 1; }
    .kicker { width: 1fr; text-style: bold; }
    .badge { text-style: bold; }
    .headline { width: 100%; height: auto; text-style: bold; }
    .meta { width: 100%; height: auto; }
    .orig-title { width: 100%; height: auto; text-style: italic; }
    .summary { width: 100%; height: auto; margin-top: 1; }
    .card-actions { height: 1; margin-top: 1; align: left middle; }
    .card-actions Input {
        width: 1fr; height: 1; border: none; padding: 0 1; margin-right: 1;
    }
    .card-actions Button {
        height: 1; min-width: 10; border: none; margin-left: 1;
    }
    """

    def __init__(self, article: Any, index: int, on_copy, on_read):
        super().__init__()
        self._article = article
        self._index = index
        self._on_copy = on_copy
        self._on_read = on_read

    def compose(self) -> ComposeResult:
        art = self._article
        src_label = (art.source or domain_of(art.url) or "SOURCE").upper()
        kicker = ("★ À LA UNE · " if self._index == 0 else f"#{self._index + 1} · ") + src_label
        score = getattr(art, "score", 0)
        rtime = getattr(art, "reading_time", 1)

        with Horizontal(classes="card-top"):
            yield Label(kicker, classes="kicker")
            badge_txt = f"PERTINENCE {score}% · ~{rtime} MIN" if score else f"~{rtime} MIN"
            yield Label(badge_txt, classes="badge")

        yield Static(art.title, classes="headline", markup=False)

        by = f"Par {art.author}" if getattr(art, "author", None) else "Auteur non indiqué"
        meta = f"{by} · {fmt_date(art.published)}"
        lang = getattr(art, "lang", None)
        if lang and lang != "fr":
            meta += f" · Traduit de : {lang_name(lang)}"
        yield Static(meta, classes="meta", markup=False)

        orig = getattr(art, "original_title", None)
        if orig:
            yield Static(f"Titre original : {orig}", classes="orig-title", markup=False)

        yield Static(art.summary or "", classes="summary", markup=False)

        with Horizontal(classes="card-actions"):
            yield Input(value=art.url or "", classes="source-url")
            yield Button("Lire", id=f"read-{self._index}", variant="primary")
            yield Button("Copier", id=f"copy-{self._index}", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == f"copy-{self._index}":
            self._on_copy(self._article.url or "")
        elif event.button.id == f"read-{self._index}":
            self._on_read(self._article)


class FullInfoTUI(App[None]):
    """Salle de rédaction FULL INFO dans le terminal."""

    TITLE = f"{APP_NAME} {APP_VERSION} — Salle de rédaction anonyme"

    BINDINGS = [
        ("ctrl+q", "quit", "Quitter"),
        ("ctrl+t", "toggle_theme", "Thème"),
        ("ctrl+r", "check_tor", "Vérifier Tor"),
        ("ctrl+f", "focus_filter", "Filtrer"),
        ("ctrl+e", "toggle_form", "Recherche"),
        ("ctrl+d", "toggle_condensed", "Condensé"),
        ("pagedown", "scroll_results('down')", "Page ↓"),
        ("pageup", "scroll_results('up')", "Page ↑"),
        ("ctrl+end", "scroll_results('end')", "Fin"),
        ("ctrl+home", "scroll_results('home')", "Début"),
        ("escape", "close_reader", "Retour"),
    ]

    CSS = """
    Screen {
        layout: vertical;
    }

    #main-body {
        height: 1fr;
        padding: 0 2;
    }

    /* En-tête compact sur une seule ligne */
    #masthead {
        height: 2;
        padding: 0 1;
        margin-top: 0;
        margin-bottom: 0;
        align: left middle;
        border-bottom: solid;
    }
    /* Boutons compacts d'1 ligne (le CSS de l'app prime sur les variantes par défaut,
       y compris au survol/focus, qui sinon rajoutent des bordures et écrasent la hauteur). */
    #masthead Button, #toolbar Button, ArticleCard Button, #btn-new-search,
    #btn-new-search:hover, #btn-new-search:focus,
    #masthead Button:hover, #toolbar Button:hover, ArticleCard Button:hover,
    #masthead Button:focus, #toolbar Button:focus, ArticleCard Button:focus {
        height: 1;
        border: none;
    }
    #masthead Button { min-width: 14; }
    #brand-title {
        text-style: bold;
        margin-right: 2;
    }
    #edition-label {
        width: 1fr;
    }
    #tor-btn {
        margin-right: 1;
    }

    /* Formulaire compact */
    #search-form {
        height: auto;
        margin-bottom: 1;
    }
    #search-form.collapsed {
        display: none;
    }
    #form-fields {
        height: auto;
    }
    .field-box {
        height: auto;
        margin-right: 1;
    }
    #box-theme { width: 2fr; }
    #box-pays { width: 17; }
    #box-period { width: 22; }
    #box-question { width: 3fr; }
    #box-count { width: 11; }
    .field-label {
        text-style: bold;
    }
    #btn-search {
        min-width: 18;
        margin-top: 1;
    }
    #form-options {
        height: 3;
        margin-top: 0;
        align: left middle;
    }
    #form-options Checkbox {
        margin-right: 2;
    }
    #btn-adv-toggle {
        margin-left: 1;
    }

    /* Panneau de filtres avancés */
    #advanced-panel {
        height: auto;
        padding: 1 2;
        margin-bottom: 1;
        display: none;
    }
    #advanced-panel.visible {
        display: block;
    }
    #adv-row { height: auto; }
    #box-inc-dom { width: 1fr; margin-right: 1; }
    #box-exc-dom { width: 1fr; margin-right: 1; }
    #box-sort { width: 22; }

    /* Barre d'outils */
    #toolbar {
        height: 1;
        margin-bottom: 1;
        align: left middle;
    }
    #toolbar Input, #toolbar Button { height: 1; border: none; padding: 0 1; }
    #toolbar Button { min-width: 6; }
    #btn-export-ok { width: 6; }
    #toolbar Select, #toolbar SelectCurrent { height: 1; border: none; padding: 0 1; }
    #input-live-filter {
        width: 32;
        margin-right: 1;
    }
    #select-live-sort {
        width: 30;
        margin-right: 1;
    }
    #toolbar-spacer { width: 1fr; }
    #btn-form-toggle { width: 24; margin-right: 1; }
    #btn-condensed { width: 14; margin-right: 1; }

    /* Panneau de progression de la recherche */
    #search-progress {
        height: auto;
        padding: 0 2;
        margin-bottom: 1;
        display: none;
    }
    #search-progress.visible { display: block; }
    #sp-steps { width: 100%; height: 1; }
    #sp-text { width: 100%; height: 1; }

    /* Défilement : barres bien visibles */
    #news-scroll, #arch-right, #mods-scroll, #reader-body-scroll {
        scrollbar-size-vertical: 2;
        scrollbar-gutter: stable;
    }
    #news-scroll.condensed .summary,
    #news-scroll.condensed .orig-title,
    #arch-right.condensed .summary,
    #arch-right.condensed .orig-title { display: none; }
    #export-label { margin-right: 1; text-style: bold; }
    #select-export { width: 24; margin-right: 1; }

    /* Onglets et liste d'articles plein écran */
    #tabs {
        height: 1fr;
    }
    TabPane {
        height: 1fr;
        padding: 0;
    }
    #news-split {
        height: 1fr;
    }
    #news-left {
        width: 1fr;
        height: 1fr;
    }
    #news-header-bar {
        height: auto;
        margin-bottom: 1;
    }
    #news-topline { height: 1; margin-bottom: 1; }
    #btn-new-search { display: none; margin-right: 2; }
    #btn-new-search.visible { display: block; }
    #news-headline { width: 1fr; }
    #arch-headline {
        width: 100%;
        height: auto;
        text-style: bold;
    }
    /* CRUCIAL : sans cela le conteneur prend 1fr (= la hauteur de l'écran)
       et coupe les cartes au lieu de laisser le défilement fonctionner. */
    #news-list, #arch-list, #mods-container {
        height: auto;
    }
    #news-warnings {
        height: auto;
    }
    .news-note {
        text-style: italic;
    }
    #news-scroll {
        height: 1fr;
    }

    /* Lecteur latéral */
    #reader-pane {
        width: 48%;
        height: 1fr;
        margin-left: 2;
        padding: 1 2;
        display: none;
    }
    #reader-pane.open {
        display: block;
    }
    #reader-header { height: 3; margin-bottom: 1; }
    #reader-kicker { width: 1fr; text-style: bold; }
    #reader-title { width: 100%; height: auto; text-style: bold; margin-bottom: 1; }
    #reader-meta { width: 100%; height: auto; margin-bottom: 1; }
    #reader-body-scroll { height: 1fr; margin-bottom: 1; }
    #reader-actions { height: 3; }
    #reader-url { width: 1fr; margin-right: 1; }

    /* Bienvenue */
    #welcome-box {
        height: auto;
        padding: 1 2;
    }
    #welcome-title { text-style: bold; margin-bottom: 1; }

    /* Archives */
    #arch-layout { height: 1fr; }
    #arch-left { width: 42; height: 1fr; margin-right: 2; }
    #arch-table { height: 1fr; margin-bottom: 1; }
    #arch-actions Button { width: 100%; margin-bottom: 1; }
    #arch-right { width: 1fr; height: 1fr; }

    /* Modules */
    #mods-scroll { height: 1fr; }
    .mod-group-title { text-style: bold; margin-top: 1; margin-bottom: 1; }
    .mod-item { height: auto; padding: 1 2; margin-bottom: 1; }
    .mod-header { height: 3; align: left middle; }
    .mod-header Checkbox { width: 1fr; }
    .mod-header Button { margin-left: 1; }
    .mod-meta { margin-left: 4; }
    #mods-footer-actions { height: auto; margin-top: 1; margin-bottom: 2; }

    /* Barre d'état */
    #statusbar {
        height: 2;
        padding: 0 2;
        align: left middle;
        border-top: solid;
    }
    #status-text { width: 1fr; }
    """

    def __init__(self, engine: Any):
        super().__init__()
        self.engine = engine
        self.busy = False
        self.archive_records: list[Any] = []
        self.selected_archive_id: Any = None
        self.displayed: dict[str, tuple[Any, list[Any]] | None] = {"news": None, "arch": None}
        self.headlines: dict[str, str] = {"news": "", "arch": ""}
        self.notes: dict[str, list[str]] = {"news": []}
        self.tor_state: tuple[str, str] = ("wait", "")
        self.current_tab: str = "news"
        self.reader_article: Any = None

        # Progression de la recherche
        self._phases: list[Phase] = []
        self._phase_idx = 0
        self._disp = 0.0
        self._target = 0.0
        self._t0 = 0.0
        self._last_event = 0.0
        self._last_text = ""
        self._spin = 0
        self._last_count = 0
        self._tick_timer: Any = None
        self._hide_timer: Any = None
        self.condensed = False

        saved_theme = self.engine.settings.get("theme", "sombre")
        self.theme_name = saved_theme if saved_theme in PALETTES else "sombre"
        self._init_translate = bool(self.engine.settings.get("translate", True))
        self._init_strict = bool(self.engine.settings.get("strict", True))
        self._init_dedupe = bool(self.engine.settings.get("deduplicate", True))

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)

        with Vertical(id="main-body"):
            with Horizontal(id="masthead"):
                yield Label(f"📰 {APP_NAME} v{APP_VERSION}", id="brand-title")
                yield Label(f"ÉDITION TOR ANONYME · {today_long().upper()}", id="edition-label")
                yield Button("● Tor : vérification…", id="tor-btn")
                yield Button(self._theme_toggle_label(), id="theme-btn")

            with Vertical(id="search-form"):
                with Horizontal(id="form-fields"):
                    with Vertical(id="box-theme", classes="field-box"):
                        yield Label("THÈME", classes="field-label")
                        yield Input(placeholder="ex : jeux vidéo, cybersécurité…", id="input-theme")

                    with Vertical(id="box-pays", classes="field-box"):
                        yield Label("PAYS", classes="field-label")
                        pays_options = [(WORLD, WORLD)] + [(p, p) for p in PAYS]
                        yield Select(pays_options, value=WORLD, allow_blank=False, id="select-pays")

                    with Vertical(id="box-period", classes="field-box"):
                        yield Label("PÉRIODE", classes="field-label")
                        period_options = [(lbl, k) for k, (lbl, _) in PERIODS.items()]
                        yield Select(period_options, value="all", allow_blank=False, id="select-period")

                    with Vertical(id="box-question", classes="field-box"):
                        yield Label("QUESTION / ANGLE PRÉCIS", classes="field-label")
                        yield Input(placeholder="ex : Meilleurs jeux vidéo de 2026 ?", id="input-question")

                    with Vertical(id="box-count", classes="field-box"):
                        yield Label("ARTICLES", classes="field-label")
                        yield Input(value="10", id="input-count")

                    yield Button("ENQUÊTER ▸", id="btn-search", variant="primary")

                with Horizontal(id="form-options"):
                    yield Checkbox("Traduire en français", value=self._init_translate, id="chk-translate")
                    yield Checkbox("Mode strict", value=self._init_strict, id="chk-strict")
                    yield Checkbox("Anti-doublons", value=self._init_dedupe, id="chk-dedupe")
                    yield Button("⚙ Filtres avancés", id="btn-adv-toggle", variant="default")

                with Vertical(id="advanced-panel"):
                    with Horizontal(id="adv-row"):
                        with Vertical(id="box-inc-dom", classes="field-box"):
                            yield Label("DOMAINES AUTORISÉS (virgule)", classes="field-label")
                            yield Input(placeholder="ex : lemonde.fr, reuters.com", id="input-inc-dom")
                        with Vertical(id="box-exc-dom", classes="field-box"):
                            yield Label("DOMAINES EXCLUS", classes="field-label")
                            yield Input(placeholder="ex : pinterest.com, msn.com", id="input-exc-dom")
                        with Vertical(id="box-sort", classes="field-box"):
                            yield Label("TRI PAR DÉFAUT", classes="field-label")
                            yield Select(
                                [("Pertinence", "relevance"), ("Plus récent", "date")],
                                value="relevance",
                                allow_blank=False,
                                id="select-default-sort",
                            )

            with Horizontal(id="toolbar"):
                yield Input(placeholder="Filtrer les résultats (Ctrl+F)…", id="input-live-filter")
                yield Select(
                    [("Trier : Pertinence", "relevance"), ("Trier : Date", "date")],
                    value="relevance",
                    allow_blank=False,
                    id="select-live-sort",
                )
                yield Button("▴ Masquer recherche", id="btn-form-toggle", variant="default")
                yield Button("☰ Condensé", id="btn-condensed", variant="default")
                yield Static("", id="toolbar-spacer")
                yield Label("Exporter :", id="export-label")
                yield Select([], allow_blank=True, id="select-export")
                yield Button("OK", id="btn-export-ok", variant="default")

            with Vertical(id="search-progress"):
                yield Static("", id="sp-steps")
                yield LoadingBar(id="sp-bar")
                yield Static("", id="sp-text")

            with TabbedContent(initial="news", id="tabs"):
                with TabPane("À LA UNE", id="news"):
                    with Horizontal(id="news-split"):
                        with Vertical(id="news-left"):
                            with Vertical(id="news-header-bar"):
                                with Horizontal(id="news-topline"):
                                    yield Button("◂ Nouvelle recherche", id="btn-new-search", variant="primary")
                                    yield Static("", id="news-headline", markup=False)
                                yield Container(id="news-warnings")
                            with VerticalScroll(id="news-scroll"):
                                yield Container(id="news-list")

                        with Vertical(id="reader-pane"):
                            with Horizontal(id="reader-header"):
                                yield Label("LECTEUR INTÉGRAL", id="reader-kicker")
                                yield Button("✕ Fermer", id="btn-close-reader", variant="default")
                            yield Static("", id="reader-title", markup=False)
                            yield Static("", id="reader-meta", markup=False)
                            with VerticalScroll(id="reader-body-scroll"):
                                yield Static("", id="reader-body")
                            with Horizontal(id="reader-actions"):
                                yield Input(value="", id="reader-url")
                                yield Button("Copier le lien", id="btn-reader-copy", variant="primary")

                with TabPane("ARCHIVES", id="arch"):
                    with Horizontal(id="arch-layout"):
                        with Vertical(id="arch-left"):
                            yield Label("ENQUÊTES ARCHIVÉES", classes="field-label")
                            yield DataTable(id="arch-table", cursor_type="row")
                            with Vertical(id="arch-actions"):
                                yield Button("Exporter Dashboard HTML", id="btn-export-dash")
                                yield Button("Supprimer l'enquête", id="btn-delete-arch", variant="error")
                        with VerticalScroll(id="arch-right"):
                            yield Static("", id="arch-headline", markup=False)
                            yield Container(id="arch-list")

                with TabPane("MODULES", id="mods"):
                    with VerticalScroll(id="mods-scroll"):
                        yield Container(id="mods-container")
                        with Horizontal(id="mods-footer-actions"):
                            yield Button("Recharger les modules", id="btn-reload-mods", variant="primary")

        with Horizontal(id="statusbar"):
            yield Label("Prêt.", id="status-text")

        yield Footer()

    def on_mount(self) -> None:
        self._tick_timer = self.set_interval(0.1, self._tick, pause=True)
        table = self.query_one("#arch-table", DataTable)
        table.add_columns("Date", "Enquête", "Nb")

        self._apply_palette_styles()
        self._apply_tor_ui()
        self._show_welcome()
        self.refresh_archives()
        self.render_modules()
        self.refresh_exporters()
        self.action_check_tor()

    # ------------------------------------------------------------------ #
    # Thème & styles apaisés
    # ------------------------------------------------------------------ #

    def _theme_toggle_label(self) -> str:
        return "☀ Clair" if self.theme_name == "sombre" else "☾ Sombre"

    def action_toggle_theme(self) -> None:
        self.theme_name = "clair" if self.theme_name == "sombre" else "sombre"
        self.engine.settings.set("theme", self.theme_name)
        self.query_one("#theme-btn", Button).label = self._theme_toggle_label()
        self._apply_palette_styles()
        self._apply_tor_ui()
        try:
            self.engine.build_dashboard()
        except Exception:
            pass

    def _apply_palette_styles(self) -> None:
        p = PALETTES[self.theme_name]
        paper, card, elevated, ink, accent, muted, border = (
            p["paper"], p["card"], p["elevated"], p["ink"], p["accent"], p["muted"], p["border"]
        )

        self.screen.styles.background = paper
        self.screen.styles.color = ink

        masthead = self.query_one("#masthead")
        masthead.styles.background = paper
        masthead.styles.border_bottom = ("solid", border)

        adv = self.query_one("#advanced-panel")
        adv.styles.background = elevated
        adv.styles.border = ("solid", border)

        reader = self.query_one("#reader-pane")
        reader.styles.background = card
        reader.styles.border = ("solid", border)

        for lbl_id, color in (
            ("#brand-title", ink),
            ("#edition-label", muted),
            ("#news-headline", ink),
            ("#arch-headline", ink),
            ("#status-text", muted),
            ("#reader-meta", muted),
        ):
            self.query_one(lbl_id, Static).styles.color = color

        for cls in ("field-label", "kicker", "mod-group-title"):
            for w in self.query(f".{cls}"):
                w.styles.color = accent

        for w in self.query(".news-note"):
            w.styles.color = p["amber"]

        for w in self.query(".badge"):
            w.styles.color = p["amber"]

        for cls in ("meta", "orig-title", "mod-meta"):
            for w in self.query(f".{cls}"):
                w.styles.color = muted

        for w in self.query("ArticleCard"):
            w.styles.background = card
            w.styles.border = ("solid", border)

        for w in self.query(".mod-item"):
            w.styles.background = card
            w.styles.border = ("solid", border)

        for welcome in self.query("#welcome-box"):
            welcome.styles.background = card
            welcome.styles.border = ("solid", border)

        panel = self.query_one("#search-progress")
        panel.styles.background = elevated
        bar = self.query_one("#sp-bar", LoadingBar)
        bar.fill, bar.empty, bar.fg = accent, border, ink
        bar.refresh()
        self.query_one("#sp-text", Static).styles.color = muted

        statusbar = self.query_one("#statusbar")
        statusbar.styles.background = paper
        statusbar.styles.border_top = ("solid", border)

    # ------------------------------------------------------------------ #
    # Navigation & événements
    # ------------------------------------------------------------------ #

    def action_focus_filter(self) -> None:
        self.query_one("#input-live-filter", Input).focus()

    def _set_form_collapsed(self, collapsed: bool) -> None:
        form = self.query_one("#search-form")
        form.set_class(collapsed, "collapsed")
        self.query_one("#btn-new-search", Button).set_class(collapsed, "visible")
        self.query_one("#btn-form-toggle", Button).label = (
            "▾ Afficher recherche" if collapsed else "▴ Masquer recherche"
        )

    def action_toggle_form(self) -> None:
        collapsed = not self.query_one("#search-form").has_class("collapsed")
        self._set_form_collapsed(collapsed)
        if not collapsed:
            self.query_one("#input-theme", Input).focus()

    def action_toggle_condensed(self) -> None:
        self.condensed = not self.condensed
        for sel in ("#news-scroll", "#arch-right"):
            self.query_one(sel).set_class(self.condensed, "condensed")
        self.query_one("#btn-condensed", Button).label = "☰ Détaillé" if self.condensed else "☰ Condensé"

    def action_scroll_results(self, where: str) -> None:
        sel = {"news": "#news-scroll", "arch": "#arch-right"}.get(self.current_tab)
        if not sel:
            return
        w = self.query_one(sel, VerticalScroll)
        if where == "down":
            w.scroll_page_down(animate=False)
        elif where == "up":
            w.scroll_page_up(animate=False)
        elif where == "end":
            w.scroll_end(animate=False)
        else:
            w.scroll_home(animate=False)

    def action_close_reader(self) -> None:
        reader = self.query_one("#reader-pane")
        if reader.has_class("open"):
            reader.remove_class("open")
            self.reader_article = None
        elif self.query_one("#search-form").has_class("collapsed") and not self.busy:
            # Échap : retour au formulaire pour refaire une recherche
            self._set_form_collapsed(False)
            self.query_one("#input-theme", Input).focus()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        tab_id = event.pane.id or "news"
        self.current_tab = tab_id
        self.query_one("#toolbar", Horizontal).display = tab_id != "mods"
        if tab_id == "arch":
            self.refresh_archives()
        elif tab_id == "mods":
            self.render_modules()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in ("input-theme", "input-question", "input-count", "input-inc-dom", "input-exc-dom"):
            self.start_search()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "input-live-filter":
            self._reapply_live_view()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "select-live-sort":
            self._reapply_live_view()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        cb_id = event.checkbox.id or ""
        if cb_id == "chk-translate":
            self.engine.settings.set("translate", bool(event.value))
        elif cb_id == "chk-strict":
            self.engine.settings.set("strict", bool(event.value))
        elif cb_id == "chk-dedupe":
            self.engine.settings.set("deduplicate", bool(event.value))
        elif cb_id.startswith("mod-"):
            self.on_checkbox_changed_module(cb_id[4:], bool(event.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "theme-btn":
            self.action_toggle_theme()
        elif btn_id == "tor-btn":
            self.action_check_tor()
        elif btn_id == "btn-search":
            self.start_search()
        elif btn_id == "btn-new-search":
            self._set_form_collapsed(False)
            self.query_one("#input-theme", Input).focus()
        elif btn_id == "btn-form-toggle":
            self.action_toggle_form()
        elif btn_id == "btn-condensed":
            self.action_toggle_condensed()
        elif btn_id == "btn-adv-toggle":
            self.query_one("#advanced-panel").toggle_class("visible")
        elif btn_id == "btn-close-reader":
            self.action_close_reader()
        elif btn_id == "btn-reader-copy" and self.reader_article:
            self.copy_link(self.reader_article.url or "")
        elif btn_id == "btn-export-ok":
            self.do_export()
        elif btn_id == "btn-export-dash":
            self.export_dashboard()
        elif btn_id == "btn-delete-arch":
            self.delete_selected_archive()
        elif btn_id == "btn-reload-mods":
            self.reload_modules()
        elif btn_id.startswith("cfg-mod-"):
            self._open_module_config(btn_id[8:])
        elif btn_id.startswith("test-mod-"):
            self._test_source_module(btn_id[9:])

    def set_status(self, text: str) -> None:
        self.query_one("#status-text", Label).update(text)

    def copy_link(self, url: str) -> None:
        if not url:
            self.set_status("Aucun lien disponible.")
            return
        if _copy_to_clipboard(url):
            self.set_status("Lien copié. Ouvre-le dans Tor Browser.")
        else:
            self.set_status(f"Lien : {url}")

    # ------------------------------------------------------------------ #
    # Lecteur intégral & Rendu
    # ------------------------------------------------------------------ #

    def open_reader(self, article: Any) -> None:
        self.reader_article = article
        self.query_one("#reader-pane").add_class("open")

        src = (article.source or domain_of(article.url) or "SOURCE").upper()
        score = getattr(article, "score", 0)
        rtime = getattr(article, "reading_time", 1)
        self.query_one("#reader-kicker", Label).update(f"{src} · PERTINENCE {score}% · ~{rtime} MIN")
        self.query_one("#reader-title", Static).update(article.title)

        by = f"Par {article.author}" if getattr(article, "author", None) else "Auteur non indiqué"
        self.query_one("#reader-meta", Static).update(f"{by} · {fmt_date(article.published)}")

        body = getattr(article, "full_text", "") or article.summary or "Contenu indisponible."
        self.query_one("#reader-body", Static).update(body)
        self.query_one("#reader-url", Input).value = article.url or ""
        self.query_one("#reader-body-scroll", VerticalScroll).scroll_home(animate=False)

    def _show_welcome(self) -> None:
        self.query_one("#news-headline", Static).update("")
        self.query_one("#news-warnings", Container).remove_children()
        list_box = self.query_one("#news-list", Container)
        list_box.remove_children()

        box = Container(id="welcome-box")
        list_box.mount(box)
        box.mount(Label("Bienvenue dans la salle de rédaction FULL INFO.", id="welcome-title"))
        box.mount(
            Static(
                "• Évite les guillemets autour du thème (écris simplement : jeux vidéo) pour profiter "
                "du thésaurus multilingue automatique.\n"
                "• Réserve les guillemets aux expressions strictes (ex : \"GTA VI\") et le signe moins (-mobile) aux exclusions.\n"
                "• Clique sur « Lire » sur n'importe quel article pour afficher son texte intégral à droite."
            )
        )
        self._apply_palette_styles()

    def _reapply_live_view(self) -> None:
        target = self.current_tab if self.current_tab in ("news", "arch") else "news"
        data = self.displayed.get(target)
        if not data:
            return
        _, articles = data
        query_filter = self.query_one("#input-live-filter", Input).value.strip().lower()
        sort_val = self.query_one("#select-live-sort", Select).value
        sort_mode = str(sort_val) if sort_val and sort_val is not Select.BLANK else "relevance"

        filtered = articles
        if query_filter:
            filtered = [
                a for a in articles
                if query_filter in f"{a.title} {a.source} {a.author} {a.summary}".lower()
            ]
        filtered = sort_articles(filtered, mode=sort_mode)
        self.render_articles(target, filtered, self.headlines.get(target, ""), self.notes.get(target) if target == "news" else None)

    def render_articles(
        self,
        target: str,
        articles: list[Any],
        headline: str,
        notes: list[str] | None = None,
    ) -> None:
        if target == "news":
            self.query_one("#news-headline", Static).update(headline)
            warn_box = self.query_one("#news-warnings", Container)
            warn_box.remove_children()
            if notes:
                warn_box.mount(Label("ℹ " + " · ".join(notes), classes="news-note"))
            list_box = self.query_one("#news-list", Container)
        else:
            self.query_one("#arch-headline", Static).update(headline)
            list_box = self.query_one("#arch-list", Container)

        list_box.remove_children()

        if not articles:
            list_box.mount(Static("Aucun article ne correspond aux critères actuels."))
            self._apply_palette_styles()
            return

        list_box.mount_all(
            [ArticleCard(art, i, self.copy_link, self.open_reader) for i, art in enumerate(articles)]
        )

        if target == "news":
            self.query_one("#news-scroll", VerticalScroll).scroll_home(animate=False)

        self._apply_palette_styles()

    # ------------------------------------------------------------------ #
    # Tor
    # ------------------------------------------------------------------ #

    def _apply_tor_ui(self) -> None:
        p = PALETTES[self.theme_name]
        kind, info = self.tor_state
        tor_btn = self.query_one("#tor-btn", Button)
        search_btn = self.query_one("#btn-search", Button)

        if kind == "ok":
            tor_btn.label = f"● Tor actif · {info}"
            tor_btn.styles.color = p["green"]
            search_btn.disabled = self.busy
        elif kind == "ko":
            tor_btn.label = "● Tor hors ligne"
            tor_btn.styles.color = p["accent"]
            search_btn.disabled = True
        else:
            tor_btn.label = "● Tor : connexion…"
            tor_btn.styles.color = p["muted"]
            search_btn.disabled = True

    @work(thread=True)
    def action_check_tor(self) -> None:
        self.call_from_thread(self._set_tor_waiting)
        try:
            exit_ip = self.engine.connect_tor()
            self.call_from_thread(self._on_tor_ok, exit_ip)
        except Exception as exc:
            self.call_from_thread(self._on_tor_ko, str(exc))

    def _set_tor_waiting(self) -> None:
        self.tor_state = ("wait", "")
        self._apply_tor_ui()

    def _on_tor_ok(self, exit_ip: str) -> None:
        self.tor_state = ("ok", exit_ip)
        self._apply_tor_ui()
        self.set_status("Circuit Tor opérationnel.")

    def _on_tor_ko(self, err: str) -> None:
        self.tor_state = ("ko", err)
        self._apply_tor_ui()
        self.set_status(err.replace("\n", " "))

    # ------------------------------------------------------------------ #
    # Recherche
    # ------------------------------------------------------------------ #

    def start_search(self) -> None:
        if self.busy:
            return
        if not getattr(self.engine.tor, "connected", False):
            self.push_screen(
                InfoModal("Tor requis", "Tor n'est pas connecté. Clique sur l'indicateur Tor (Ctrl+R).")
            )
            return

        theme = self.query_one("#input-theme", Input).value.strip()
        pays_val = self.query_one("#select-pays", Select).value
        pays = "" if not pays_val or pays_val == WORLD else str(pays_val)
        period_val = self.query_one("#select-period", Select).value
        period = str(period_val) if period_val and period_val is not Select.BLANK else "all"
        question = self.query_one("#input-question", Input).value.strip()
        translate = bool(self.query_one("#chk-translate", Checkbox).value)
        strict = bool(self.query_one("#chk-strict", Checkbox).value)
        dedupe = bool(self.query_one("#chk-dedupe", Checkbox).value)
        inc_dom = self.query_one("#input-inc-dom", Input).value.strip()
        exc_dom = self.query_one("#input-exc-dom", Input).value.strip()
        sort_val = self.query_one("#select-default-sort", Select).value
        sort_by = str(sort_val) if sort_val and sort_val is not Select.BLANK else "relevance"

        if not (theme or pays or question):
            self.push_screen(InfoModal("Critère manquant", "Renseigne au moins un thème, un pays ou une question."))
            return

        raw_count = self.query_one("#input-count", Input).value.strip()
        try:
            count = max(1, min(MAX_ARTICLES, int(raw_count)))
        except ValueError:
            self.push_screen(InfoModal("Nombre invalide", f"Choisis un nombre entre 1 et {MAX_ARTICLES}."))
            return

        request = SearchRequest(
            theme=theme,
            pays=pays,
            question=question,
            count=count,
            translate=translate,
            strict=strict,
            period=period,
            sort_by=sort_by,
            include_domains=inc_dom,
            exclude_domains=exc_dom,
            deduplicate=dedupe,
        )

        self.busy = True
        self._last_count = count
        self.query_one("#reader-pane").remove_class("open")
        self.reader_article = None
        self.query_one("#input-live-filter", Input).value = ""
        self.query_one("#select-live-sort", Select).value = sort_by

        search_btn = self.query_one("#btn-search", Button)
        search_btn.disabled = True
        search_btn.label = "ENQUÊTE…"

        self.query_one("#tabs", TabbedContent).active = "news"
        # On libère l'écran : le formulaire se replie, les résultats gagnent la place.
        self._set_form_collapsed(True)
        self._begin_progress(translate)

        self._run_search_worker(request)

    @work(thread=True)
    def _run_search_worker(self, request: SearchRequest) -> None:
        def on_progress(done: int, total: int, text: str) -> None:
            self.call_from_thread(self._apply_progress, done, total, text)

        try:
            result = self.engine.search(request, on_progress)
            self.call_from_thread(self._on_search_done, result)
        except Exception as exc:
            self.call_from_thread(self._on_search_error, str(exc))

    # --- Barre de chargement ------------------------------------------- #

    def _begin_progress(self, translate: bool) -> None:
        if self._hide_timer is not None:
            self._hide_timer.stop()
            self._hide_timer = None
        self._phases = build_phases(translate)
        self._phase_idx = 0
        self._disp = self._target = 0.0
        self._t0 = self._last_event = time.monotonic()
        self._last_text = "Démarrage de l'enquête…"
        self.query_one("#search-progress").add_class("visible")
        self._tick_timer.resume()
        self._render_progress()

    def _apply_progress(self, done: int, total: int, text: str) -> None:
        if not self.busy or not self._phases:
            return
        key = phase_key_of(text)
        idx = next((i for i, ph in enumerate(self._phases) if ph.key == key), self._phase_idx)
        self._phase_idx = max(self._phase_idx, idx)  # on ne recule jamais
        ph = self._phases[self._phase_idx]
        frac = min(done / total, 1.0) if total > 0 and done > 0 else 0.0
        self._target = max(self._target, ph.start + (ph.end - ph.start) * frac)
        self._last_event = time.monotonic()
        self._last_text = text
        self.set_status(text)
        self._render_progress()

    def _tick(self) -> None:
        """Fait avancer la barre en douceur, même entre deux événements du moteur."""
        if not self.busy or not self._phases:
            return
        diff = self._target - self._disp
        if diff > 0.0005:
            self._disp += diff * 0.3
        else:
            ph = self._phases[self._phase_idx]
            cap = ph.start + 0.92 * (ph.end - ph.start)
            if self._disp < cap:
                self._disp += (cap - self._disp) * 0.004
        self._spin = (self._spin + 1) % len(SPINNER)
        self._render_progress()

    def _render_progress(self, final: str | None = None, ok: bool = True) -> None:
        p = PALETTES[self.theme_name]
        now = time.monotonic()
        elapsed = int(now - self._t0)
        clock = f"{elapsed // 60:02d}:{elapsed % 60:02d}"

        steps = Text()
        for i, ph in enumerate(self._phases):
            if i:
                steps.append("  ›  ", style=p["muted"])
            if (final is not None and ok) or i < self._phase_idx:
                steps.append(f"✔ {ph.label}", style=p["green"])
            elif i == self._phase_idx and final is None:
                steps.append(f"{SPINNER[self._spin]} {ph.label}", style=f"bold {p['accent']}")
            else:
                steps.append(f"○ {ph.label}", style=p["muted"])
        self.query_one("#sp-steps", Static).update(steps)
        self.query_one("#sp-bar", LoadingBar).set_value(self._disp)

        if final is not None:
            line = Text(f"{final}   ⏱ {clock}", style=p["green"] if ok else p["accent"])
        else:
            line = Text(f"{self._last_text}   ⏱ {clock}")
            if now - self._last_event > 20:
                line.append(
                    "  · Tor répond lentement : l'application travaille toujours, patience.",
                    style=p["amber"],
                )
        self.query_one("#sp-text", Static).update(line)

    def _finish_progress(self, ok: bool, message: str) -> None:
        self._tick_timer.pause()
        if ok:
            self._phase_idx = max(0, len(self._phases) - 1)
            self._disp = self._target = 1.0
        self._render_progress(final=message, ok=ok)
        if ok:
            self._hide_timer = self.set_timer(8, self._hide_progress)

    def _hide_progress(self) -> None:
        self.query_one("#search-progress").remove_class("visible")
        self._hide_timer = None

    def _on_search_done(self, result: Any) -> None:
        self.displayed["news"] = (result.record, result.articles)
        n = len(result.articles)
        self.headlines["news"] = (
            f"Enquête : {result.label} — {n} article(s) sur {self._last_count} demandé(s) "
            f"· {result.stats.get('raw', n)} candidats analysés"
        )
        self.notes["news"] = list(result.warnings or [])
        self.render_articles("news", result.articles, self.headlines["news"], result.warnings)
        self.refresh_archives()

        self.busy = False
        search_btn = self.query_one("#btn-search", Button)
        search_btn.disabled = False
        search_btn.label = "ENQUÊTER ▸"

        if n:
            self._finish_progress(True, f"✔ Terminé : {n} article(s) affiché(s)")
            self.set_status(
                f"{n} article(s). Molette, PgUp/PgDn ou barre de défilement pour tous les voir "
                "· Ctrl+E : nouvelle recherche · Ctrl+D : mode condensé."
            )
            self.query_one("#news-scroll", VerticalScroll).focus()
        else:
            self._finish_progress(False, "✖ Aucun article retenu")
            self.set_status("Aucun article retenu.")
            self._set_form_collapsed(False)

    def _on_search_error(self, err: str) -> None:
        self.busy = False
        search_btn = self.query_one("#btn-search", Button)
        search_btn.disabled = False
        search_btn.label = "ENQUÊTER ▸"
        self._finish_progress(False, "✖ Échec de l'enquête : " + err.replace("\n", " "))
        self._set_form_collapsed(False)
        self.set_status("Erreur : " + err)
        self.push_screen(InfoModal("Erreur", err))

    # ------------------------------------------------------------------ #
    # Archives
    # ------------------------------------------------------------------ #

    def refresh_archives(self) -> None:
        table = self.query_one("#arch-table", DataTable)
        table.clear()
        self.archive_records = list(self.engine.list_searches())

        for idx, rec in enumerate(self.archive_records):
            table.add_row(fmt_date(rec.created_dt), str(rec.label), str(rec.count), key=str(idx))

        tab = self.query_one("#tabs", TabbedContent).get_tab("arch")
        tab.label = f"ARCHIVES ({len(self.archive_records)})"

        if self.archive_records and self.selected_archive_id is None:
            self._load_archive_index(0)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "arch-table" and event.row_key is not None:
            try:
                idx = int(str(event.row_key.value))
            except ValueError:
                return
            self._load_archive_index(idx)

    def _load_archive_index(self, idx: int) -> None:
        if 0 <= idx < len(self.archive_records):
            rec = self.archive_records[idx]
            self.selected_archive_id = rec.id
            arts = self.engine.load_articles(rec.id)
            self.displayed["arch"] = (rec, arts)
            self.headlines["arch"] = f"Archive : {rec.label} ({len(arts)} articles)"
            self.render_articles("arch", arts, self.headlines["arch"])

    def export_dashboard(self) -> None:
        try:
            path = self.engine.build_dashboard()
        except Exception as exc:
            self.push_screen(InfoModal("Erreur", str(exc)))
            return

        def after_confirm(open_now: bool) -> None:
            if open_now:
                try:
                    webbrowser.open(path.as_uri())
                except Exception as exc:
                    self.set_status(f"Impossible d'ouvrir le navigateur : {exc}")

        self.push_screen(
            ConfirmModal(
                "Dashboard exporté",
                f"Dashboard généré :\n{path}\n\nOuvrir dans le navigateur local ?",
                yes_label="Ouvrir",
                no_label="Fermer",
            ),
            after_confirm,
        )

    def delete_selected_archive(self) -> None:
        table = self.query_one("#arch-table", DataTable)
        if not self.archive_records or table.cursor_row is None:
            self.push_screen(InfoModal("Archives", "Sélectionne d'abord une enquête."))
            return

        row_idx = table.cursor_row
        if not (0 <= row_idx < len(self.archive_records)):
            return
        rec = self.archive_records[row_idx]

        def after_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            self.engine.delete_search(rec.id)
            if self.selected_archive_id == rec.id:
                self.selected_archive_id = None
                self.displayed["arch"] = None
                self.query_one("#arch-headline", Static).update("")
                self.query_one("#arch-list", Container).remove_children()
            self.refresh_archives()
            self.set_status("Enquête supprimée.")

        self.push_screen(
            ConfirmModal("Supprimer", "Supprimer définitivement cette enquête ?", yes_label="Supprimer", no_label="Annuler"),
            after_confirm,
        )

    # ------------------------------------------------------------------ #
    # Modules
    # ------------------------------------------------------------------ #

    def render_modules(self) -> None:
        container = self.query_one("#mods-container", Container)
        container.remove_children()
        mm = self.engine.modules

        container.mount(Label("ARCHITECTURE MODULAIRE", classes="field-label"))
        container.mount(Static(f"Dossier des plugins : {MODULES_DIR}"))

        groups = (
            ("source", "SOURCES D'INFORMATION"),
            ("translator", "TRADUCTION (le premier module actif est utilisé)"),
            ("exporter", "EXPORTS"),
        )

        for kind, title in groups:
            mods = [m for m in mm.modules.values() if m.kind == kind]
            container.mount(Label(f"{title} ({len(mods)})", classes="mod-group-title"))
            if not mods:
                container.mount(Static("Aucun module de ce type."))
                continue

            for m in mods:
                item = Container(classes="mod-item")
                container.mount(item)

                header = Horizontal(classes="mod-header")
                item.mount(header)
                header.mount(
                    Checkbox(
                        f"{m.label} (v{m.version})",
                        value=mm.is_enabled(m.name),
                        id=f"mod-{m.name}",
                    )
                )
                if getattr(m, "settings_schema", None):
                    header.mount(Button("⚙ Configurer", id=f"cfg-mod-{m.name}", variant="default"))
                if kind == "source":
                    header.mount(Button("⚡ Tester", id=f"test-mod-{m.name}", variant="default"))

                if m.description:
                    item.mount(Static(m.description, classes="mod-meta"))
                meta = f"id : {m.name}" + (f" · auteur : {m.author}" if m.author else "")
                item.mount(Label(meta, classes="mod-meta"))

        if mm.errors:
            container.mount(Label("MODULES EN ERREUR", classes="mod-group-title"))
            for err in mm.errors:
                container.mount(Label("• " + err, classes="news-note"))

        self._apply_palette_styles()

    def _open_module_config(self, mod_name: str) -> None:
        mod = self.engine.modules.modules.get(mod_name)
        if not mod or not getattr(mod, "settings_schema", None):
            return
        current = self.engine.modules.get_module_config(mod_name)

        def on_saved(new_cfg: dict[str, Any] | None) -> None:
            if new_cfg is not None:
                self.engine.modules.set_module_config(mod_name, new_cfg)
                self.set_status(f"Configuration enregistrée pour « {mod.label} ».")

        self.push_screen(ModuleConfigModal(mod.label, mod.settings_schema, current), on_saved)

    def _test_source_module(self, mod_name: str) -> None:
        if not getattr(self.engine.tor, "connected", False):
            self.push_screen(InfoModal("Tor requis", "Connecte Tor avant de tester une source."))
            return
        mod = self.engine.modules.modules.get(mod_name)
        if not mod:
            return
        self.set_status(f"Test de « {mod.label} » via Tor…")
        self._run_module_test_worker(mod)

    @work(thread=True)
    def _run_module_test_worker(self, mod: Any) -> None:
        try:
            items = mod.search(self.engine.tor, "technologie", "fr-FR", 3) or []
            sample = "\n".join(f"• {c.get('title', '?')}" for c in items[:3]) or "(aucun résultat)"
            msg = f"Source : {mod.label}\nRésultats reçus : {len(items)}\n\nAperçu :\n{sample}"
            self.call_from_thread(self.push_screen, InfoModal("Test réussi", msg))
            self.call_from_thread(self.set_status, f"Test réussi pour « {mod.label} ».")
        except Exception as exc:
            self.call_from_thread(self.push_screen, InfoModal("Échec du test", f"{mod.label} :\n{exc}"))
            self.call_from_thread(self.set_status, f"Échec du test pour « {mod.label} ».")

    def on_checkbox_changed_module(self, mod_name: str, enabled: bool) -> None:
        self.engine.modules.set_enabled(mod_name, enabled)
        self.refresh_exporters()
        self.set_status(f"Module « {mod_name} » {'activé' if enabled else 'désactivé'}.")

    def reload_modules(self) -> None:
        self.engine.reload_modules()
        self.render_modules()
        self.refresh_exporters()
        n, e = len(self.engine.modules.modules), len(self.engine.modules.errors)
        self.set_status(f"{n} module(s) chargé(s)" + (f", {e} en erreur." if e else "."))

    def refresh_exporters(self) -> None:
        exporters = list(self.engine.modules.exporters(enabled_only=True))
        options = [(m.label, m.name) for m in exporters]
        sel = self.query_one("#select-export", Select)
        sel.set_options(options)
        sel.value = options[0][1] if options else Select.BLANK

    def do_export(self) -> None:
        data = self.displayed.get(self.current_tab)
        if not data or not data[0] or not data[1]:
            self.push_screen(InfoModal("Export", "Rien à exporter : affiche d'abord une enquête."))
            return

        record, articles = data
        sel = self.query_one("#select-export", Select)
        mod_name = sel.value
        if not mod_name or mod_name is Select.BLANK:
            self.push_screen(InfoModal("Export", "Aucun module d'export actif (onglet MODULES)."))
            return

        try:
            path = self.engine.export(str(mod_name), record, articles)
        except Exception as exc:
            self.push_screen(InfoModal("Erreur d'export", str(exc)))
            return

        self.set_status(f"Exporté : {path}")
        self.push_screen(InfoModal("Export terminé", f"Fichier créé :\n{path}"))


def run(engine: Any) -> None:
    FullInfoTUI(engine).run()