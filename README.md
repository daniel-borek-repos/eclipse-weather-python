# Weather & Eclipses

A small self-hosted Flask app that shows, for every place you care about:

* current weather conditions
* today's sunrise, sunset and length of day
* the **next solar eclipse visible from that exact spot** — partial, annular or
  total — with local contact times, how much of the Sun gets covered, and the
  sunrise and sunset times for the eclipse date itself

You can group locations into named **views** and switch between them, so
"Home" can sit alongside "Eclipse trip 2027".

## Running it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5000>. On first launch the app asks for your
location — either from the browser's geolocation prompt or by searching for a
town — and saves it as your default view.

Useful flags: `python app.py --port 8080 --host 0.0.0.0 --debug`.

## Settings

Everything is stored in `settings.json` beside the app. That file is
**git-ignored** because it holds your home coordinates; `settings.json.sample`
documents the format and can be copied to `settings.json` to pre-seed the app
instead of using the first-run screen.

Writes are atomic, so an interrupted save cannot corrupt your configuration. A
corrupt or hand-mangled file is treated as "not set up yet" rather than
crashing on startup.

Set `ECLIPSE_WEATHER_SETTINGS=/some/path.json` to store settings elsewhere.

## What you can do

| Action | Where |
| --- | --- |
| Add a location to the current view | **Add location** |
| Remove a location | the `×` on its card, or **Manage views** |
| Rename the current view, make it the default, or delete it | **Manage views** |
| Create a view, optionally copying the current locations | **+ New view** |
| Switch views | the tabs under the title |
| Change temperature, wind and clock units | **Units** |

The view marked *default* is the one that opens when you visit `/`.

## Location API

Everything a dashboard card shows for a place — current weather, today's
sunrise, sunset and forecast, and the next visible solar eclipse — is also
available from a single unauthenticated HTTP call, so you can script against
the running server without opening the web UI:

```
GET /api/location?city=<city>&country=<country>&format=<json|text|xml>
```

| Parameter | Required | Meaning |
| --- | --- | --- |
| `city` | yes | Place name, as you would type it into **Add location** |
| `country` | yes | Country name (`Poland`) or ISO code (`PL`), case-insensitive |
| `format` | no | `json` (default), `text` or `xml` |

The place does not have to be saved in any view. The city is looked up with
the same geocoder the UI uses and the most relevant match inside the given
country wins. Temperature, wind and clock units follow your **Units** settings.

Retrieve the data for Kraków as JSON:

```bash
curl -G 'http://127.0.0.1:5000/api/location' \
     --data-urlencode 'city=Krakow' \
     --data-urlencode 'country=Poland'
```

The same as plain text or XML:

```bash
curl 'http://127.0.0.1:5000/api/location?city=Reykjavik&country=IS&format=text'
curl 'http://127.0.0.1:5000/api/location?city=New%20York&country=US&format=xml'
```

A successful JSON response looks like this (trimmed):

```json
{
  "ok": true,
  "location": {
    "name": "Krakow",
    "label": "Krakow, Lesser Poland, Poland",
    "country": "Poland",
    "latitude": 50.06143,
    "longitude": 19.93658,
    "timezone": "Europe/Warsaw",
    "units": {"temperature": "°C", "wind_speed": "km/h"},
    "weather": {"temperature": 4, "condition": {"label": "Light drizzle", "icon": "drizzle"}, "...": "..."},
    "today": {"sunrise": "06:46", "sunset": "16:01", "day_length": "9h 14m", "forecast": ["..."], "...": "..."},
    "eclipse": {"type_label": "Partial solar eclipse", "date": "2027-08-02", "obscuration_percent": 37.8, "...": "..."},
    "errors": {}
  },
  "generated_at": "2026-11-11T10:05:54+00:00"
}
```

If the weather service is unreachable the eclipse data is still returned and
the problem is reported under `errors`. Other failures use the usual status
codes with `{"ok": false, "error": "..."}` (rendered in the requested format):
`400` for a missing parameter or unknown format, `404` when no such city exists
in that country, `502` when the geocoder is down.

**Rate limit:** one request per second per client IP. Extra requests are not
rejected — the server holds each one until its turn comes, so a burst of calls
is answered one per second, in order. Only a client that queues up more than
30 seconds' worth of calls gets `429 Too Many Requests` with a `Retry-After`
header.

## How the data is produced

Weather, and today's sunrise and sunset, come from
[Open-Meteo](https://open-meteo.com) — free, no API key, no account. Place
search uses Open-Meteo's geocoder; reverse geocoding uses BigDataCloud with an
IP lookup as a last-resort fallback.

Everything astronomical is computed locally in `astro.py`, in pure Python with
no dependencies. This is not laziness about finding an API: no weather service
can tell you *"the next eclipse visible from my garden, in my local time"*,
and forecast APIs only reach about 16 days ahead, whereas eclipse dates are
years out. So the app finds eclipses itself, by stepping forward through new
moons and computing the topocentric Sun–Moon geometry for your coordinates
until it finds one whose discs actually overlap while the Sun is above your
horizon.

Algorithms follow Jean Meeus, *Astronomical Algorithms* (2nd ed.): the Sun
from chapter 25, the Moon from the abridged ELP-2000 of chapter 47, new moons
from chapter 49, and diurnal parallax from chapter 40. Terrestrial Time and
Universal Time are kept distinct, because conflating them shifts eclipse
predictions by the whole of ΔT — currently over a minute.

### Accuracy

Verified against published local circumstances for the total eclipses of
2017-08-21 (Nashville), 2024-04-08 (Dallas), 2026-08-12 (Reykjavík) and
2028-07-22 (Sydney):

| Quantity | Agreement |
| --- | --- |
| Eclipse date and type | correct in every case tested |
| Time of maximum eclipse | within ~40 seconds |
| Duration of totality | within ~15 seconds |
| Magnitude and obscuration | within ~0.005 |
| Sunrise / sunset vs Open-Meteo | ~30 s at mid latitudes, ~2 min above 65° |

The residual comes from the truncated lunar series (~10 arcsec, which is ~20
seconds of eclipse time) and cannot be reduced without the full ELP-2000
expansion. The app also does not model the lunar limb profile that governs the
exact instants of second and third contact.

That is fine for deciding where to be on the day. **If you are travelling to
stand on the centre line, check a dedicated prediction** such as NASA's eclipse
pages or Xavier Jubier's interactive maps.

Locations very near the edge of the umbral path are the one place where this
matters: a spot predicted here as 0.999 magnitude may in reality be just
inside totality, or vice versa.

**Never look at the Sun without proper eclipse filters**, including during the
partial phases of a total eclipse.

## Tests

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

107 tests, no network required — the weather provider is stubbed. The astronomy
tests assert against real published eclipse circumstances, so they are the ones
that would catch a regression in the maths. The provider tests swap out
`providers.requests` wholesale, so no HTTP call is ever made.

To produce the coverage and test-execution reports that SonarQube reads:

```bash
python -m pytest --cov --cov-report=xml --cov-report=term --junitxml=junit-report.xml
```

That writes `coverage.xml` (Cobertura) and `junit-report.xml`. Both are build
artifacts and are gitignored; CI regenerates them before every analysis.

### Front-end tests

The browser JavaScript is tested with Vitest under jsdom:

```bash
npm install
npm test           # or: npm run coverage
```

`npm run coverage` writes `coverage-js/lcov.info`, which SonarQube reads via
`sonar.javascript.lcov.reportPaths`. No lockfile is committed, so CI uses
`npm install` rather than `npm ci`.

## Layout

```
app.py                 Flask routes
services.py            assembles weather + sun + eclipse per location
providers.py           Open-Meteo / geocoding clients, with caching
astro.py               sun, moon, sunrise/sunset, eclipse geometry
settings.py            settings.json persistence (atomic writes)
formats.py             plain-text and XML rendering for the location API
ratelimit.py           per-client rate limiter for the location API
templates/, static/    server-rendered shell, one CSS file, three JS files
```

Weather is cached for 10 minutes and eclipse results until the date changes, so
switching between views is quick and the upstream API is not hammered.
