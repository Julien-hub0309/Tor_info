# -*- coding: utf-8 -*-
"""Module source : DuckDuckGo (version HTML). Pas de date dans les résultats :
la date est récupérée ensuite en lisant la page de l'article."""

from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from full_info import SourceModule, make_candidate

DDG_URL = "https://html.duckduckgo.com/html/"
KL_OVERRIDES = {"en-GB": "uk-en", "en-US": "us-en", "ja-JP": "jp-jp", "pt-BR": "br-pt"}


def _clean_link(href):
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return unquote(target[0])
    return href


class DuckDuckGo(SourceModule):
    name = "duckduckgo"
    label = "DuckDuckGo"
    description = "Résultats web DuckDuckGo. Complète Bing quand il y a peu d'actualités datées."
    version = "1.0"
    author = "Full Info"
    default_enabled = True

    def search(self, tor, query, market, limit):
        kl = KL_OVERRIDES.get(market, market.lower()) if market else "wt-wt"
        resp = tor.post(DDG_URL, data={"q": query, "kl": kl})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        out = []
        for block in soup.select("div.result"):
            a = block.select_one("a.result__a")
            if not a:
                continue
            snip = block.select_one(".result__snippet")
            out.append(make_candidate(
                title=a.get_text(strip=True),
                url=_clean_link(a.get("href", "")),
                snippet=snip.get_text(" ", strip=True) if snip else "",
            ))
            if len(out) >= limit:
                break
        return out
