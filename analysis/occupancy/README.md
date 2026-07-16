# Occupancy analysis — "chambre romantique (+ atelier)"

Monthly guest-lodging occupancy derived from the owner's booking calendar
(Feb 2015 – Jul 2026). Occupancy = distinct occupied nights / nights in month;
"bloqué" blocks and non-lodging personal events are excluded. Aggregated
rates only — the raw booking history (guest names) is deliberately NOT
committed here.

## Files
- `occupancy_monthly_guestonly.csv` — monthly occupancy rates.
- `trend_data.json` — monthly series + 12-month rolling average (chart/model input).
- `tfm_analysis.py` — TimesFM 2.5 deep analysis: 18-month quantile forecast
  + 12-month holdout backtest vs seasonal-naive.
  Run: `pip install "timesfm[torch]"` then `python tfm_analysis.py`
  (downloads `google/timesfm-2.5-200m-pytorch` from huggingface.co).
- `classical_analysis.py` / `classical_forecast.json` — interim STL + SARIMA
  analysis and its results (seasonal profile, trend, anomalies, forecast).
