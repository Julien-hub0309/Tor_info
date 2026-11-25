from __future__ import annotations

import difflib
import hashlib
import html
import importlib.util
import inspect
import json
import math
import os
import re
import socket
import sqlite3
import sys
import threading
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
# Configuration globale
# --------------------------------------------------------------------------- #

APP_NAME = "FULL INFO"
APP_VERSION = "4.0"
APP_DIR = Path(__file__).resolve().parent
MODULES_DIR = APP_DIR / "modules"
DATA_DIR = Path(os.environ.get("FULL_INFO_HOME") or (Path.home() / ".full_info"))

TOR_HOST = "127.0.0.1"
TOR_PORTS = (9050, 9150)
TOR_CHECK_URL = "https://check.torproject.org/api/ip"

SEARCH_TIMEOUT = 45
ARTICLE_TIMEOUT = 25
WORKERS = 6
MAX_ARTICLES = 50

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}

PAYS = {
    "France": "fr-FR",
    "Belgique": "fr-BE",
    "Suisse": "fr-CH",
    "Canada": "fr-CA",
    "Allemagne": "de-DE",
    "Espagne": "es-ES",
    "Italie": "it-IT",
    "Royaume-Uni": "en-GB",
    "États-Unis": "en-US",
    "Japon": "ja-JP",
    "Brésil": "pt-BR",
}

PERIODS = {
    "all": ("Toutes dates", None),
    "24h": ("Dernières 24h", timedelta(hours=24)),
    "7d": ("7 derniers jours", timedelta(days=7)),
    "30d": ("30 derniers jours", timedelta(days=30)),
    "1y": ("Cette année", timedelta(days=365)),
}

MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
        "juil.", "août", "sept.", "oct.", "nov.", "déc."]
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS_LONG = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre"]


# --------------------------------------------------------------------------- #
# Modèles de données
# --------------------------------------------------------------------------- #


@dataclass
class Article:
    title: str
    url: str
    source: str = ""
    author: str = ""
    published: datetime | None = None
    summary: str = ""
    lang: str = ""
    original_title: str = ""
    score: int = 0                  # Score de pertinence normalisé (0 à 100)
    reading_time: int = 1           # Temps de lecture estimé en minutes
    full_text: str = ""             # Extrait long pour lecture dans l'interface
    text: str = field(default="", repr=False)


@dataclass
class SearchRequest:
    theme: str = ""
    pays: str = ""
    question: str = ""
    count: int = 15
    translate: bool = True
    strict: bool = True
    period: str = "all"             # all | 24h | 7d | 30d | 1y
    sort_by: str = "relevance"      # relevance | date
    include_domains: str = ""       # ex: "lemonde.fr, reuters.com"
    exclude_domains: str = ""       # ex: "pinterest.com"
    deduplicate: bool = True        # Fusionner les articles traitant du même sujet


@dataclass
class SearchRecord:
    id: int
    created: str
    theme: str
    pays: str
    question: str
    query: str
    requested: int
    sources: str = ""
    count: int = 0

    @property
    def label(self) -> str:
        return search_label(self.theme, self.pays, self.question, self.query)

    @property
    def created_dt(self) -> datetime | None:
        return parse_date(self.created)


@dataclass
class SearchResult:
    label: str
    query: str
    articles: list[Article]
    record: SearchRecord | None = None
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Utilitaires généraux
# --------------------------------------------------------------------------- #


def search_label(theme: str, pays: str, question: str, query: str = "") -> str:
    return " · ".join(x for x in (theme, pays, question) if x) or query


def build_query(theme: str, pays: str, question: str) -> str:
    parts = []
    if question:
        parts.append(question)
    if theme:
        parts.append(theme)
    if pays and not question:
        parts.append(pays)
    return " ".join(parts).strip()


def resolve_country(name: str) -> str:
    wanted = (name or "").strip().casefold()
    for label in PAYS:
        if label.casefold() == wanted:
            return label
    return ""


def fmt_date(dt: datetime | None) -> str:
    if not dt:
        return "date inconnue"
    local = dt.astimezone()
    return f"{local.day} {MOIS[local.month - 1]} {local.year}, {local:%H:%M}"


def today_long() -> str:
    n = datetime.now()
    return f"{JOURS[n.weekday()]} {n.day} {MOIS_LONG[n.month - 1]} {n.year}"


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def slugify(text: str, default: str = "export") -> str:
    text = re.sub(r"[^\w\-]+", "-", (text or "").lower(), flags=re.UNICODE).strip("-_")
    return text[:60] or default


def parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        value = str(value).strip()
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def sort_articles(articles: list[Article], mode: str = "date") -> list[Article]:
    floor = datetime.min.replace(tzinfo=timezone.utc)
    if mode == "relevance":
        return sorted(
            articles,
            key=lambda a: (a.score, a.published or floor),
            reverse=True,
        )
    return sorted(articles, key=lambda a: a.published or floor, reverse=True)


def make_candidate(title: str, url: str, source: str = "", published: Any = None, snippet: str = "") -> dict[str, Any]:
    return {
        "title": (title or "").strip(),
        "url": (url or "").strip(),
        "source": (source or "").strip(),
        "published": parse_date(published),
        "snippet": (snippet or "").strip(),
    }


# --------------------------------------------------------------------------- #
# Langues, thésaurus & moteur sémantique
# --------------------------------------------------------------------------- #

LANG_NAMES = {
    "en": "anglais", "es": "espagnol", "de": "allemand", "it": "italien", "pt": "portugais",
    "nl": "néerlandais", "ru": "russe", "ja": "japonais", "zh": "chinois", "ko": "coréen",
    "ar": "arabe", "tr": "turc", "pl": "polonais", "sv": "suédois", "da": "danois", "no": "norvégien",
    "fi": "finnois", "el": "grec", "he": "hébreu", "hi": "hindi", "uk": "ukrainien", "ro": "roumain",
    "cs": "tchèque", "hu": "hongrois", "id": "indonésien", "vi": "vietnamien", "th": "thaï",
    "fr": "français",
}

FR_WORDS = set("""le la les des une est dans pour sur avec qui que sont cette ont plus par au aux du et en
ce ses leur nous vous il elle été être mais comme aussi fait très sera selon après avant entre sans""".split())
EN_WORDS = set("""the and of to in is are was for with that this on as by at from it be have has will not but
his her their its they more new after before about would could which""".split())

STOP = set("""le la les un une des de du et en à au aux ce ces que qui quoi dans pour par sur avec sans est
sont été a ont il elle ils elles on nous vous je tu se sa son ses leur leurs plus pas ne ou mais donc or ni
car the an of to in and is are was were for with as by at from that this it be not but his her their its
cette cet ça comme aussi entre après avant très tout tous toute toutes fait faire peut être sera""".split())


def lang_name(code: str) -> str:
    code = (code or "").strip()
    return LANG_NAMES.get(code) or LANG_NAMES.get(code.split("-")[0]) or code.upper()


def looks_french(text: str) -> bool:
    words = re.findall(r"[a-zà-ÿ']+", (text or "").lower())
    if len(words) < 5:
        return False
    fr = sum(w in FR_WORDS for w in words)
    en = sum(w in EN_WORDS for w in words)
    accents = 1 if re.search(r"[éèêàçùôûî]", text.lower()) else 0
    return (fr - 1.5 * en + accents) >= 2


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower()).strip()


def _has(text: str, term: str) -> bool:
    return bool(term) and re.search(r"(?<![a-z0-9])" + re.escape(term), text) is not None


_QUESTION_STOP = None


def tokens(text: str) -> list[str]:
    global _QUESTION_STOP
    if _QUESTION_STOP is None:
        _QUESTION_STOP = {normalize(w) for w in STOP} | set(
            "quand comment pourquoi quel quelle quels quelles quoi date what when how why which does did "
            "est-ce qu'est tout tous donc alors encore ainsi cela celui celle ceux".split()
        )
    return [t for t in re.findall(r"[a-z0-9]+", normalize(text))
            if len(t) >= 3 and t not in _QUESTION_STOP]


def parse_advanced_query(raw: str) -> tuple[list[str], list[str], str]:
    """Extrait les expressions exactes ("..."), les exclusions (-mot) et le texte restant."""
    exact_phrases = [normalize(m) for m in re.findall(r'"([^"]+)"', raw or "") if m.strip()]
    cleaned = re.sub(r'"[^"]+"', " ", raw or "")
    negatives = []
    remaining = []
    for part in cleaned.split():
        if part.startswith("-") and len(part) > 2:
            negatives.append(normalize(part[1:]))
        else:
            remaining.append(part)
    return exact_phrases, negatives, " ".join(remaining).strip()


def _t(canon, aliases, fr, en, keywords, exclude=()):
    return {"canon": canon, "aliases": aliases, "fr": fr, "en": en,
            "keywords": keywords, "exclude": list(exclude)}


THESAURUS = [
    _t("jeux vidéo",
       ["jeu video", "jeux video", "jeu vidéo", "jv", "gaming", "gamer", "gamers", "video game",
        "video games", "videogame", "videogames"],
       ['"jeux vidéo"', "jeu vidéo sortie annonce", "gaming actualité"],
       ['"video games"', "gaming news", "video game release"],
       ["jeu video", "jeux video", "gaming", "gamer", "joueur", "joueurs", "console", "playstation",
        "ps5", "xbox", "nintendo", "switch", "steam", "esport", "video game", "videogame", "dlc",
        "fps", "rpg", "game", "gameplay"],
       ["jeux olympiques", "jeu de societe", "jeux de societe", "casino", "loto", "jeux d'argent",
        "paris sportifs", "jeux paralympiques", "olympic games"]),
    _t("intelligence artificielle",
       ["ia", "ai", "artificial intelligence", "machine learning", "apprentissage automatique",
        "ia generative", "chatgpt", "llm"],
       ['"intelligence artificielle"', "IA générative actualité", "chatgpt openai anthropic"],
       ['"artificial intelligence"', "generative AI news", "openai anthropic google AI"],
       ["intelligence artificielle", "artificial intelligence", "ia generative", "generative ai",
        "machine learning", "deep learning", "chatgpt", "openai", "anthropic", "llm",
        "modele de langage", "language model", "robot", "neural"]),
    _t("cybersécurité",
       ["cybersecurite", "securite informatique", "cyber securite", "hacking", "piratage",
        "cybersecurity", "infosec", "cyberattaque"],
       ["cybersécurité", '"cyberattaque"', "piratage informatique faille"],
       ["cybersecurity", "cyberattack data breach", "ransomware attack"],
       ["cybersecurite", "cybersecurity", "cyberattaque", "cyberattack", "piratage", "hacker",
        "ransomware", "rancongiciel", "faille", "vulnerabilite", "vulnerability", "data breach",
        "fuite de donnees", "malware", "phishing", "hameconnage"]),
    _t("football",
       ["foot", "soccer", "ligue 1", "ligue des champions", "mercato"],
       ["football", "football résultats match", "ligue 1 mercato"],
       ["football soccer news", "soccer match results", "premier league"],
       ["football", "foot", "soccer", "ligue 1", "ligue des champions", "champions league",
        "premier league", "buteur", "mercato", "equipe de france", "coupe du monde", "match", "entraineur"],
       ["football americain", "nfl", "super bowl"]),
    _t("cinéma",
       ["cinema", "film", "films", "movie", "movies", "box office", "box-office"],
       ["cinéma", "film sortie en salles", "box-office"],
       ["movies", "film box office news", "new movie release"],
       ["cinema", "film", "films", "movie", "box-office", "box office", "realisateur", "director",
        "acteur", "actrice", "festival de cannes", "salles", "bande-annonce", "trailer", "oscar", "cesar"]),
    _t("musique",
       ["music", "album", "albums", "concert", "concerts", "chanson", "chansons", "rap", "rock"],
       ["musique", "sortie album concert", "chanson clip"],
       ["music news", "new album release", "concert tour"],
       ["musique", "music", "album", "concert", "chanson", "chanteur", "chanteuse", "rappeur", "rap",
        "single", "tournee", "festival", "clip", "artiste"]),
    _t("économie",
       ["economie", "finance", "bourse", "economy", "inflation", "marches financiers"],
       ["économie", "économie inflation croissance", "bourse marchés financiers"],
       ["economy news", "inflation markets", "stock market"],
       ["economie", "economy", "inflation", "croissance", "pib", "gdp", "bourse", "marches financiers",
        "banque centrale", "taux d'interet", "chomage", "entreprise", "stock market"]),
    _t("politique",
       ["politics", "gouvernement", "elections", "election", "parlement"],
       ["politique", "actualité politique gouvernement", "élections"],
       ["politics news", "government election", "parliament"],
       ["politique", "politics", "gouvernement", "government", "election", "elections", "parlement",
        "assemblee nationale", "senat", "ministre", "president", "depute", "parti"]),
    _t("santé",
       ["sante", "medecine", "health", "medical", "maladie", "medicine"],
       ["santé", "santé médecine étude", "maladie traitement"],
       ["health news", "medicine study", "disease treatment"],
       ["sante", "health", "medecine", "medical", "maladie", "traitement", "vaccin", "hopital",
        "patient", "etude clinique", "medicament", "virus", "cancer", "oms"]),
    _t("climat et environnement",
       ["climat", "environnement", "ecologie", "climate", "changement climatique",
        "rechauffement climatique", "biodiversite"],
       ['"changement climatique"', "climat environnement écologie", "réchauffement climatique"],
       ["climate change", "environment news", "global warming"],
       ["climat", "climate", "environnement", "environment", "ecologie", "rechauffement", "carbone",
        "emissions", "biodiversite", "energie renouvelable", "pollution", "gaz a effet de serre"]),
    _t("espace",
       ["space", "astronomie", "nasa", "spatial", "fusee", "astronomy", "astronautique"],
       ["espace astronomie", "fusée satellite NASA", "astronomie découverte"],
       ["space news", "NASA rocket satellite", "astronomy discovery"],
       ["spatial", "espace", "nasa", "esa", "spacex", "fusee", "satellite", "astronaute", "astronomie",
        "mars", "lune", "telescope", "orbite", "cosmos", "space"],
       ["espace vert", "espaces verts", "espace de travail", "espace client", "espace public",
        "espace personnel", "espace de coworking"]),
    _t("technologie",
       ["tech", "technologies", "high-tech", "hightech", "technology", "innovation", "numerique"],
       ["technologie", "high-tech innovation", "nouveautés tech"],
       ["technology news", "tech innovation", "new gadgets"],
       ["technologie", "technology", "tech", "high-tech", "innovation", "numerique", "smartphone",
        "ordinateur", "logiciel", "application", "startup", "puce", "processeur", "apple", "google",
        "microsoft"]),
    _t("automobile",
       ["auto", "voiture", "voitures", "car", "cars", "vehicule", "automotive", "voiture electrique"],
       ["automobile", "voiture électrique nouveautés", "actualité auto constructeurs"],
       ["automotive news", "electric cars", "car makers"],
       ["automobile", "automotive", "voiture", "voitures", "vehicule", "electrique", "moteur",
        "constructeur", "tesla", "renault", "peugeot", "stellantis", "conduite", "essai"]),
    _t("cryptomonnaie",
       ["crypto", "cryptos", "bitcoin", "ethereum", "blockchain", "cryptocurrency", "crypto-monnaie",
        "cryptomonnaies"],
       ["cryptomonnaie", "bitcoin ethereum cours", "blockchain actualité"],
       ["cryptocurrency", "bitcoin ethereum price", "blockchain news"],
       ["crypto", "cryptomonnaie", "cryptomonnaies", "bitcoin", "ethereum", "blockchain", "token",
        "binance", "stablecoin", "nft", "portefeuille crypto"]),
    _t("sport",
       ["sports", "olympique", "olympiques", "jeux olympiques", "tennis", "rugby", "basket",
        "formule 1", "f1", "cyclisme"],
       ["sport actualité", "résultats sportifs match", "championnat tournoi"],
       ["sports news", "match results", "championship tournament"],
       ["sport", "sports", "match", "championnat", "tournoi", "athlete", "victoire", "equipe",
        "olympique", "tennis", "rugby", "basket", "formule 1", "cyclisme", "medaille"]),
]

_THEME_INDEX = None


def _theme_index():
    global _THEME_INDEX
    if _THEME_INDEX is None:
        idx = []
        for e in THESAURUS:
            for term in [e["canon"]] + e["aliases"]:
                idx.append((normalize(term), e))
        _THEME_INDEX = idx
    return _THEME_INDEX


def match_theme(theme: str) -> dict[str, Any] | None:
    n = normalize(theme)
    if not n:
        return None
    idx = _theme_index()
    for term, e in idx:
        if n == term:
            return e
    for term, e in sorted(idx, key=lambda x: -len(x[0])):
        if len(term) >= 4 and _has(n, term):
            return e
    close = difflib.get_close_matches(n, [t for t, _ in idx], n=1, cutoff=0.82)
    if close:
        return next(e for t, e in idx if t == close[0])
    return None


@dataclass
class SearchProfile:
    queries_fr: list[str]
    queries_en: list[str]
    phrases: list[str]
    exact_phrases: list[str]
    keywords: list[str]
    exclude: list[str]
    q_tokens: list[str]
    q_need: int = 0
    min_score: int = 2
    strict: bool = True
    entry_name: str = ""
    cutoff_date: datetime | None = None
    include_domains: list[str] = field(default_factory=list)
    exclude_domains: list[str] = field(default_factory=list)

    def tasks(self, pays: str) -> list[tuple[str, str]]:
        en = self.queries_en or self.queries_fr
        mkt = PAYS.get(pays, "")
        if mkt:
            qs = self.queries_fr if mkt.startswith("fr") else en
            return [(q, mkt) for q in qs[:3]]
        return [(q, "fr-FR") for q in self.queries_fr[:2]] + [(q, "en-US") for q in en[:2]]

    def allows_url(self, url: str) -> bool:
        dom = domain_of(url)
        if not dom:
            return False
        if self.exclude_domains and any(dom == d or dom.endswith("." + d) for d in self.exclude_domains):
            return False
        if self.include_domains and not any(dom == d or dom.endswith("." + d) for d in self.include_domains):
            return False
        return True

    def allows_date(self, pub: datetime | None) -> bool:
        if self.cutoff_date is None:
            return True
        if pub is None:
            return not self.strict
        return pub >= self.cutoff_date

    def accepts(self, raw_score: int, matched_q: int, exact_ok: bool) -> bool:
        if not exact_ok:
            return False
        if not self.strict:
            return True
        if self.min_score <= 0 and not self.q_tokens:
            return True
        return raw_score >= self.min_score and matched_q >= self.q_need


def build_profile(request: SearchRequest, english_hint: str = "") -> SearchProfile:
    exact_t, neg_t, clean_theme = parse_advanced_query(request.theme)
    exact_q, neg_q, clean_question = parse_advanced_query(request.question)
    exact_phrases = exact_t + exact_q
    user_excludes = neg_t + neg_q

    theme_n = normalize(clean_theme)
    entry = match_theme(clean_theme)

    phrases, keywords, exclude = [], [], list(user_excludes)
    if entry:
        known = {normalize(entry["canon"])} | {normalize(a) for a in entry["aliases"]}
        phrases = [normalize(entry["canon"])] + [normalize(a) for a in entry["aliases"] if len(a) >= 4]
        keywords = [normalize(k) for k in entry["keywords"]]
        exclude += [normalize(x) for x in entry["exclude"] if normalize(x) not in theme_n]
        if theme_n and theme_n not in known:
            phrases.append(theme_n)
            keywords += tokens(clean_theme)
    elif theme_n:
        phrases = [theme_n]
        keywords = tokens(clean_theme)
        if english_hint and not clean_question:
            phrases.append(normalize(english_hint))
            keywords += tokens(english_hint)

    q_tokens = tokens(clean_question)
    q_need = 0 if not q_tokens else (1 if len(q_tokens) <= 3 else math.ceil(len(q_tokens) * 0.4))
    if clean_question and english_hint:
        q_tokens = q_tokens + [t for t in tokens(english_hint) if t not in q_tokens]

    canon = entry["canon"] if entry else clean_theme
    if clean_question or exact_q:
        base_q = f"{request.question} {canon}".strip() if clean_theme else request.question.strip()
        fr = [base_q, request.question.strip()] if clean_theme else [request.question.strip()]
        en_q = english_hint.strip() or request.question.strip()
        en = [f"{en_q} {entry['en'][0].strip(chr(34))}" if entry and clean_theme else en_q, en_q]
    elif clean_theme or exact_t:
        if entry:
            fr, en = list(entry["fr"]), list(entry["en"])
        else:
            fr = [request.theme.strip(), f"{clean_theme} actualité".strip()]
            en = [f'"{english_hint}"', f"{english_hint} news"] if english_hint else list(fr)
        if request.pays:
            fr.insert(1, f"{fr[0]} {request.pays}")
    else:
        fr = [f"{request.pays} actualité", request.pays]
        en = [f"{request.pays} news"]

    # Ajout des opérateurs site: si un domaine unique est spécifié
    inc_doms = [d.strip().lower().lstrip(".") for d in request.include_domains.split(",") if d.strip()]
    exc_doms = [d.strip().lower().lstrip(".") for d in request.exclude_domains.split(",") if d.strip()]
    if len(inc_doms) == 1:
        fr = [f"site:{inc_doms[0]} {q}" for q in fr]
        en = [f"site:{inc_doms[0]} {q}" for q in en]

    def dedupe(items):
        seen, out = set(), []
        for x in items:
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        return out

    delta = PERIODS.get(request.period, ("", None))[1]
    cutoff = (datetime.now(timezone.utc) - delta) if delta else None

    return SearchProfile(
        queries_fr=dedupe(fr),
        queries_en=dedupe(en),
        phrases=dedupe(phrases),
        exact_phrases=dedupe(exact_phrases),
        keywords=dedupe(keywords),
        exclude=dedupe(exclude),
        q_tokens=q_tokens,
        q_need=q_need,
        min_score=2 if (clean_theme or clean_question or exact_phrases) else 0,
        strict=request.strict,
        entry_name=entry["canon"] if entry else "",
        cutoff_date=cutoff,
        include_domains=inc_doms,
        exclude_domains=exc_doms,
    )


def relevance(profile: SearchProfile, title: str = "", summary: str = "", body: str = "") -> tuple[int, int, bool, int]:
    """Calcule (score_brut, mots_question_trouvés, exact_ok, score_normalisé_0_100).
    Le titre a un poids double par rapport au corps du texte."""
    n_title = normalize(title)
    n_rest = normalize(f"{summary} {body}")
    full = f"{n_title} {n_rest}".strip()

    # Vérification des expressions exactes imposées entre guillemets
    exact_ok = all(_has(full, ep) for ep in profile.exact_phrases) if profile.exact_phrases else True

    raw = 0
    if any(_has(n_title, p) for p in profile.phrases):
        raw += 5
    elif any(_has(n_rest, p) for p in profile.phrases):
        raw += 3

    kw_title = sum(1 for k in profile.keywords if _has(n_title, k))
    kw_rest = sum(1 for k in profile.keywords if _has(n_rest, k))
    raw += min(kw_title * 2 + kw_rest, 7)

    # Pénalité forte pour les termes exclus
    raw -= 6 * sum(1 for x in profile.exclude if _has(full, x))

    matched_q = sum(1 for t in profile.q_tokens if _has(full, t))
    q_title = sum(1 for t in profile.q_tokens if _has(n_title, t))
    raw += 2 * matched_q + q_title

    if profile.exact_phrases and exact_ok:
        raw += 4 * len(profile.exact_phrases)

    # Normalisation sur 100
    max_possible = max(8, 5 + 7 + 3 * len(profile.q_tokens) + 4 * len(profile.exact_phrases))
    pct = max(0, min(100, int(round((raw / max_possible) * 100))))
    return raw, matched_q, exact_ok, pct


def are_similar_titles(t1: str, t2: str) -> bool:
    """Détecte si deux titres couvrent la même dépêche (dédoublonnage sémantique)."""
    n1, n2 = normalize(t1), normalize(t2)
    if not n1 or not n2:
        return False
    if n1 == n2:
        return True
    if difflib.SequenceMatcher(None, n1, n2).ratio() >= 0.78:
        return True
    tok1, tok2 = set(tokens(n1)), set(tokens(n2))
    if len(tok1) >= 4 and len(tok2) >= 4:
        jaccard = len(tok1 & tok2) / len(tok1 | tok2)
        if jaccard >= 0.62:
            return True
    return False


# --------------------------------------------------------------------------- #
# Client Tor strict avec cache de session
# --------------------------------------------------------------------------- #


class TorError(Exception):
    pass


def detect_tor_port() -> int | None:
    for port in TOR_PORTS:
        try:
            with socket.create_connection((TOR_HOST, port), timeout=2):
                return port
        except OSError:
            continue
    return None


class TorClient:
    """Passerelle réseau obligatoire via Tor (SOCKS5h) avec cache mémoire léger."""

    def __init__(self):
        self.session: requests.Session | None = None
        self.ip: str = ""
        self.port: int | None = None
        self._cache: dict[str, tuple[int, dict[str, str], bytes]] = {}
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self.session is not None

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def connect(self) -> str:
        self.session = None
        self.port = detect_tor_port()
        if self.port is None:
            raise TorError(
                "Tor introuvable sur 127.0.0.1:9050 / 9150.\n"
                "Lance le service Tor ou ouvre Tor Browser."
            )
        s = requests.Session()
        s.trust_env = False
        proxy = f"socks5h://{TOR_HOST}:{self.port}"
        s.proxies = {"http": proxy, "https": proxy}
        s.headers.update(HEADERS)
        try:
            data = s.get(TOR_CHECK_URL, timeout=SEARCH_TIMEOUT).json()
        except Exception as exc:
            raise TorError(f"Vérification Tor impossible : {exc}")
        if not data.get("IsTor"):
            raise TorError("Le trafic ne sort pas par Tor : recherche bloquée.")
        self.ip = data.get("IP", "")
        self.session = s
        return self.ip

    def get(self, url: str, timeout: int = SEARCH_TIMEOUT, use_cache: bool = False, **kw):
        if self.session is None:
            raise TorError("Tor n'est pas connecté.")
        if use_cache and not kw:
            with self._lock:
                cached = self._cache.get(url)
            if cached is not None:
                resp = requests.Response()
                resp.status_code = cached[0]
                resp.headers.update(cached[1])
                resp._content = cached[2]
                resp.url = url
                return resp

        resp = self.session.get(url, timeout=timeout, **kw)
        if use_cache and not kw and resp.ok and len(resp.content) < 2_000_000:
            with self._lock:
                if len(self._cache) > 150:
                    self._cache.clear()
                self._cache[url] = (resp.status_code, dict(resp.headers), resp.content)
        return resp

    def post(self, url: str, timeout: int = SEARCH_TIMEOUT, **kw):
        if self.session is None:
            raise TorError("Tor n'est pas connecté.")
        return self.session.post(url, timeout=timeout, **kw)


# --------------------------------------------------------------------------- #
# Système de modules (plugins) extensible
# --------------------------------------------------------------------------- #


class Module:
    kind: str = ""
    name: str = ""
    label: str = ""
    description: str = ""
    version: str = "1.0"
    author: str = ""
    default_enabled: bool = True
    # Permet aux modules d'exposer des options configurables dans l'UI :
    # ex: {"api_key": {"label": "Clé API", "default": "", "secret": True}}
    settings_schema: dict[str, dict[str, Any]] = {}

    def configure(self, config: dict[str, Any]) -> None:
        """Reçoit la configuration utilisateur enregistrée pour ce module."""
        self.config = config


class SourceModule(Module):
    kind = "source"

    def search(self, tor: TorClient, query: str, market: str, limit: int) -> list[dict[str, Any]]:
        raise NotImplementedError


class ExporterModule(Module):
    kind = "exporter"
    extension: str = ""

    def export(self, record: SearchRecord, articles: list[Article], out_dir: Path) -> Path | str:
        raise NotImplementedError


class TranslatorModule(Module):
    kind = "translator"

    def translate(self, tor: TorClient, text: str, target: str = "fr") -> tuple[str, str]:
        raise NotImplementedError


class Settings:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()


class ModuleManager:
    BASES = (Module, SourceModule, ExporterModule, TranslatorModule)

    def __init__(self, folder: str | Path, settings: Settings):
        self.folder = Path(folder)
        self.settings = settings
        self.modules: dict[str, Module] = {}
        self.errors: list[str] = []

    def load(self) -> None:
        self.modules, self.errors = {}, []
        if not self.folder.is_dir():
            self.errors.append(f"Dossier de modules introuvable : {self.folder}")
            return
        for path in sorted(self.folder.glob("*.py")):
            if path.name.startswith("_"):
                continue
            mod_name = f"full_info_modules.{path.stem}"
            try:
                spec = importlib.util.spec_from_file_location(mod_name, path)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = mod
                spec.loader.exec_module(mod)
                found = False
                for _, cls in inspect.getmembers(mod, inspect.isclass):
                    if (cls.__module__ == mod_name and issubclass(cls, Module)
                            and cls not in self.BASES and cls.name):
                        if cls.name in self.modules:
                            self.errors.append(f"{path.name} : nom déjà utilisé « {cls.name} »")
                            continue
                        instance = cls()
                        instance.configure(self.get_module_config(cls.name))
                        self.modules[cls.name] = instance
                        found = True
                if not found:
                    self.errors.append(f"{path.name} : aucun module valide trouvé")
            except Exception as exc:
                self.errors.append(f"{path.name} : {exc}")

    def get_module_config(self, name: str) -> dict[str, Any]:
        mod = self.modules.get(name)
        saved = dict(self.settings.get("module_configs", {}).get(name, {}))
        if mod and getattr(mod, "settings_schema", None):
            for k, meta in mod.settings_schema.items():
                saved.setdefault(k, meta.get("default", ""))
        return saved

    def set_module_config(self, name: str, config: dict[str, Any]) -> None:
        all_cfg = dict(self.settings.get("module_configs", {}))
        all_cfg[name] = config
        self.settings.set("module_configs", all_cfg)
        mod = self.modules.get(name)
        if mod:
            mod.configure(config)

    def is_enabled(self, name: str) -> bool:
        mod = self.modules.get(name)
        if mod is None:
            return False
        return bool(self.settings.get("modules", {}).get(name, mod.default_enabled))

    def set_enabled(self, name: str, value: bool) -> None:
        states = dict(self.settings.get("modules", {}))
        states[name] = bool(value)
        self.settings.set("modules", states)

    def _of_kind(self, kind: str, enabled_only: bool) -> list[Any]:
        return [m for m in self.modules.values()
                if m.kind == kind and (not enabled_only or self.is_enabled(m.name))]

    def sources(self, enabled_only: bool = False) -> list[SourceModule]:
        return self._of_kind("source", enabled_only)

    def exporters(self, enabled_only: bool = False) -> list[ExporterModule]:
        return self._of_kind("exporter", enabled_only)

    def translators(self, enabled_only: bool = False) -> list[TranslatorModule]:
        return self._of_kind("translator", enabled_only)


# --------------------------------------------------------------------------- #
# Extraction d'article haute précision & résumé
# --------------------------------------------------------------------------- #


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for n in names:
        tag = (soup.find("meta", attrs={"property": n})
               or soup.find("meta", attrs={"name": n})
               or soup.find("meta", attrs={"itemprop": n}))
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def _jsonld_nodes(soup: BeautifulSoup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                yield x
                if "@graph" in x:
                    stack.append(x["@graph"])


def _names(a: Any) -> list[str]:
    if isinstance(a, str):
        return [a]
    if isinstance(a, dict):
        return [a["name"]] if isinstance(a.get("name"), str) else []
    if isinstance(a, list):
        out = []
        for i in a:
            out += _names(i)
        return out
    return []


def _clean_author(a: str) -> str:
    a = re.sub(r"\s+", " ", a or "").strip()
    a = re.sub(r"^(par|by|de|écrit par|written by)\s+", "", a, flags=re.I)
    if not a or len(a) > 80 or "http" in a.lower() or "@" in a:
        return ""
    return a


def extract_article(content: bytes) -> dict[str, Any]:
    soup = BeautifulSoup(content, "html.parser")

    date = None
    author = ""
    for node in _jsonld_nodes(soup):
        if not date and node.get("datePublished"):
            date = parse_date(node["datePublished"])
        if not author and node.get("author"):
            names = [_clean_author(n) for n in _names(node["author"])]
            author = ", ".join(n for n in names if n)[:80]

    if not date:
        date = parse_date(_meta(
            soup, "article:published_time", "og:article:published_time",
            "datePublished", "pubdate", "publish-date", "date",
            "DC.date.issued", "parsely-pub-date",
        ))
    if not date:
        t = soup.find("time", attrs={"datetime": True})
        if t:
            date = parse_date(t["datetime"])

    if not author:
        author = _clean_author(_meta(soup, "author", "article:author", "twitter:creator"))
    if not author:
        node = (soup.find(attrs={"rel": "author"}) or soup.find(attrs={"itemprop": "author"})
                or soup.find(class_=re.compile(r"byline|author", re.I)))
        if node:
            author = _clean_author(node.get_text(" ", strip=True))

    description = _meta(soup, "og:description", "description", "twitter:description")
    title = _meta(soup, "og:title", "twitter:title") or (soup.title.get_text(strip=True) if soup.title else "")

    for bad in soup(["script", "style", "nav", "footer", "aside", "form", "header", "noscript", "iframe"]):
        bad.decompose()

    container = soup.find("article") or soup.find("main") or soup
    paras = [p.get_text(" ", strip=True) for p in container.find_all("p")]
    clean_paras = [p for p in paras if len(p) >= 50 and not re.search(r"(cookies?|abonnez-vous|newsletter|tous droits réservés)", p, re.I)]
    text = "\n\n".join(clean_paras)[:25000]

    return {
        "date": date,
        "author": author,
        "description": description,
        "title": title,
        "text": text,
    }


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def summarize(text: str, max_sentences: int = 3, max_chars: int = 500) -> str:
    flat = re.sub(r"\s+", " ", text or "").strip()
    if not flat:
        return ""
    sentences = [
        s.strip() for s in re.split(r"(?<=[.!?…])\s+(?=[A-ZÀ-ÖØ-Þ0-9«\"“])", flat)
        if 40 <= len(s.strip()) <= 420
    ][:45]
    if not sentences:
        return _cut(flat, max_chars)

    def words(s: str) -> list[str]:
        return [w for w in re.findall(r"\w{3,}", s.lower()) if w not in STOP]

    freq = Counter(w for s in sentences for w in words(s))
    scored = []
    for i, s in enumerate(sentences):
        ws = words(s)
        if not ws:
            continue
        score = sum(freq[w] for w in ws) / (len(ws) ** 0.8)
        if i < 2:
            score *= 1.4
        scored.append((score, i, s))
    best = sorted(scored, reverse=True)[:max_sentences]
    summary = " ".join(s for _, _, s in sorted(best, key=lambda x: x[1]))
    return _cut(summary, max_chars)


def enrich(tor: TorClient, cand: dict[str, Any]) -> Article:
    art = Article(
        title=cand["title"],
        url=cand["url"],
        source=cand.get("source") or domain_of(cand["url"]),
        published=cand.get("published"),
    )
    text, desc = "", cand.get("snippet", "")
    try:
        r = tor.get(cand["url"], timeout=ARTICLE_TIMEOUT, use_cache=True)
        if r.ok and "html" in r.headers.get("Content-Type", "").lower():
            info = extract_article(r.content[:1_500_000])
            art.author = info["author"]
            art.published = info["date"] or art.published
            text = info["text"]
            desc = info["description"] or desc
            if not art.title:
                art.title = info["title"]
    except Exception:
        pass

    flat_text = re.sub(r"\s+", " ", text).strip()
    if len(flat_text) > 300:
        art.summary = summarize(flat_text)
        art.full_text = text[:6000]
        word_count = len(flat_text.split())
        art.reading_time = max(1, round(word_count / 220))
    else:
        art.summary = _cut(desc or flat_text, 500)
        art.full_text = desc or flat_text
        art.reading_time = 1

    if not art.summary:
        art.summary = "Résumé indisponible (page protégée ou inaccessible via Tor)."
    art.text = " ".join(x for x in (flat_text[:4500], desc) if x)
    return art


# --------------------------------------------------------------------------- #
# Stockage local SQLite
# --------------------------------------------------------------------------- #


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        with closing(self._connect()):
            pass

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path)
        con.execute("""CREATE TABLE IF NOT EXISTS searches(
            id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, theme TEXT, pays TEXT,
            question TEXT, query TEXT, requested INTEGER, sources TEXT DEFAULT '')""")
        con.execute("""CREATE TABLE IF NOT EXISTS articles(
            id INTEGER PRIMARY KEY AUTOINCREMENT, search_id INTEGER, title TEXT, url TEXT,
            source TEXT, author TEXT, published TEXT, summary TEXT,
            lang TEXT DEFAULT '', original_title TEXT DEFAULT '',
            score INTEGER DEFAULT 0, reading_time INTEGER DEFAULT 1, full_text TEXT DEFAULT '',
            UNIQUE(search_id, url))""")

        # Migrations automatiques depuis v3.x
        scols = {row[1] for row in con.execute("PRAGMA table_info(searches)")}
        if "sources" not in scols:
            con.execute("ALTER TABLE searches ADD COLUMN sources TEXT DEFAULT ''")

        acols = {row[1] for row in con.execute("PRAGMA table_info(articles)")}
        for col, ddl in (
            ("lang", "TEXT DEFAULT ''"),
            ("original_title", "TEXT DEFAULT ''"),
            ("score", "INTEGER DEFAULT 0"),
            ("reading_time", "INTEGER DEFAULT 1"),
            ("full_text", "TEXT DEFAULT ''"),
        ):
            if col not in acols:
                con.execute(f"ALTER TABLE articles ADD COLUMN {col} {ddl}")
        return con

    def save_search(self, theme: str, pays: str, question: str, query: str,
                    requested: int, sources: str, articles: list[Article]) -> SearchRecord:
        created = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as con:
            cur = con.execute(
                "INSERT INTO searches(created,theme,pays,question,query,requested,sources)"
                " VALUES(?,?,?,?,?,?,?)",
                (created, theme, pays, question, query, requested, sources),
            )
            sid = cur.lastrowid or 0
            for a in articles:
                con.execute(
                    "INSERT OR IGNORE INTO articles(search_id,title,url,source,author,published,summary,"
                    "lang,original_title,score,reading_time,full_text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, a.title, a.url, a.source, a.author,
                     a.published.isoformat() if a.published else "", a.summary,
                     a.lang, a.original_title, a.score, a.reading_time, a.full_text),
                )
            con.commit()
        return SearchRecord(sid, created, theme, pays, question, query, requested, sources, len(articles))

    def list_searches(self) -> list[SearchRecord]:
        with closing(self._connect()) as con:
            rows = con.execute(
                "SELECT s.id,s.created,s.theme,s.pays,s.question,s.query,s.requested,s.sources,"
                "(SELECT COUNT(*) FROM articles a WHERE a.search_id=s.id) "
                "FROM searches s ORDER BY s.id DESC"
            ).fetchall()
        return [SearchRecord(*r) for r in rows]

    def load_articles(self, search_id: int) -> list[Article]:
        with closing(self._connect()) as con:
            rows = con.execute(
                "SELECT title,url,source,author,published,summary,lang,original_title,score,reading_time,full_text "
                "FROM articles WHERE search_id=?",
                (search_id,),
            ).fetchall()
        return sort_articles([
            Article(t, u, s, a, parse_date(p), sm, lg or "", ot or "", sc or 0, rt or 1, ft or "")
            for t, u, s, a, p, sm, lg, ot, sc, rt, ft in rows
        ], mode="date")

    def delete_search(self, search_id: int) -> None:
        with closing(self._connect()) as con:
            con.execute("DELETE FROM articles WHERE search_id=?", (search_id,))
            con.execute("DELETE FROM searches WHERE id=?", (search_id,))
            con.commit()


# --------------------------------------------------------------------------- #
# Dashboard HTML hors ligne
# --------------------------------------------------------------------------- #

DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="fr" data-theme="__THEME__"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Full Info - Dashboard</title>
<style>
:root{--paper:#f3eee2;--card:#faf6ec;--ink:#141414;--red:#a4161a;--grey:#6b665c;--border:#2b2823}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#14120e;--card:#1d1a14;--ink:#ece6d6;--red:#e0555a;--grey:#a29b8a;--border:#5c5548}}
:root[data-theme="dark"]{--paper:#14120e;--card:#1d1a14;--ink:#ece6d6;--red:#e0555a;--grey:#a29b8a;--border:#5c5548}
:root[data-theme="light"]{--paper:#f3eee2;--card:#faf6ec;--ink:#141414;--red:#a4161a;--grey:#6b665c;--border:#2b2823}
.tg{font:inherit;font-size:12px;letter-spacing:.14em;text-transform:uppercase;background:none;border:1px solid var(--ink);color:var(--ink);padding:2px 10px;cursor:pointer;margin-right:12px}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:Georgia,"Times New Roman",serif;line-height:1.5}
.wrap{max-width:1200px;margin:0 auto;padding:0 20px 60px}
.top{border-top:6px solid var(--ink);margin-top:14px}
.meta{display:flex;justify-content:space-between;font-size:12px;letter-spacing:.14em;text-transform:uppercase;padding:8px 0;color:var(--grey)}
h1{font-size:clamp(40px,8vw,84px);margin:0;text-align:center;letter-spacing:.06em;font-weight:900;line-height:1}
.tag{text-align:center;font-style:italic;color:var(--grey);padding:8px 0 12px}
.rule{border-top:1px solid var(--ink);border-bottom:3px solid var(--ink);height:6px;margin-bottom:18px}
.stats{display:flex;gap:28px;flex-wrap:wrap;justify-content:center;margin-bottom:18px}
.stat b{display:block;font-size:30px;text-align:center}
.stat span{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--grey)}
.tools{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}
.tools input,.tools select{font:inherit;padding:8px 10px;background:var(--card);color:var(--ink);border:1px solid var(--ink);flex:1;min-width:200px}
.cols{column-count:2;column-gap:34px;column-rule:1px solid var(--border)}
@media (max-width:800px){.cols{column-count:1}}
.card{break-inside:avoid;padding:0 0 18px;margin:0 0 18px;border-bottom:1px solid var(--grey)}
.kicker{color:var(--red);font-size:11px;letter-spacing:.16em;text-transform:uppercase;font-weight:700}
.card h2{font-size:21px;line-height:1.2;margin:4px 0 6px}
.by{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--grey);margin-bottom:8px}
.card p{margin:0 0 8px;text-align:justify;hyphens:auto}
.card a{font-family:Consolas,monospace;font-size:12px;color:var(--red);word-break:break-all}
.chip{font-size:11px;color:var(--grey);font-style:italic}
.empty{text-align:center;color:var(--grey);padding:40px}
footer{text-align:center;font-size:12px;color:var(--grey);border-top:3px double var(--ink);padding-top:12px;margin-top:30px}
</style></head><body><div class="wrap">
<div class="top"><div class="meta"><span>Édition hors ligne · Archives Tor</span><span><button class="tg" id="tg" type="button"></button>__UPDATED__</span></div>
<h1>FULL INFO</h1><div class="tag">Le quotidien de l'information anonyme</div></div>
<div class="rule"></div>
<div class="stats"><div class="stat"><b>__NB_SEARCHES__</b><span>enquêtes</span></div>
<div class="stat"><b>__NB_ARTICLES__</b><span>articles</span></div>
<div class="stat"><b>__NB_SOURCES__</b><span>sources</span></div></div>
<div class="tools"><input id="q" type="search" placeholder="Filtrer les articles…">
<select id="s"><option value="">Toutes les enquêtes</option>__OPTIONS__</select></div>
<div class="cols" id="list">__CARDS__</div>
<div class="empty" id="empty" style="display:none">Aucun article ne correspond au filtre.</div>
<footer>Les liens sources s'ouvrent depuis ce fichier local : utilise Tor Browser pour les consulter de façon anonyme.</footer>
</div>
<script>
const q=document.getElementById('q'),s=document.getElementById('s'),
cards=[...document.querySelectorAll('.card')],empty=document.getElementById('empty');
function f(){const t=q.value.toLowerCase().trim(),v=s.value;let n=0;
cards.forEach(c=>{const ok=(!v||c.dataset.search===v)&&(!t||c.dataset.text.includes(t));
c.style.display=ok?'':'none';if(ok)n++});empty.style.display=n?'none':'block'}
q.addEventListener('input',f);s.addEventListener('change',f);
const root=document.documentElement,tg=document.getElementById('tg');
function cur(){const t=root.dataset.theme;if(t==='dark'||t==='light')return t;
return matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'}
function lab(){tg.textContent=cur()==='dark'?'☀ Mode clair':'☾ Mode sombre'}
tg.addEventListener('click',()=>{root.dataset.theme=cur()==='dark'?'light':'dark';lab()});
lab();
</script></body></html>"""


def build_dashboard(storage: Storage, path: str | Path, theme: str = "") -> Path:
    with closing(storage._connect()) as con:
        searches = con.execute(
            "SELECT id,theme,pays,question,query FROM searches ORDER BY id DESC"
        ).fetchall()
        rows = con.execute(
            "SELECT title,url,source,author,published,summary,search_id,lang,original_title,score,reading_time "
            "FROM articles"
        ).fetchall()
    labels = {sid: search_label(t, p, q, qu) for sid, t, p, q, qu in searches}
    rows.sort(key=lambda r: r[4] or "", reverse=True)

    e = html.escape
    options = "".join(f'<option value="{sid}">{e(lbl)}</option>' for sid, lbl in labels.items())
    cards = []
    for title, url, source, author, published, summary, sid, lang, orig, score, rtime in rows:
        by = f"Par {author}" if author else "Auteur non indiqué"
        translated = ""
        if lang and lang != "fr":
            translated = (
                f'<div class="chip">Traduit en français · langue d\'origine : {e(lang_name(lang))}'
                + (f' · titre original : {e(orig)}' if orig else "") + "</div>"
            )
        text_blob = e(f"{title} {source} {author} {summary}".lower(), quote=True)
        badge = f" · Pertinence {score}%" if score else ""
        cards.append(
            f'<article class="card" data-search="{sid}" data-text="{text_blob}">'
            f'<div class="kicker">{e(source or domain_of(url))}{badge} · ~{rtime or 1} min</div>'
            f'<h2>{e(title)}</h2>'
            f'<div class="by">{e(by)} · {e(fmt_date(parse_date(published)))}</div>'
            f'<p>{e(summary)}</p>{translated}'
            f'<a href="{e(url, quote=True)}" rel="noopener noreferrer" target="_blank">{e(url)}</a>'
            f'<div class="chip">Enquête : {e(labels.get(sid, ""))}</div></article>'
        )

    page = (
        DASHBOARD_TEMPLATE
        .replace("__THEME__", theme if theme in ("dark", "light") else "")
        .replace("__UPDATED__", e(f"Mis à jour le {today_long()}"))
        .replace("__NB_SEARCHES__", str(len(searches)))
        .replace("__NB_ARTICLES__", str(len(rows)))
        .replace("__NB_SOURCES__", str(len({(r[2] or domain_of(r[1])) for r in rows})))
        .replace("__OPTIONS__", options)
        .replace("__CARDS__", "".join(cards) or '<div class="empty">Aucune archive pour le moment.</div>')
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Engine : orchestrateur principal
# --------------------------------------------------------------------------- #


class Engine:
    def __init__(self, data_dir: str | Path | None = None, modules_dir: str | Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings = Settings(self.data_dir / "settings.json")
        self.storage = Storage(self.data_dir / "full_info.db")
        self.tor = TorClient()
        self.modules = ModuleManager(modules_dir or MODULES_DIR, self.settings)
        self.modules.load()
        self.dashboard_path = self.data_dir / "dashboard.html"
        self.exports_dir = self.data_dir / "exports"

    def connect_tor(self) -> str:
        return self.tor.connect()

    def reload_modules(self) -> None:
        self.modules.load()

    def search(self, request: SearchRequest, progress: Callable[[int, int, str], None] | None = None) -> SearchResult:
        progress = progress or (lambda done, total, text: None)

        query = build_query(request.theme, request.pays, request.question)
        if not query:
            raise ValueError("Renseigne au moins un thème, un pays ou une question.")
        if not self.tor.connected:
            raise TorError("Tor n'est pas connecté : la recherche est bloquée.")
        sources = self.modules.sources(enabled_only=True)
        if not sources:
            raise ValueError("Aucun module source actif (voir l'onglet MODULES).")

        count = max(1, min(MAX_ARTICLES, int(request.count)))
        warnings: list[str] = []
        label = search_label(request.theme, request.pays, request.question, query)

        translator = next(iter(self.modules.translators(enabled_only=True)), None)
        if request.translate and translator is None:
            warnings.append("Aucun module de traduction actif : articles laissés dans leur langue d'origine.")

        # 1) Analyse sémantique et expansion
        progress(0, 1, "Analyse sémantique de la requête…")
        hint = ""
        _, _, clean_t = parse_advanced_query(request.theme)
        _, _, clean_q = parse_advanced_query(request.question)
        if translator and (clean_q or (clean_t and match_theme(clean_t) is None)):
            try:
                hint, _ = translator.translate(
                    self.tor, " ".join(x for x in (clean_q, clean_t) if x), "en"
                )
            except Exception:
                hint = ""
        profile = build_profile(request, hint)

        # 2) Interrogation parallèle des sources
        tasks = profile.tasks(request.pays)
        pairs = [(m, q, mk) for q, mk in tasks for m in sources][:16]
        progress(0, 1, f"Interrogation de {len(sources)} source(s) via Tor ({len(tasks)} requête(s))…")

        pool_size = max(count * 2, 25)

        def run_source(args):
            mod, q, mk = args
            try:
                return mod, mod.search(self.tor, q, mk, pool_size) or [], None
            except Exception as exc:
                return mod, [], exc

        by_url: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        raw_total = 0

        with ThreadPoolExecutor(max_workers=min(len(pairs), 6)) as pool:
            for mod, items, err in pool.map(run_source, pairs):
                if err is not None:
                    msg = f"{mod.label} : {err}"
                    if msg not in errors:
                        errors.append(msg)
                for c in items:
                    raw_total += 1
                    url = c.get("url", "")
                    if not url.startswith("http") or not profile.allows_url(url):
                        continue
                    pub = parse_date(c.get("published"))
                    if not profile.allows_date(pub):
                        continue
                    c["published"] = pub
                    key = url.split("#")[0].rstrip("/")
                    old = by_url.get(key)
                    if old is None or (not old.get("published") and pub):
                        by_url[key] = c
        warnings += errors

        candidates = list(by_url.values())
        if not candidates:
            return SearchResult(label, query, [], None, warnings, {"raw": raw_total, "unique": 0})

        # 3) Pré-tri & dédoublonnage préliminaire
        floor = datetime.min.replace(tzinfo=timezone.utc)
        for c in candidates:
            c["_rel"] = relevance(profile, c["title"], c["snippet"])

        candidates.sort(
            key=lambda c: (c["_rel"][3], c.get("published") or floor),
            reverse=True,
        )

        if request.deduplicate:
            unique_cands = []
            for c in candidates:
                if not any(are_similar_titles(c["title"], u["title"]) for u in unique_cands):
                    unique_cands.append(c)
            candidates = unique_cands

        good = [c for c in candidates if profile.accepts(c["_rel"][0], c["_rel"][1], c["_rel"][2])]
        good_ids = {id(c) for c in good}
        rest = [c for c in candidates if id(c) not in good_ids]
        maybe = [c for c in rest if c["_rel"][0] > 0 and c["_rel"][2]]
        bad = [c for c in rest if c["_rel"][0] <= 0 and c["_rel"][2]]

        ordered = good + maybe + (bad if not request.strict else [])
        if not ordered:
            warnings.append(
                f"Les {len(candidates)} candidats trouvés ont été écartés par les filtres : "
                "élargis la période, assouplis les mots-clés ou décoche « Résultats stricts »."
            )
            return SearchResult(label, query, [], None, warnings, {"raw": raw_total, "unique": len(candidates)})

        # 4) Extraction approfondie via Tor par lots
        cap = min(len(ordered), max(count * 3, 15), 60)
        accepted: list[tuple[int, Article]] = []
        rejected: list[tuple[int, Article]] = []
        pos = 0

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            while len(accepted) < count and pos < cap:
                size = max(WORKERS, count - len(accepted))
                batch = ordered[pos:min(pos + size, cap)]
                pos += len(batch)
                for art in pool.map(lambda c: enrich(self.tor, c), batch):
                    if not profile.allows_date(art.published):
                        continue
                    raw_s, mq, ex_ok, pct = relevance(profile, art.title, art.summary, art.text)
                    art.score = pct
                    if profile.accepts(raw_s, mq, ex_ok):
                        if not request.deduplicate or not any(are_similar_titles(art.title, a.title) for _, a in accepted):
                            accepted.append((raw_s, art))
                    elif ex_ok:
                        rejected.append((raw_s, art))
                found = min(len(accepted), count)
                progress(found, count, f"Analyse et extraction via Tor… {found}/{count} articles retenus")

        chosen = [a for _, a in sorted(accepted, key=lambda x: x[0], reverse=True)][:count]
        if len(chosen) < count:
            extra = [a for sc, a in sorted(rejected, key=lambda x: -x[0]) if sc > 0][:count - len(chosen)]
            if extra:
                warnings.append(f"{len(extra)} article(s) de pertinence secondaire ajouté(s).")
            chosen += extra
            if len(chosen) < count:
                warnings.append(f"Seulement {len(chosen)} article(s) pertinent(s) trouvé(s) pour « {label} ».")

        for a in chosen:
            a.text = ""

        if not chosen:
            return SearchResult(label, query, [], None, warnings, {"raw": raw_total, "unique": len(candidates)})

        # 5) Traduction en français
        if request.translate and translator is not None:
            self._translate_articles(translator, chosen, progress, warnings)

        chosen = sort_articles(chosen, mode=request.sort_by)

        # 6) Archivage & Dashboard
        record = self.storage.save_search(
            request.theme, request.pays, request.question, query, count,
            ",".join(m.name for m in sources), chosen,
        )
        self.build_dashboard()
        stats = {
            "raw": raw_total,
            "unique": len(candidates),
            "retained": len(chosen),
            "sources_used": len(sources),
        }
        return SearchResult(label, query, chosen, record, warnings, stats)

    def _translate_articles(self, translator: TranslatorModule, articles: list[Article],
                            progress: Callable[[int, int, str], None], warnings: list[str]) -> None:
        total, done, errors = len(articles), 0, []

        def work(art: Article) -> None:
            placeholder = art.summary.startswith("Résumé indisponible")
            if not placeholder and looks_french(f"{art.title}. {art.summary}"):
                art.lang = "fr"
                return
            try:
                probe = art.title if placeholder else art.summary
                probe_fr, lang = translator.translate(self.tor, probe, "fr")
                if lang and lang.split("-")[0] == "fr":
                    art.lang = "fr"
                    return
                if placeholder:
                    title_fr, summary_fr = probe_fr, art.summary
                else:
                    summary_fr = probe_fr
                    title_fr, _ = translator.translate(self.tor, art.title, "fr")
                art.original_title = art.title
                art.title = title_fr or art.title
                art.summary = summary_fr or art.summary
                art.lang = lang or "?"
            except Exception as exc:
                errors.append(str(exc))

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for _ in pool.map(work, articles):
                done += 1
                progress(done, total, f"Traduction en français… {done}/{total}")
        if errors:
            warnings.append(f"Traduction incomplète ({len(errors)} échec(s)) : {errors[0]}")

    def list_searches(self) -> list[SearchRecord]:
        return self.storage.list_searches()

    def load_articles(self, search_id: int) -> list[Article]:
        return self.storage.load_articles(search_id)

    def delete_search(self, search_id: int) -> None:
        self.storage.delete_search(search_id)
        self.build_dashboard()

    def build_dashboard(self) -> Path:
        theme = "light" if self.settings.get("theme", "sombre") == "clair" else "dark"
        return build_dashboard(self.storage, self.dashboard_path, theme)

    def export(self, exporter_name: str, record: SearchRecord, articles: list[Article]) -> Path:
        mod = self.modules.modules.get(exporter_name)
        if mod is None or mod.kind != "exporter":
            raise ValueError(f"Module d'export inconnu : {exporter_name}")
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        return Path(mod.export(record, articles, self.exports_dir))