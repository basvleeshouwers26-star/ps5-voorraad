# PS5 voorraadcheck

Controleert elke 5 minuten of de refurbished PS5 slim met disc en de refurbished PS5 Digital Edition (slim) op playstation.com/nl-nl weer te koop zijn, en stuurt dan een pushmelding via de ntfy-app.

## Hoe het werkt

- `check_stock.py` haalt beide productpagina's op en leest het veld `stockLevelStatus` dat Sony in de pagina meestuurt.
- Wordt een console beschikbaar (of staat er een wachtrij), dan komt er een melding met hoge prioriteit en een koop-link. Is hij weer op, dan volgt een rustige melding.
- Geeft de check 6 keer op rij geen bruikbare status (~30 minuten), dan krijg je één storingsmelding.
- De laatste status wordt tussen runs bewaard in de Actions-cache, zodat je niet elke 5 minuten dezelfde melding krijgt.
- Het script koopt niets en logt nergens in.

## Instellen

1. Installeer de app **ntfy** (iOS/Android) en abonneer je op een lang, willekeurig onderwerp (topic).
2. Zet in GitHub onder **Settings → Secrets and variables → Actions** een secret `NTFY_TOPIC` met precies die naam van het topic.
3. Start de workflow één keer handmatig via **Actions → PS5 voorraadcheck → Run workflow**. Je krijgt dan een statusmelding met de huidige stand van beide consoles.

## Stoppen

**Actions → PS5 voorraadcheck → ⋯ → Disable workflow.**

De repository moet openbaar zijn: dan zijn de Actions-minuten gratis. Het topic staat in een secret en is dus niet zichtbaar.
