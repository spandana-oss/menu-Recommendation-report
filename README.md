# AI Menu Intelligence Platform

Local Streamlit app. Sidebar nav: Overview → Menu Refresh → Menu Creation → Settings.
**Menu Refresh** and **Menu Creation** are both wired up.

**The Menu Refresh page is one button.** Upload whatever menu data you have,
optionally give it a ZIP code, click **Start Menu Refresh**, and it runs the
complete pipeline — file-type detection, market-data fetch, the full scoring
engine, the Keep/Refresh/Remove decision for every item, a replacement brief
for anything removed, the workbook, and the report — without asking which
"mode" you're in. That auto-detection and orchestration lives in
`workflow_orchestrator.py`; everything else is a module it calls.

## Files

```
menu_intelligence_app/
├── app.py                      # Streamlit entry point — sidebar nav + pages
├── workflow_orchestrator.py     # THE product: one function, file-type auto-detect -> full pipeline -> outputs
├── replacement_recommender.py   # for every REMOVE item: what gap it leaves + what a replacement should target
├── analysis_engine.py          # Sections A/B/D/E/F/G/H/I — validated exactly vs real data
├── role_classifier.py          # heuristic Weekday/Weekend Role assignment
├── duplicate_detector.py       # structural bundles + near-dupes + unique ingredients
├── market_data.py              # Census ACS5 + LODES SA/SE/SI commuter-flow fetch
├── menu_intelligence_ingest.py # reads the "Menu Intelligence" schema (financial + market data pre-merged)
├── sales_data_builder.py       # builds Menu Intelligence financial fields from a raw POS sales export
├── input_normalizer.py          # alias-based column matching + restaurant/comparable normalization + generates missing Category/Ingredients/Theoretical Cost
├── run_menu_creation.py        # Menu Creation pipeline: restaurant list + optional comparable metrics -> workbook
├── creation_engine.py          # ZCTA context, category landscape, comparable selection, profitability math
├── dish_library.py             # curated dish templates and tier upgrades
├── output_row_builder.py       # workbook rows, including comparable price/qty evidence fields
├── creative_naming.py          # final display-name generation for created items
├── run_analysis.py             # per-schema builders + workbook construction (called by the orchestrator)
├── report_engine.py            # workbook -> report HTML (importable + CLI)
├── report_template.html        # tokenized report template (CSS/JS untouched)
├── requirements.txt
└── README.md
```

## Run it in VS Code

1. Open this folder in VS Code.
2. Open a terminal (`` Ctrl+` ``) and create a virtual environment (recommended):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run the app:
   ```bash
   streamlit run app.py
   ```
5. Streamlit prints a local URL (usually `http://localhost:8501`) — open it
   in your browser, or Ctrl/Cmd-click it in the VS Code terminal.

Streamlit auto-reloads on file save, so you can leave it running while you edit.

## Using the Menu Refresh page

There's one flow: upload a file, optionally give it a ZIP code, click
**Start Menu Refresh**. That's the whole interaction — no mode picker.

Under the hood, `workflow_orchestrator.detect_input_type()` fingerprints
whatever you uploaded by its column headers and routes it automatically:

| If your file looks like... | It's treated as | Market data comes from |
|---|---|---|
| Has `Recommendation (KEEP/REFRESH/REMOVE)` and `No.` | a finished analysis workbook | already in the file |
| Has `MENU_ITEM_NAME`, `FAMILY_GROUP_NAME`, `STORE_QTY_SOLD` | a raw POS sales export | embedded ZCTA (if present) → auto-fetch → your manual entry |
| Has `Ingredient Cost ($)` or `Prep Cost ($)` | a Menu Intelligence workbook | embedded in the file → auto-fetch → manual entry |
| Has *any* recognizable item-name + price + quantity-sold column | a raw item list | embedded ZCTA (if present) → auto-fetch → manual entry |

That last row is deliberately broad — `input_normalizer.py` matches a long
list of real-world header spellings (`Item Name`, `Selling Price`,
`Quantity Sold`, `MENU_ITEM_PRICE`, `unit cost`, etc. — see
`CANONICAL_ALIASES` to add more) and generates whatever's missing:
- **No Category column** → inferred from the item name via keyword
  matching (burger/salad/soup/wrap/etc.), or `UNKNOWN` if nothing matches.
- **No Ingredients column** → left blank (duplicate detection runs at
  reduced fidelity — same tradeoff documented everywhere else in this
  project when ingredients aren't available).
- **No Theoretical Cost column** → estimated from the item's category
  food-cost benchmark (the same validated benchmarks `sales_data_builder.py`
  uses — Entrée ~27%, Sandwich/Wrap ~30%, Sides ~22%, etc.).

Every generated field is logged as a warning shown in the app, naming
exactly what was inferred and how — nothing is silently made up. A file
with only **Item Name, Price, and Quantity Sold** — the actual minimum
the engine can compute anything from — now goes all the way through to a
workbook and report; only name, price, and quantity truly can't be
inferred from nothing.

Whichever it is, the same sequence runs: market-data fetch (if needed) →
role classification → duplicate detection → the full Sections A–I engine →
Keep/Refresh/Remove for every item → **a replacement brief for every REMOVE
item** (what market gap it leaves, and what a new item should target —
see `replacement_recommender.py`) → the workbook → the report. Progress
prints live as each step runs.

If the automatic market-data fetch fails (or you'd rather not wait on it),
open "enter it manually" above the button and fill in at least Residents —
anything entered there overrides the fetch.

### The replacement brief — the piece that used to be missing

For every item the engine decides to REMOVE, it doesn't just say "remove
this and stop." It classifies *why* (duplication / bottom-quintile margin /
bottom-quintile popularity / ghost revenue) and computes, from the menu
that survives the removal: whether that item's category is still
adequately covered, what price band similar surviving items command, and
which occasion (weekday/weekend role) the removed item served — so a
replacement can be aimed at real, already-identified demand instead of a
guess. What it deliberately does NOT do is name a dish or write a
description; that's the same creative call REFRESH's new-item content
already isn't automated for. The brief is the analytical direction a
person (or an assistant, hosted this output) would use to actually design
the replacement.

### What's genuinely automated vs. what needs review

| Piece | Confidence | Why |
|---|---|---|
| Popularity, Profitability, Forecast, Recommendation rules | **Validated exactly** | Reproduced every formula in the spec number-for-number against a real 54-item menu, including every edge case in the rounding conventions and quintile boundaries. |
| Structural-bundle detection | **Validated exactly** | Mechanical rule (category + name pattern), 10/10 match, 0 false positives on the test menu. |
| Unique-ingredient detection | **Mechanical** | Directly implements the spec's rule (ingredient appears in exactly 1 dish menu-wide) — no judgment involved. Degrades gracefully to name+category-only matching when a file has no Ingredients column. |
| Theoretical Cost resolution | **Validated exactly** on given values; **estimated** via category benchmark or flat default when Ingredient/Prep cost is also missing | Benchmarks are this exact menu's own real TC/Price ratios per category — recalibrate for a different restaurant's cost structure. |
| Sales-export → Menu Intelligence formulas (Theoretical Cost, Ingredient/Prep split, Profitability Value, Total Revenue/Profit, 3/6/9-month estimates) | **Validated exactly** | Reverse-engineered from a real reference Menu Intelligence workbook and checked against all 54 of its items — 52/54 exact, 2/54 differ by <0.003% (floating-point noise in the source file's own rounded values, not a formula error). |
| Weekday/Weekend Role assignment | **Heuristic** | Price/category rules-of-thumb. Matched the real menu's exact role label ~65-70% of the time, but downstream recommendations still matched exactly on the test menu — close enough to rank items correctly even when the label itself differs. |
| Near-duplicate detection | **Heuristic, review recommended** | Catches duplicates that share vocabulary; misses conceptually-similar-but-lexically-different pairs (e.g. "Chips" vs "French Fries"). Needs an Ingredients column to work well — without one, expect a materially different Keep/Refresh/Remove split (confirmed on the test menu: 32/20/2 without ingredients vs. the true 27/23/4 with them). The app lists every candidate pair it found — check that list. |
| Replacement briefs for REMOVE items | **Mechanical analysis, template-generated text** | Category coverage, price band, and occasion are computed from real survivor data; the brief's wording is a template, not invented content — no dish name/recipe is generated. |
| Input-file auto-detection | **Deterministic, header-based** | Fingerprints the uploaded file's column headers against four known schemas; raises a clear error if none match rather than guessing. |
| Census ACS5 residents fetch | **Solid, logic-verified** | Simple documented REST/JSON endpoint. |
| LODES commuter-flow fetch | **Rewritten and logic-verified against real numbers** | Aggregates LODES Origin-Destination SA/SE/SI (age/earnings/industry) columns directly for whichever Census blocks fall in the target ZCTA — this is what OnTheMap itself is built from. Reproduced every one of this project's known reference numbers for ZCTA 38103 exactly when checked against embedded ground-truth data. An earlier version of this fetcher tried to scrape OnTheMap's website directly and reliably returned zeros — see the docstring at the top of `market_data.py` for why, and why the LODES approach replaced it. |
| REFRESH creative content (new name/description/ingredients) | **Not generated** | Genuine judgment call, not a formula. New *price* is computed automatically (spec's mechanical pricing rule); the description fields are marked `[NEEDS INPUT]` in the generated workbook. |

I still can't reach `api.census.gov`, `tigerweb.geo.census.gov`, or
`lehd.ces.census.gov` from my build sandbox to run the fetchers live — test
them on your machine before fully trusting the auto-fetch button. Manual
entry is always available as a fallback right below it.

## Menu Creation automation

Menu Creation now runs as a deterministic pipeline:

1. `app.py` renders the restaurant-list uploader, the optional comparable-data uploader, and the generation controls.
2. `input_normalizer.py` normalizes the restaurant list and the optional comparable file into the schema the pipeline expects.
3. `run_menu_creation.py` orchestrates the end-to-end flow for the selected ZCTA.
4. `market_data.py` fetches Census ACS and LODES commuter data for that ZCTA.
5. `creation_engine.py` builds the ZCTA context, category landscape, comparable selection, and pricing inputs.
6. `dish_library.py` supplies the curated dish templates and tier upgrades.
7. `output_row_builder.py` assembles the workbook rows, including comparable restaurant price and quantity-sold fields when the optional file is present.
8. `creative_naming.py` generates the final display names for the new items.

The optional comparable-data file should include a restaurant name plus price and quantity-sold columns. When present, those values are used to fill the comparable restaurant fields in the generated workbook.

## Troubleshooting

- **"Could not find header row"** — the workbook's column layout doesn't
  match what `report_engine.py` expects (`REQUIRED_COLUMNS`). Check the
  first 10 rows have a `No.` cell in column A as the header row.
- **"Workbook is missing required column(s)"** — same idea; the error
  message lists exactly which columns weren't found.
- **Commuter-flow auto-fetch is slow the first time for a new state** —
  expected. It streams and filters an ~8 million row national Census file
  once per state, then caches the result to disk in `.census_cache/`
  next to the app — every later run for that state (this session or a
  future one) loads instantly instead of re-downloading. Delete that
  folder if you ever need to force a refresh.
- **Commuter-flow auto-fetch fails outright** — the aggregation logic is
  checked against known real numbers but hasn't been run against live
  Census servers from my end. Fill the market-intelligence form manually
  if it doesn't work, and the error message should say exactly what step
  failed.
- Port already in use: `streamlit run app.py --server.port 8502`
