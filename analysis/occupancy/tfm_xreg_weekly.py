# TimesFM 2.5 + XReg covariates on WEEKLY occupancy for the "chambre romantique".
# Requires: pip install "timesfm[torch]" jax scikit-learn   (CPU jax is fine)
#           + network access to huggingface.co
# Inputs:  weekly_occupancy.csv (595 weeks), weekly_covariates.csv (history+future)
# Output:  tfm_xreg_results.json
#
# Covariates are deterministic calendar features (knowable for the future):
#   sin/cos week-of-year, French public-holiday count per week,
#   approximate school-holiday fraction (zone-union windows), summer-peak flag.
#
# Backtest design: 52-week holdout, three models on identical data:
#   (a) seasonal-naive (value 52 weeks earlier)
#   (b) TimesFM 2.5 plain
#   (c) TimesFM 2.5 + XReg ("xreg + timesfm" mode)

import csv
import json

import numpy as np
import torch
import timesfm

torch.set_float32_matmul_precision("high")

hist = list(csv.DictReader(open("weekly_occupancy.csv")))
cov = list(csv.DictReader(open("weekly_covariates.csv")))
y = np.array([float(r["occ_pct"]) for r in hist], dtype=np.float32)
labels = [r["week_start"] for r in hist]
N = len(y)
COV_KEYS = ["sin_woy", "cos_woy", "school_hol_frac", "public_hol_count", "is_summer_peak"]
covs = {k: [float(r[k]) for r in cov] for k in COV_KEYS}
assert [r["week_start"] for r in cov][:N] == labels, "covariate/history misalignment"

model = timesfm.TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch")
model.compile(
    timesfm.ForecastConfig(
        max_context=1024,
        max_horizon=128,
        normalize_inputs=True,
        use_continuous_quantile_head=True,
        force_flip_invariance=True,
        infer_is_positive=True,
        fix_quantile_crossing=True,
        return_backcast=True,
    )
)

def tfm_plain(context, horizon):
    pf, qf = model.forecast(horizon=horizon, inputs=[context])
    return pf[0], qf[0]

def tfm_xreg(context_len, horizon):
    dyn = {k: [v[: context_len + horizon]] for k, v in covs.items()}
    out, xreg_out = model.forecast_with_covariates(
        inputs=[y[:context_len]],
        dynamic_numerical_covariates=dyn,
        xreg_mode="xreg + timesfm",
        ridge=1.0,
        force_on_cpu=True,
    )
    return np.asarray(out[0]), np.asarray(xreg_out[0])

# ---------- backtest: last 52 weeks held out ----------
HOLD = 52
ctx_len = N - HOLD
actual = y[ctx_len:]
naive = y[ctx_len - 52 : ctx_len]
plain_bt, _ = tfm_plain(y[:ctx_len], HOLD)
xreg_bt, _ = tfm_xreg(ctx_len, HOLD)
mae = lambda p: float(np.mean(np.abs(np.asarray(p)[:HOLD] - actual)))
maes = {
    "seasonal_naive": round(mae(naive), 2),
    "timesfm_plain": round(mae(plain_bt), 2),
    "timesfm_xreg": round(mae(xreg_bt), 2),
}

# also compare at MONTHLY aggregation (fairer vs earlier monthly numbers)
def monthly_mae(pred):
    bym = {}
    for i in range(HOLD):
        m = labels[ctx_len + i][:7]
        bym.setdefault(m, []).append((float(np.asarray(pred)[i]), float(actual[i])))
    return round(
        float(np.mean([abs(np.mean([p for p, _ in v]) - np.mean([a for _, a in v]))
                       for v in bym.values()])), 2)
maes_monthly = {
    "seasonal_naive": monthly_mae(naive),
    "timesfm_plain": monthly_mae(plain_bt),
    "timesfm_xreg": monthly_mae(xreg_bt),
}

# ---------- final forecast: through 2027-12-27 ----------
H = len(covs["sin_woy"]) - N  # 78 weeks
point_plain, q_plain = tfm_plain(y, H)
point_xreg, _ = tfm_xreg(N, H)

from datetime import date, timedelta
start = date.fromisoformat(labels[-1]) + timedelta(days=7)
fut_labels = [(start + timedelta(days=7 * i)).isoformat() for i in range(H)]

clip = lambda a: [round(float(max(0, min(100, v))), 1) for v in np.asarray(a)[:H]]
out = {
    "series": "weekly guest-lodging occupancy %",
    "n_history_weeks": N,
    "backtest_52w": {"mae_weekly": maes, "mae_monthly_agg": maes_monthly},
    "future_labels": fut_labels,
    "timesfm_plain": {"point": clip(point_plain),
                      "p10": clip(q_plain[:, 1]), "p90": clip(q_plain[:, 9])},
    "timesfm_xreg": {"point": clip(point_xreg)},
}
json.dump(out, open("tfm_xreg_results.json", "w"), indent=1)
print("Backtest 52w MAE (weekly):", maes)
print("Backtest 52w MAE (monthly agg):", maes_monthly)
print("Forecast horizon:", H, "weeks ->", fut_labels[-1])
