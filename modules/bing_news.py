# -*- coding: utf-8 -*-
"""Module source : flux d'actualités Bing (dates fiables, triées par date)."""

import html
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from full_info import SourceModule, make_candidate, parse_date

BING_NEWS_URL = "https://www.bing.com/news/search"


class BingNews(SourceModule):
    name = "bing_news"
    label = "Bing Actualités"
    description = "Flux RSS d'actualités de Bing, avec dates de publication. Source principale."
    version = "1.0"
    author = "Full Info"
    default_enabled = True

    def search(self, tor, query, market, limit):
        market = market or "en-US"
        params = {
            "q": query,
            "format": "rss",
            "setmkt": market,
            "setlang": market.split("-")[0],
            "qft": 'sortbydate="1"',
        }
        resp = tor.get(BING_NEWS_URL, params=params)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        out = []
        for item in root.iter("item"):
            d = {c.tag.split("}")[-1]: (c.text or "") for c in item}
            link = d.get("link", "")
            qs = parse_qs(urlparse(link).query)
            url = unquote(qs["url"][0]) if "url" in qs else link
            snippet = BeautifulSoup(d.get("description", ""), "html.parser").get_text(" ", strip=True)
            out.append(make_candidate(
                title=html.unescape(d.get("title", "")),
                url=url,
                source=d.get("Source", ""),
                published=parse_date(d.get("pubDate")),
                snippet=snippet,
            ))
            if len(out) >= limit:
                break
        return out
