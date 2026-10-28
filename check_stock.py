#!/usr/bin/env python3
"""Voorraadcheck voor refurbished PS5's op playstation.com (Nederland).

Haalt de productpagina's op, leest het veld `stockLevelStatus` dat Sony in de
pagina meestuurt en stuurt een pushmelding via ntfy.sh zodra een console
beschikbaar wordt (en een rustige melding als hij weer uitverkocht is).

Alleen standaardbibliotheek, zodat het zonder installatie op GitHub Actions draait.
Het script eindigt altijd met exitcode 0, zodat GitHub geen faalmails stuurt;
problemen worden via de log en (na herhaling) via ntfy gemeld.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

PRODUCTS = [
    # Het oude refurbished model (CFI-1000) is niet meer te koop: PS Direct stuurt door naar de slim.
    {
        "key": "disc_slim",
        "name": "Refurbished PS5 slim met disc",
        "url": "https://www.playstation.com/nl-nl/ps5/certified-refurbished-ps5-slim/",
        "code": "PSD001-PSDE00233_59-PS5GRADEDCLIZSLE",
        "buy": "https://www.playstation.com/nl-nl/ps5/certified-refurbished-ps5-slim/",
    },
    {
        "key": "digital",
        "name": "Refurbished PS5 Digital Edition (slim)",
        "url": "https://www.playstation.com/nl-nl/ps5/certified-refurbished-ps5-slim-digital-edition/",
        "code": "PSD001-PSDE00234_23-PS5GRADEDDMBTOTE",
        "buy": "https://www.playstation.com/nl-nl/ps5/certified-refurbished-ps5-slim-digital-edition/",
    },
]

STATE_FILE = "state.json"
DEBUG_DIR = "debug"
FAILS_BEFORE_ALERT = 6  # 6 mislukte runs op rij (~30 min) voordat er een storingsmelding komt

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

# Productcodes zien eruit als PSD001-PSDE01840_63-PS5GRADEDCEC7XZE
CODE_RE = re.compile(r"PSD\d{3}-[A-Z0-9_]+-[A-Z0-9]+")
# stockLevelStatus kan als gewone JSON of als ge-escapete JSON (Next.js) in de pagina staan
STATUS_RE = re.compile(r'stockLevelStatus\\*"\s*:\s*\\*"([A-Za-z_]+)')
PRODUCT_ID_RES = [
    re.compile(r'name="productId"\s+content="([^"]+)"'),
    re.compile(r'content="([^"]+)"\s+name="productId"'),
    re.compile(r'productId\\*"\s*:\s*\\*"(PSD[^"\\]+)'),
]
QUEUE_HINTS = ("queue-it", "queueit", "waitingroom", "waiting-room", "wachtrij")
OUT_VALUES = {"OUT_OF_STOCK", "OUTOFSTOCK", "NOSTOCK", "NO_STOCK"}


def log(msg):
    print(msg, flush=True)


def fetch(url):
    """Geeft (http_status, eind-URL, html) terug; html is '' bij een fout."""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, resp.geturl(), body
    except urllib.error.HTTPError as e:
        return e.code, e.geturl() if hasattr(e, "geturl") else url, ""
    except Exception as e:  # netwerkfout, timeout
        log(f"  netwerkfout: {e!r}")
        return 0, url, ""


def find_product_code(html, url):
    for rx in PRODUCT_ID_RES:
        m = rx.search(html)
        if m:
            c = CODE_RE.search(m.group(1))
            if c:
                return c.group(0)
    m = CODE_RE.search(url)
    if m:
        return m.group(0)
    return None


def statuses_for(html, code):
    """Koppelt elk stockLevelStatus-veld aan de dichtstbijzijnde productcode ervoor.

    Geeft (statussen voor deze code, alle gevonden (code, status)-paren) terug.
    """
    code_positions = [(m.start(), m.group(0)) for m in CODE_RE.finditer(html)]
    pairs = []
    for m in STATUS_RE.finditer(html):
        pos = m.start()
        nearest = None
        for cpos, c in code_positions:
            if cpos < pos:
                nearest = c
            else:
                break
        pairs.append((nearest, m.group(1)))

    if code:
        base = code.split("-E0")[0]  # SKU-varianten zoals ...-E000KR6YV horen bij hetzelfde product
        mine = [s for c, s in pairs if c and (c == code or c.startswith(base))]
    else:
        mine = []

    # Geen koppeling mogelijk, maar wel precies één unieke status op de pagina: gebruik die.
    if not mine and pairs and len({s for _, s in pairs}) == 1:
        mine = [pairs[0][1]]
    return mine, pairs


def classify(product):
    status_code, final_url, html = fetch(product["url"])
    info = {"http": status_code, "final_url": final_url, "len": len(html)}
    log(f"- {product['name']}: HTTP {status_code}, {len(html)} tekens, eind-URL {final_url}")

    if any(h in final_url.lower() for h in QUEUE_HINTS):
        return "QUEUE", info, html
    if status_code != 200 or not html:
        return "ERROR", info, html

    code = product["code"] or find_product_code(html, product["url"])
    info["code"] = code
    mine, pairs = statuses_for(html, code)
    info["pairs"] = pairs[:20]
    log(f"  productcode: {code}")
    log(f"  gevonden stockLevelStatus-velden: {len(pairs)} -> {pairs[:10]}")
    for m in list(STATUS_RE.finditer(html))[:3]:
        snippet = html[max(0, m.start() - 150): m.end() + 50].replace("\n", " ")
        log(f"  context: ...{snippet}...")
        info.setdefault("snippet", snippet[-200:])
    # Hoofdsignaal: staat er een 'Toevoegen aan wagentje'-knop (ctaSubType add_to_cart) voor dit product?
    # Getest op 28-10-2026: Sony's stockLevelStatus zei OUT_OF_STOCK terwijl de console via die knop
    # gewoon te koop was, en een pagina zonder die knop toonde "Niet beschikbaar voor aankoop".
    base = (code or "").split("-E0")[0]
    info["cart_button"] = bool(base) and any(
        base in html[m.start(): m.start() + 800] for m in re.finditer(r"add_to_cart", html)
    )
    info["stock_field"] = mine
    lower = html.lower()
    info["hints"] = {
        k: (k in lower)
        for k in ("stocklevel", "uitverkocht", "toevoegen aan", "add_to_cart",
                  "niet beschikbaar voor aankoop", "access denied", "self.__next_f")
    }
    log(f"  koopknop voor dit product: {info['cart_button']}")

    if not base or base not in html:
        return "UNKNOWN", info, html  # verkeerde of gewijzigde pagina
    return ("IN_STOCK" if info["cart_button"] else "OUT_OF_STOCK"), info, html


def notify(topic, title, message, priority=3, click=None, tags=None):
    if not topic:
        log(f"  (geen NTFY_TOPIC ingesteld, melding niet verstuurd: {title})")
        return
    payload = {"topic": topic, "title": title, "message": message, "priority": priority}
    if tags:
        payload["tags"] = tags
    if click:
        payload["click"] = click
        payload["actions"] = [{"action": "view", "label": "Openen", "url": click}]
    req = urllib.request.Request(
        "https://ntfy.sh/",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            log(f"  melding verstuurd ({resp.status}): {title}")
    except Exception as e:
        log(f"  melding mislukt: {e!r}")


def annotate(title, message):
    """Zet diagnose als annotatie bij de run (alleen op GitHub Actions)."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return

    def esc(s, prop=False):
        s = s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        if prop:
            s = s.replace(":", "%3A").replace(",", "%2C")
        return s

    print(f"::notice title={esc(title, True)}::{esc(message)}", flush=True)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def save_debug(key, html):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    with open(os.path.join(DEBUG_DIR, f"{key}.html"), "w", encoding="utf-8") as f:
        f.write(html)


LABELS = {
    "IN_STOCK": "BESCHIKBAAR",
    "QUEUE": "wachtrij actief (mogelijk restock)",
    "OUT_OF_STOCK": "uitverkocht",
    "UNKNOWN": "onbekend (status niet gevonden)",
    "ERROR": "pagina niet bereikbaar",
}


def main():
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    force_report = os.environ.get("FORCE_REPORT", "").lower() == "true"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state = load_state()
    summary = []

    for i, product in enumerate(PRODUCTS):
        if i:
            time.sleep(3)
        status, info, html = classify(product)
        log(f"  => {status}")
        prev = state.get(product["key"], {})
        prev_status = prev.get("status")
        fails = prev.get("fails", 0)
        summary.append(f"{product['name']}: {LABELS[status]}")

        if status in ("UNKNOWN", "ERROR") or force_report:
            save_debug(product["key"], html)
            annotate(f"{product['key']}: {status}", json.dumps(info, ensure_ascii=False)[:3000])

        available = status in ("IN_STOCK", "QUEUE")
        was_available = prev_status in ("IN_STOCK", "QUEUE")

        if available and not was_available:
            notify(
                topic,
                f"PS5 beschikbaar: {product['name']}",
                f"Status: {LABELS[status]}. Max. 1 per bestelling; partijen zijn vaak snel op.",
                priority=5,
                click=product["buy"],
                tags=["video_game", "rotating_light"],
            )
        elif was_available and status == "OUT_OF_STOCK":
            notify(topic, f"Weer uitverkocht: {product['name']}", "De voorraad is op.", priority=2)

        if status in ("UNKNOWN", "ERROR"):
            fails += 1
            if fails == FAILS_BEFORE_ALERT:
                notify(
                    topic,
                    "PS5-voorraadcheck werkt niet goed",
                    f"{product['name']}: al {fails} keer op rij {LABELS[status]} "
                    f"(HTTP {info.get('http')}). Bekijk de log in GitHub Actions.",
                    priority=3,
                    tags=["warning"],
                )
            # houd de laatste bekende echte status vast, zodat een storing geen valse melding geeft
            new_status = prev_status
        else:
            if fails >= FAILS_BEFORE_ALERT:
                notify(topic, "PS5-voorraadcheck werkt weer", product["name"], priority=2)
            fails = 0
            new_status = status

        state[product["key"]] = {
            "status": new_status,
            "last_seen": status,
            "fails": fails,
            "checked": now,
        }

    if force_report:
        notify(topic, "PS5-voorraadcheck draait", "\n".join(summary), priority=3, tags=["white_check_mark"])

    save_state(state)
    log("\n".join(summary))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # nooit falen: GitHub zou anders elke 5 minuten een mail sturen
        log(f"Onverwachte fout: {e!r}")
    sys.exit(0)
