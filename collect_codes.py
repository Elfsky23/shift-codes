#!/usr/bin/env python3
"""
Collecteur de codes SHiFT : visite chaque site de sites.txt + la liste communautaire,
récupère TOUT ce qui ressemble à un code SHiFT et fusionne le tout, sans rien filtrer.

    python collect_codes.py                    # écrit shiftcodes.json
    python collect_codes.py --out autre.json

Le fichier produit garde l'historique : un code déjà vu n'est jamais supprimé.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
COMMUNITY_URL = "https://raw.githubusercontent.com/ugoogalizer/autoshift-codes/main/shiftcodes.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
# Codes SHiFT = 5 blocs de 5 caractères en MAJUSCULES (évite les bouts d'URL en minuscules)
CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z0-9]{5}(?:\s*[-–—]\s*[A-Z0-9]{5}){4})(?![A-Za-z0-9])")


def norm(code: str) -> str:
    return "-".join(re.findall(r"[A-Z0-9]{5}", code.upper()))


def read_sites(path: Path) -> list[tuple[str, str]]:
    sites = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url, _, game = line.partition("|")
        sites.append((url.strip(), game.strip()))
    return sites


def scrape(url: str) -> tuple[str, set[str], str | None]:
    """Renvoie (url, codes trouvés, erreur éventuelle)."""
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": UA, "Accept-Language": "en,fr;q=0.8"})
        if r.status_code >= 400:
            return url, set(), f"HTTP {r.status_code}"
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        text = soup.get_text(" ")
        # Certains sites mettent le code dans un attribut (bouton "copier")
        attrs = " ".join(v for tag in soup.find_all(True) for k, v in tag.attrs.items()
                         if isinstance(v, str) and k.startswith("data-"))
        codes = {norm(m) for m in CODE_RE.findall(text + " " + attrs)}
        return url, codes, None
    except requests.RequestException as e:
        return url, set(), type(e).__name__


def load_community() -> list[dict]:
    try:
        data = requests.get(COMMUNITY_URL, timeout=30, headers={"User-Agent": UA}).json()
        return (data[0] if isinstance(data, list) else data)["codes"]
    except Exception as e:  # noqa: BLE001
        print(f"  liste communautaire indisponible ({type(e).__name__})")
        return []


def collect(sites_file: Path, previous: dict | None = None, quiet: bool = False) -> dict:
    say = (lambda *a: None) if quiet else print
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    codes: dict[str, dict] = {}
    for c in (previous or {}).get("codes", []):  # on garde tout l'historique
        codes[c["code"]] = c

    def add(code, game, source, reward="", expires="", expired=False):
        e = codes.setdefault(code, {"code": code, "game": game or "", "reward": reward, "expires": expires,
                                    "expired": expired, "first_seen": now, "sources": []})
        if game and not e.get("game"):
            e["game"] = game
        if reward and not e.get("reward"):
            e["reward"] = reward
        if expires and not e.get("expires"):
            e["expires"] = expires
        if source not in e["sources"]:
            e["sources"].append(source)

    say("Liste communautaire…")
    for c in load_community():
        add(norm(c["code"]), c.get("game", ""), "communauté (mentalmars)",
            c.get("reward", ""), c.get("expires", ""), bool(c.get("expired")))

    sites = read_sites(sites_file)
    say(f"{len(sites)} sites à visiter…")
    game_of = dict(sites)
    report = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for url, found, err in pool.map(lambda s: scrape(s[0]), sites):
            host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
            for code in found:
                add(code, game_of.get(url, ""), host)
            report.append({"url": url, "found": len(found), "error": err})
            say(f"  {'✗' if err else '✓'} {host:<28} {len(found):>3} code(s)" + (f"  ({err})" if err else ""))

    out = {"meta": {"generated": now, "sites": report, "count": len(codes)},
           "codes": sorted(codes.values(), key=lambda c: c.get("first_seen", ""), reverse=True)}
    say(f"Total : {len(codes)} codes uniques.")
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sites", default=str(HERE / "sites.txt"))
    p.add_argument("--out", default=str(HERE / "shiftcodes.json"))
    a = p.parse_args()
    out_path = Path(a.out)
    previous = None
    if out_path.exists():
        try:
            previous = json.loads(out_path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    t = time.time()
    data = collect(Path(a.sites), previous)
    out_path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Écrit dans {out_path.name} ({time.time() - t:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
