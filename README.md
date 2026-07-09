# FoundationIntelligencePlatform

## Official Charity Commission register data

The dashboard can use the Charity Commission for England and Wales daily public
register extract as a local enrichment source. Streamlit never calls the live API.

Download and verify all non-trustee JSON extracts:

```bash
python -m src.pipelines.download_charity_commission_bulk
```

Build the local SQLite index used by the dashboard:

```bash
python -m src.pipelines.build_charity_commission_index
```

The downloaded archives, extracted JSON files, and generated SQLite database are
local caches and are excluded from Git. The trustee extract is deliberately not
downloaded because it is unnecessary for organization enrichment and would add
personal data that the UI does not display.

Run the dashboard:

```bash
streamlit run src/dashboard/app.py
```
