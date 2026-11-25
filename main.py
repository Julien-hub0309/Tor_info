import argparse
import sys


def check_dependencies():
    missing = []
    for module, package in (("requests", "requests"), ("bs4", "beautifulsoup4"), ("socks", "PySocks")):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        print("[!] Dépendances manquantes : " + ", ".join(missing))
        print("    Installe-les avec :  pip install -r requirements.txt")
        sys.exit(1)


def parse_args():
    p = argparse.ArgumentParser(description="Full Info - recherche d'informations anonyme via Tor")
    p.add_argument("-t", "--theme", default="", help="thème (ex : jeux vidéo)")
    p.add_argument("-p", "--pays", default="", help="pays (ex : France)")
    p.add_argument("-q", "--question", default="", help="question précise ou opérateurs")
    p.add_argument("-n", "--nombre", type=int, default=15, help="nombre d'articles (défaut : 15)")
    p.add_argument("--periode", default="all", choices=["all", "24h", "7d", "30d", "1y"],
                   help="filtre temporel (défaut : all)")
    p.add_argument("--tri", default="relevance", choices=["relevance", "date"],
                   help="tri des résultats : relevance ou date (défaut : relevance)")
    p.add_argument("--domaines", default="", help="domaines autorisés séparés par virgule")
    p.add_argument("--exclure-domaines", default="", help="domaines exclus séparés par virgule")
    p.add_argument("--no-translate", action="store_true", help="ne pas traduire en français")
    p.add_argument("--tout", action="store_true", help="résultats non stricts")
    p.add_argument("--no-dedupe", action="store_true", help="désactiver la fusion des doublons")
    p.add_argument("--list-modules", action="store_true", help="liste les modules et quitte")
    p.add_argument("--dashboard", action="store_true", help="régénère le dashboard HTML et quitte")
    return p.parse_args()


def list_modules(engine):
    print(f"Dossier des modules : {engine.modules.folder}\n")
    for kind, title in (("source", "Sources"), ("translator", "Traduction"), ("exporter", "Exports")):
        print(f"{title} :")
        mods = [m for m in engine.modules.modules.values() if m.kind == kind]
        for m in mods:
            state = "actif  " if engine.modules.is_enabled(m.name) else "inactif"
            print(f"  [{state}] {m.name:<18} {m.label} (v{m.version}) - {m.description}")
        if not mods:
            print("  (aucun)")
        print()
    for err in engine.modules.errors:
        print(f"[!] {err}")


def cli_search(engine, args):
    from full_info import (
        MAX_ARTICLES,
        PAYS,
        SearchRequest,
        TorError,
        domain_of,
        fmt_date,
        lang_name,
        resolve_country,
    )

    pays = ""
    if args.pays:
        pays = resolve_country(args.pays)
        if not pays:
            print(f"[!] Pays inconnu « {args.pays} ». Disponibles : {', '.join(PAYS)}")
            return 2

    print("[*] Connexion à Tor…")
    try:
        ip = engine.connect_tor()
    except TorError as exc:
        print(f"[!] {exc}")
        return 1
    print(f"[+] Tor actif - IP de sortie : {ip}")

    request = SearchRequest(
        theme=args.theme,
        pays=pays,
        question=args.question,
        count=max(1, min(MAX_ARTICLES, args.nombre)),
        translate=not args.no_translate,
        strict=not args.tout,
        period=args.periode,
        sort_by=args.tri,
        include_domains=args.domaines,
        exclude_domains=args.exclure_domaines,
        deduplicate=not args.no_dedupe,
    )

    def progress(done, total, text):
        print(f"    {text}")

    try:
        result = engine.search(request, progress)
    except (ValueError, TorError) as exc:
        print(f"[!] {exc}")
        return 1

    for w in result.warnings:
        print(f"[!] {w}")
    if not result.articles:
        print("[-] Aucun article trouvé.")
        return 0

    print(f"\n=== {result.label} - {len(result.articles)} article(s) ===\n")
    for i, a in enumerate(result.articles, 1):
        by = f"par {a.author}" if a.author else "auteur non indiqué"
        print(f"{i}. [{a.score}% · ~{a.reading_time} min] {a.title}")
        tr = f" · traduit de : {lang_name(a.lang)}" if a.lang and a.lang != "fr" else ""
        print(f"   {a.source or domain_of(a.url)} · {by} · {fmt_date(a.published)}{tr}")
        print(f"   {a.summary}")
        print(f"   {a.url}\n")
    print(f"[+] Enquête archivée. Dashboard : {engine.dashboard_path}")
    return 0


def main():
    check_dependencies()
    args = parse_args()

    from full_info import Engine
    engine = Engine()

    if args.list_modules:
        list_modules(engine)
        return 0
    if args.dashboard:
        print(engine.build_dashboard())
        return 0
    if args.theme or args.pays or args.question:
        return cli_search(engine, args)

    try:
        import display
    except ImportError as exc:
        print(f"[!] Interface graphique indisponible ({exc}).")
        print("    Installe Textual avec :  pip install textual")
        return 1
    display.run(engine)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)