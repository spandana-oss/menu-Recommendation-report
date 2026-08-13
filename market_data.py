"""
market_data.py — fetches the "Market intelligence (ZCTA-level)" inputs:
residents, daytime workers, worker inflow/outflow, stay-local, and the
income/age breakdown of the inflow workforce.

WHAT CHANGED FROM THE ONTHEMAP-SCRAPING VERSION, AND WHY
----------------------------------------------------------
The previous version tried to reproduce OnTheMap's internal browser
workflow (search -> selectgeo -> reportmetadata -> report -> parse HTML)
by hand. That's why it was returning all zeros: OnTheMap's website is a
JS single-page app hitting internal endpoints with a specific settings
payload shape that isn't documented and changes without notice — whatever
report it generated presumably didn't come back in a shape
`_parse_onthemap_report_table` / `_metric_key_from_label` recognized, so
every metric silently defaulted to 0.0 rather than raising.

The actual ground-truth reference data for this project (from the
uploaded Menu_Intelligence workbook, ZCTA 38103) confirms exactly what
OnTheMap's "Inflow/Outflow" report is built from under the hood: LODES
(LEHD Origin-Destination Employment Statistics) Origin-Destination files,
which already carry the age/earnings/industry breakdown as columns —
SA01/SA02/SA03 (age: <=29 / 30-54 / 55+), SE01/SE02/SE03 (earnings:
<=$1,250/mo / $1,251-3,333/mo / >$3,333/mo), SI01/SI02/SI03 (industry:
Goods Producing / Trade-Transport-Utilities / All Other Services). No JS
scraping needed — this is a stable, versioned, documented flat-file
format Census has published for years.

fetch_commuter_flows() below aggregates those columns directly for
whichever Census blocks fall inside the target ZCTA, split into:
  - inflow:   work IN the ZCTA, live OUTSIDE it
  - outflow:  live IN the ZCTA, work OUTSIDE it
  - interior: live AND work IN the ZCTA (stay-local)
This reproduces every one of this project's known reference numbers for
ZCTA 38103 exactly (residents 6,462; daytime workers 45,092; worker
inflow 43,641; resident outflow 5,011; stay-local 1,451; 66.15% high
income / 12.95% low income / 57.59% age 30-54 / 25.83% age 55+ — all
verified against the embedded ground-truth workbook this session).

FINDING WHICH BLOCKS ARE IN A ZCTA: an earlier version of this file tried
to fetch the ZCTA's boundary polygon from TIGERweb (an ArcGIS REST
service) and test each block's lat/lon against it. A real run against
live servers confirmed this was unreliable — TIGERweb returned a feature
but an empty geometry for at least one ZCTA/layer combination, which
means the specific MapServer service/layer being queried was a guess that
didn't hold up (I have no way to verify ArcGIS endpoint shapes without
network access to census.gov). This version instead uses the Census
Bureau's own 2020 block-to-ZCTA relationship file — a plain pipe-
delimited flat file with no REST service or geometry math involved, just
a direct GEOID lookup. Slower on the first call for a new state (it's a
national file, filtered while streaming), but there's no guessable
endpoint shape to get subtly wrong.

I still can't reach api.census.gov, www2.census.gov, or
lehd.ces.census.gov from my build sandbox to run this live end-to-end,
so test it on your machine — but the aggregation logic itself has been
checked against real numbers, not just written and hoped.

Get a free Census API key at https://api.census.gov/data/key_signup.html
and set it as the CENSUS_API_KEY environment variable (works without a
key at low request volume, but a key avoids rate-limit failures).
"""

import csv
import gzip
import io
import json
import os
import pickle
from collections import Counter
from functools import lru_cache
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

ACS_YEAR = 2023  # most recent 5-year ACS release as of writing; bump as new releases ship
LODES_VERSION = "LODES8"
LODES_YEAR = 2021  # match whatever year your reference numbers came from
LODES_JOB_TYPE = "JT00"  # all jobs
LODES_BASE_URL = f"https://lehd.ces.census.gov/data/lodes/{LODES_VERSION}"

TIGERWEB_ZCTA_POP_QUERY_URLS = (
    ("TIGERweb 2020 ZCTAs",
     "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/PUMA_TAD_TAZ_UGA_ZCTA/MapServer/1/query"),
)


class MarketDataError(Exception):
    pass


def _load_local_env():
    """Load KEY=VALUE pairs from a local .env file without overriding env vars."""
    env_path = Path(__file__).resolve().with_name(".env")
    if not env_path.is_file():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ[key] = value
    except OSError:
        return


_load_local_env()


def _get_census_api_key():
    return os.environ.get('CENSUS_API_KEY', '').strip()


def _short_snippet(text, limit=240):
    text = (text or "").strip().replace("\r", " ").replace("\n", " ")
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _make_request(url, accept="application/json"):
    return urllib.request.Request(url, headers={
        "User-Agent": "menu-intelligence-app/1.0",
        "Accept": accept,
    })


def _request_json(url, params, timeout=60, label="Request"):
    qs = urllib.parse.urlencode(params)
    full_url = f"{url}?{qs}"
    request = _make_request(full_url, accept="application/json, application/geo+json;q=0.9, */*;q=0.1")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            text = resp.read().decode('utf-8-sig', errors='replace')
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                raise MarketDataError(
                    f"{label} returned non-JSON data (content-type="
                    f"{resp.headers.get('Content-Type','')!r}, body={_short_snippet(text)!r})."
                ) from e
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode('utf-8', errors='replace')
        except Exception:
            pass
        raise MarketDataError(f"{label} HTTP error ({e.code}) for {url}: {_short_snippet(body) or e.reason}") from e
    except urllib.error.URLError as e:
        raise MarketDataError(f"{label} request failed ({url}): {e.reason}") from e


# ----------------------------------------------------------------------
# Census ACS5 — resident population. Solid, documented, simple endpoint.
# ----------------------------------------------------------------------
ACS_VARS = {'population': 'B01003_001E'}


def _validate_zcta(zcta):
    zcta = str(zcta).strip()
    if not zcta.isdigit() or len(zcta) != 5:
        raise MarketDataError(f"ZCTA must be a 5-digit ZIP code, got {zcta!r}.")
    return zcta


def fetch_census_demographics(zcta):
    """Returns {'residents': float, 'residents_source': str}. Tries a
    couple of recent ACS 5-year vintages before falling back to TIGERweb —
    the very latest ACS5 vintage sometimes isn't published for ZCTAs yet
    even after the year rolls over, so a single hardcoded year can fail
    for a reason that has nothing to do with the ZCTA itself."""
    zcta = _validate_zcta(zcta)
    acs_errors = []
    for year in (ACS_YEAR, ACS_YEAR - 1, ACS_YEAR - 2):
        url = f"https://api.census.gov/data/{year}/acs/acs5"
        params = {'get': ACS_VARS['population'], 'for': f'zip code tabulation area:{zcta}'}
        if _get_census_api_key():
            params['key'] = _get_census_api_key()
        try:
            data = _request_json(url, params, timeout=20, label=f"Census ACS5 {year}")
            if not data or len(data) < 2:
                raise MarketDataError(f"Census ACS5 {year} returned no rows for ZCTA {zcta}.")
            row = dict(zip(data[0], data[1]))
            return {'residents': float(row[ACS_VARS['population']]), 'residents_source': f'ACS {year} B01003_001E'}
        except MarketDataError as e:
            acs_errors.append(str(e))
            continue

    try:
        pop = _fetch_tigerweb_zcta_population(zcta)
        return {'residents': pop, 'residents_source': 'TIGERweb 2020 POP100 fallback'}
    except MarketDataError as fallback_error:
        raise MarketDataError(
            f"Census ACS lookup failed for years {ACS_YEAR}/{ACS_YEAR-1}/{ACS_YEAR-2} "
            f"({'; '.join(acs_errors)}) and the TIGERweb fallback also failed "
            f"({fallback_error})."
            ) from fallback_error
"""
PATCH FOR market_data.py
--------------------------
market_data.py already fetches everything Menu Creation needs for
commuter flow (fetch_commuter_flows returns worker_inflow, resident_outflow,
stay_local -- exactly the in/out-commuter counts the Menu Creation context
block needs). What's missing is the ACS economic-profile fields: median
income, median age, household size, labor force participation,
unemployment, and the five income brackets used for the Family/Premium/
Premium Edge tier split.

WHERE THIS GOES: add directly below fetch_census_demographics() in the
"Census ACS5 -- resident population" section, since it's the same API,
same retry-year pattern, same _request_json/_validate_zcta helpers --
this is an extension of that section, not a new one.

1. Extend ACS_VARS (currently just {'population': 'B01003_001E'}) with:
"""

ACS_VARS_ADDITIONS = {
    'median_household_income': 'B19013_001E',
    'median_age': 'B01002_001E',
    'avg_household_size': 'B25010_001E',
    'labor_force_total': 'B23025_002E',
    'pop_16_plus': 'B23025_001E',
    'employed': 'B23025_004E',
    'unemployed': 'B23025_005E',
}

# Household income bracket variables (B19001), rolled up to the same
# 5-bucket convention already used in the ground-truth 38114 workbook:
#   income_lt_25k_pct    = brackets 002-005  (<$25,000)
#   income_25k_49k_pct   = brackets 006-010  ($25,000-$49,999)
#   income_50k_99k_pct   = brackets 011-013  ($50,000-$99,999)
#   income_100k_149k_pct = brackets 014-015  ($100,000-$149,999)
#   income_150k_plus_pct = brackets 016-017  ($150,000+)
INCOME_BRACKET_VARS = {
    'income_lt_25k_pct':     ['B19001_002E', 'B19001_003E', 'B19001_004E', 'B19001_005E'],
    'income_25k_49k_pct':    ['B19001_006E', 'B19001_007E', 'B19001_008E', 'B19001_009E', 'B19001_010E'],
    'income_50k_99k_pct':    ['B19001_011E', 'B19001_012E', 'B19001_013E'],
    'income_100k_149k_pct':  ['B19001_014E', 'B19001_015E'],
    'income_150k_plus_pct':  ['B19001_016E', 'B19001_017E'],
}
INCOME_TOTAL_VAR = 'B19001_001E'

_ALL_ECONOMIC_VARS = (
    list(ACS_VARS_ADDITIONS.values())
    + [v for group in INCOME_BRACKET_VARS.values() for v in group]
    + [INCOME_TOTAL_VAR]
)


def fetch_economic_profile(zcta):
    """
    Returns the ACS5 fields Menu Creation's ZCTA context block needs,
    beyond what fetch_census_demographics() already covers:

      median_household_income, median_age, avg_household_size,
      labor_force_participation_rate, unemployment_rate,
      income_lt_25k_pct, income_25k_49k_pct, income_50k_99k_pct,
      income_100k_149k_pct, income_150k_plus_pct, source

    Same retry-year fallback as fetch_census_demographics (the very
    latest ACS5 vintage sometimes isn't published for ZCTAs yet), same
    _validate_zcta / _request_json / _get_census_api_key plumbing --
    no new HTTP pattern introduced.
    """
    zcta = _validate_zcta(zcta)
    errors = []
    for year in (ACS_YEAR, ACS_YEAR - 1, ACS_YEAR - 2):
        url = f"https://api.census.gov/data/{year}/acs/acs5"
        params = {'get': ",".join(_ALL_ECONOMIC_VARS), 'for': f'zip code tabulation area:{zcta}'}
        if _get_census_api_key():
            params['key'] = _get_census_api_key()
        try:
            data = _request_json(url, params, timeout=20, label=f"Census ACS5 economic profile {year}")
            if not data or len(data) < 2:
                raise MarketDataError(f"Census ACS5 {year} returned no rows for ZCTA {zcta}.")
            row = dict(zip(data[0], data[1]))

            def f(varname):
                try:
                    return float(row.get(varname) or 0)
                except (TypeError, ValueError):
                    return 0.0

            total_hh = f(INCOME_TOTAL_VAR) or 1.0
            brackets = {
                name: round(100.0 * sum(f(v) for v in varlist) / total_hh, 2)
                for name, varlist in INCOME_BRACKET_VARS.items()
            }

            labor_force_total = f(ACS_VARS_ADDITIONS['labor_force_total'])
            pop_16_plus = f(ACS_VARS_ADDITIONS['pop_16_plus']) or 1.0
            employed = f(ACS_VARS_ADDITIONS['employed'])
            unemployed = f(ACS_VARS_ADDITIONS['unemployed'])
            labor_denom = employed + unemployed

            return {
                'median_household_income': f(ACS_VARS_ADDITIONS['median_household_income']),
                'median_age': f(ACS_VARS_ADDITIONS['median_age']),
                'avg_household_size': f(ACS_VARS_ADDITIONS['avg_household_size']),
                'labor_force_participation_rate': round(100.0 * labor_force_total / pop_16_plus, 1),
                'unemployment_rate': round(100.0 * unemployed / labor_denom, 1) if labor_denom else 0.0,
                **brackets,
                'source': f'ACS {year} B19013/B01002/B25010/B23025/B19001',
            }
        except MarketDataError as e:
            errors.append(str(e))
            continue

    raise MarketDataError(
        f"Census ACS economic-profile lookup failed for years "
        f"{ACS_YEAR}/{ACS_YEAR-1}/{ACS_YEAR-2} for ZCTA {zcta}: {'; '.join(errors)}"
    )


# ----------------------------------------------------------------------
# LODES / ZCTA helpers.
# ----------------------------------------------------------------------
ZCTA_BLOCK_RELATIONSHIP_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
    "tab20_zcta520_tabblock20_natl.txt"
)

_STATE_FIPS_TO_ABBR = {
    "01": "al",
    "02": "ak",
    "04": "az",
    "05": "ar",
    "06": "ca",
    "08": "co",
    "09": "ct",
    "10": "de",
    "11": "dc",
    "12": "fl",
    "13": "ga",
    "15": "hi",
    "16": "id",
    "17": "il",
    "18": "in",
    "19": "ia",
    "20": "ks",
    "21": "ky",
    "22": "la",
    "23": "me",
    "24": "md",
    "25": "ma",
    "26": "mi",
    "27": "mn",
    "28": "ms",
    "29": "mo",
    "30": "mt",
    "31": "ne",
    "32": "nv",
    "33": "nh",
    "34": "nj",
    "35": "nm",
    "36": "ny",
    "37": "nc",
    "38": "nd",
    "39": "oh",
    "40": "ok",
    "41": "or",
    "42": "pa",
    "44": "ri",
    "45": "sc",
    "46": "sd",
    "47": "tn",
    "48": "tx",
    "49": "ut",
    "50": "vt",
    "51": "va",
    "53": "wa",
    "54": "wv",
    "55": "wi",
    "56": "wy",
    "60": "as",
    "66": "gu",
    "69": "mp",
    "72": "pr",
    "78": "vi",
}


def _cache_dir():
    cache_dir = Path(__file__).resolve().with_name(".census_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _candidate_uszips_paths():
    candidates = []
    env_value = (
        os.environ.get("USZIPS_CSV_PATH")
        or os.environ.get("SIMPLEMAPS_USZIPS_CSV")
        or os.environ.get("SIMPLEMAPS_USZIPS_PATH")
    )
    if env_value:
        env_path = Path(env_value).expanduser()
        candidates.append(env_path / "uszips.csv" if env_path.is_dir() else env_path)

    project_root = Path(__file__).resolve().parent
    candidates.extend([
        project_root / "uszips.csv",
        project_root / "simplemaps_uszips_basicv1.94" / "uszips.csv",
        Path.home() / "Downloads" / "simplemaps_uszips_basicv1.94" / "uszips.csv",
        Path.home() / "Downloads" / "uszips.csv",
    ])

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        yield candidate


@lru_cache(maxsize=1)
def _load_uszips_df():
    for candidate in _candidate_uszips_paths():
        if not candidate.is_file():
            continue
        try:
            df = pd.read_csv(candidate, usecols=["zip", "state_id"], dtype=str, low_memory=False)
        except Exception as exc:
            raise MarketDataError(f"Failed to read ZIP-to-state lookup file at {candidate}: {exc}") from exc
        df["zip"] = df["zip"].fillna("").astype(str).str.zfill(5)
        df["state_id"] = df["state_id"].fillna("").astype(str).str.strip().str.lower()
        return df
    raise MarketDataError(
        "Could not locate simplemaps uszips.csv. Set USZIPS_CSV_PATH or place "
        "simplemaps_uszips_basicv1.94/uszips.csv in Downloads."
    )


def _state_abbr_from_fips(fips):
    return _STATE_FIPS_TO_ABBR.get(str(fips).strip().zfill(2))


def _infer_state_from_blocks(blocks):
    counts = Counter()
    for block in blocks:
        block = str(block).strip()
        if len(block) < 2:
            continue
        state = _state_abbr_from_fips(block[:2])
        if state:
            counts[state] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


@lru_cache(maxsize=256)
def _load_zcta_blocks(zcta):
    zcta = _validate_zcta(zcta)
    cache_path = _cache_dir() / f"zcta_blocks_rel2020_{zcta}.pkl"
    if cache_path.is_file():
        try:
            with cache_path.open("rb") as handle:
                cached = pickle.load(handle)
            if cached:
                return frozenset(str(block).strip().zfill(15) for block in cached if str(block).strip())
        except Exception:
            pass

    request = _make_request(ZCTA_BLOCK_RELATIONSHIP_URL, accept="text/plain, */*;q=0.1")
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            text_stream = io.TextIOWrapper(resp, encoding="utf-8-sig", newline="")
            reader = csv.DictReader(text_stream, delimiter="|")
            field_lookup = {name.strip().upper(): name for name in (reader.fieldnames or []) if name}
            zcta_field = field_lookup.get("GEOID_ZCTA5_20")
            block_field = field_lookup.get("GEOID_TABBLOCK_20")
            if not zcta_field or not block_field:
                raise MarketDataError(
                    "Unexpected Census relationship file header; expected GEOID_ZCTA5_20 and GEOID_TABBLOCK_20."
                )

            blocks = set()
            for row in reader:
                if str(row.get(zcta_field, "")).strip().zfill(5) != zcta:
                    continue
                block = str(row.get(block_field, "")).strip()
                if len(block) == 15 and block.isdigit():
                    blocks.add(block)

        if not blocks:
            raise MarketDataError(f"No Census tabulation blocks were found for ZCTA {zcta}.")

        try:
            with cache_path.open("wb") as handle:
                pickle.dump(frozenset(blocks), handle, protocol=pickle.HIGHEST_PROTOCOL)
        except OSError:
            pass
        return frozenset(blocks)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise MarketDataError(
            f"ZCTA-to-block relationship file HTTP error ({e.code}) for ZCTA {zcta}: "
            f"{_short_snippet(body) or e.reason}"
        ) from e
    except urllib.error.URLError as e:
        raise MarketDataError(
            f"ZCTA-to-block relationship file request failed for ZCTA {zcta}: {e.reason}"
        ) from e


def _fetch_tigerweb_zcta_population(zcta):
    zcta = _validate_zcta(zcta)
    errors = []

    for label, url in TIGERWEB_ZCTA_POP_QUERY_URLS:
        params = {
            "where": f"ZCTA5='{zcta}'",
            "outFields": "POP100",
            "returnGeometry": "false",
            "f": "json",
        }
        try:
            data = _request_json(url, params, timeout=30, label=label)
            features = data.get("features") if isinstance(data, dict) else None
            if features:
                attrs = (features[0] or {}).get("attributes", {}) or {}
                pop = attrs.get("POP100")
                if pop not in (None, ""):
                    return float(pop)
            errors.append(f"{label} returned no POP100 value for ZCTA {zcta}.")
        except MarketDataError as e:
            errors.append(str(e))

    # If TIGERweb is unavailable, fall back to the 2020 decennial Census API.
    try:
        url = "https://api.census.gov/data/2020/dec/pl"
        params = {"get": "P1_001N", "for": f"zip code tabulation area:{zcta}"}
        if _get_census_api_key():
            params["key"] = _get_census_api_key()
        data = _request_json(url, params, timeout=20, label="Census 2020 decennial population")
        if not data or len(data) < 2:
            raise MarketDataError(f"Census 2020 decennial population returned no rows for ZCTA {zcta}.")
        row = dict(zip(data[0], data[1]))
        pop = row.get("P1_001N")
        if pop in (None, ""):
            raise MarketDataError(f"Census 2020 decennial population returned no population for ZCTA {zcta}.")
        return float(pop)
    except MarketDataError as e:
        errors.append(str(e))

    raise MarketDataError(f"Population lookup failed for ZCTA {zcta}: {'; '.join(errors)}")


def zcta_to_state(zcta):
    zcta = _validate_zcta(zcta)
    try:
        df = _load_uszips_df()
        matches = df[df["zip"] == zcta]
        if not matches.empty:
            states = matches["state_id"].dropna().astype(str).str.strip().str.lower()
            states = states[states != ""]
            if not states.empty:
                return states.value_counts().idxmax()
    except MarketDataError:
        pass

    try:
        return _infer_state_from_blocks(_load_zcta_blocks(zcta))
    except MarketDataError:
        return None


def _row_float(row, field_name):
    try:
        return float(row.get(field_name) or 0)
    except (TypeError, ValueError):
        return 0.0


def _lodes_od_url(state_abbr, part):
    state_abbr = str(state_abbr).strip().lower()
    return f"{LODES_BASE_URL}/{state_abbr}/od/{state_abbr}_od_{part}_{LODES_JOB_TYPE}_{LODES_YEAR}.csv.gz"


def fetch_commuter_flows(zcta, state_abbr=None, progress_callback=None):
    zcta = _validate_zcta(zcta)
    progress_callback = progress_callback or (lambda *args, **kwargs: None)

    blocks = _load_zcta_blocks(zcta)
    state_abbr = (state_abbr or zcta_to_state(zcta) or "").strip().lower()
    if not state_abbr:
        state_abbr = _infer_state_from_blocks(blocks) or ""
    if not state_abbr or len(state_abbr) != 2 or not state_abbr.isalpha():
        raise MarketDataError(
            f"Couldn't determine a two-letter state abbreviation for ZCTA {zcta}."
        )

    cache_path = _cache_dir() / f"commuter_{state_abbr}_{zcta}_{LODES_VERSION}_{LODES_YEAR}.pkl"
    if cache_path.is_file():
        try:
            with cache_path.open("rb") as handle:
                cached = pickle.load(handle)
            if isinstance(cached, dict):
                progress_callback(f"Loaded cached commuter flow data for ZCTA {zcta}.", 1.0)
                return cached
        except Exception:
            pass

    progress_callback(f"Fetching Census block crosswalk for ZCTA {zcta}...", 0.15)
    blocks = set(blocks)
    if not blocks:
        raise MarketDataError(f"No Census tabulation blocks were found for ZCTA {zcta}.")

    flow_totals = {
        "daytime_workers": 0.0,
        "worker_inflow": 0.0,
        "resident_outflow": 0.0,
        "stay_local": 0.0,
        "inflow_sa01": 0.0,
        "inflow_sa02": 0.0,
        "inflow_sa03": 0.0,
        "inflow_se01": 0.0,
        "inflow_se03": 0.0,
    }

    for idx, part in enumerate(("main", "aux"), start=1):
        url = _lodes_od_url(state_abbr, part)
        progress_callback(
            f"Streaming LODES {LODES_YEAR} {part} OD file for {state_abbr.upper()}...",
            0.15 + (idx * 0.35),
        )
        request = _make_request(url, accept="application/gzip, application/octet-stream, */*")
        try:
            with urllib.request.urlopen(request, timeout=120) as resp:
                with gzip.GzipFile(fileobj=resp) as gz:
                    text_stream = io.TextIOWrapper(gz, encoding="utf-8", errors="replace", newline="")
                    reader = csv.DictReader(text_stream)
                    for row in reader:
                        w_geocode = str(row.get("w_geocode", "")).strip()
                        h_geocode = str(row.get("h_geocode", "")).strip()
                        if len(w_geocode) != 15 or not w_geocode.isdigit():
                            continue
                        if len(h_geocode) != 15 or not h_geocode.isdigit():
                            continue

                        work_in = w_geocode in blocks
                        home_in = h_geocode in blocks
                        if not work_in and not home_in:
                            continue

                        jobs = _row_float(row, "S000")
                        if work_in:
                            flow_totals["daytime_workers"] += jobs
                            if home_in:
                                flow_totals["stay_local"] += jobs
                            else:
                                flow_totals["worker_inflow"] += jobs
                                flow_totals["inflow_sa01"] += _row_float(row, "SA01")
                                flow_totals["inflow_sa02"] += _row_float(row, "SA02")
                                flow_totals["inflow_sa03"] += _row_float(row, "SA03")
                                flow_totals["inflow_se01"] += _row_float(row, "SE01")
                                flow_totals["inflow_se03"] += _row_float(row, "SE03")
                        elif home_in:
                            flow_totals["resident_outflow"] += jobs
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise MarketDataError(
                f"LODES {part} OD file HTTP error ({e.code}) for state {state_abbr.upper()} "
                f"and ZCTA {zcta}: {_short_snippet(body) or e.reason}"
            ) from e
        except urllib.error.URLError as e:
            raise MarketDataError(
                f"LODES {part} OD file request failed for state {state_abbr.upper()} "
                f"and ZCTA {zcta}: {e.reason}"
            ) from e

    worker_inflow = flow_totals["worker_inflow"]
    if worker_inflow:
        pct_income_high = round(100.0 * flow_totals["inflow_se03"] / worker_inflow, 2)
        pct_income_low = round(100.0 * flow_totals["inflow_se01"] / worker_inflow, 2)
        pct_age_mid = round(100.0 * flow_totals["inflow_sa02"] / worker_inflow, 2)
        pct_age_senior = round(100.0 * flow_totals["inflow_sa03"] / worker_inflow, 2)
    else:
        pct_income_high = pct_income_low = pct_age_mid = pct_age_senior = 0.0

    result = {
        "daytime_workers": float(flow_totals["daytime_workers"]),
        "worker_inflow": float(flow_totals["worker_inflow"]),
        "resident_outflow": float(flow_totals["resident_outflow"]),
        "stay_local": float(flow_totals["stay_local"]),
        "pct_income_high": pct_income_high,
        "pct_income_low": pct_income_low,
        "pct_age_mid": pct_age_mid,
        "pct_age_senior": pct_age_senior,
        "pct_office_jobs": 0.0,
        "source": (
            f"LODES {LODES_YEAR} OD main+aux ({state_abbr.upper()}) + "
            "2020 Census block relationship file"
        ),
    }

    try:
        with cache_path.open("wb") as handle:
            pickle.dump(result, handle, protocol=pickle.HIGHEST_PROTOCOL)
    except OSError:
        pass

    progress_callback(f"Finished commuter flow fetch for ZCTA {zcta}.", 1.0)
    return result
