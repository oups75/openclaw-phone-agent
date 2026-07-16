# TimesFM 2.5 deep analysis of the "chambre romantique" rental occupancy series.
# Requires: pip install "timesfm[torch]"  +  network access to huggingface.co
# Data: trend_data.json (monthly guest-lodging occupancy %, 2015-02..2026-07)
# Usage: python tfm_analysis.py

import json
import numpy as np
import torch
import timesfm

torch.set_float32_matmul_precision("high")

d = json.load(open('trend_data.json'))
labels, monthly = d['labels'], d['monthly']  # 2015-02 .. 2026-07
series = np.array(monthly, dtype=np.float32)
# Drop the last point (2026-07 is a partial month prorated to 10 nights) for modeling
ctx_labels, ctx = labels[:-1], series[:-1]
print("context:", ctx_labels[0], "->", ctx_labels[-1], f"({len(ctx)} months)")

model = timesfm.TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch")
model.compile(
    timesfm.ForecastConfig(
        max_context=256,
        max_horizon=64,
        normalize_inputs=True,
        use_continuous_quantile_head=True,
        force_flip_invariance=True,
        infer_is_positive=True,
        fix_quantile_crossing=True,
    )
)

# 1) Main forecast: Jul 2026 .. Dec 2027 (18 months from end of context)
H = 18
pf, qf = model.forecast(horizon=H, inputs=[ctx])
pf, qf = pf[0], qf[0]   # (H,), (H, 10)

# 2) Backtest: hold out last 12 full months (2025-07 .. 2026-06)
hold = 12
bt_pf, _ = model.forecast(horizon=hold, inputs=[ctx[:-hold]])
bt_pf = bt_pf[0]
actual = ctx[-hold:]
mae_tfm = float(np.mean(np.abs(bt_pf - actual)))
# seasonal-naive baseline: same month last year
naive = ctx[-2*hold:-hold]
mae_naive = float(np.mean(np.abs(naive - actual)))

def ym_add(label, k):
    y, m = map(int, label.split('-')); m += k
    y += (m-1)//12; m = (m-1)%12+1
    return f"{y}-{m:02d}"

fut_labels = [ym_add(ctx_labels[-1], i+1) for i in range(H)]
out = {
  "context_end": ctx_labels[-1],
  "future_labels": fut_labels,
  "point": [round(float(v),1) for v in pf],
  "p10": [round(float(v),1) for v in qf[:,1]],
  "p50": [round(float(v),1) for v in qf[:,5]],
  "p90": [round(float(v),1) for v in qf[:,9]],
  "backtest": {
    "labels": ctx_labels[-hold:],
    "pred": [round(float(v),1) for v in bt_pf],
    "actual": [round(float(v),1) for v in actual],
    "mae_timesfm": round(mae_tfm,2),
    "mae_seasonal_naive": round(mae_naive,2),
  },
}
json.dump(out, open('tfm_forecast.json','w'), indent=1)
print("\nBacktest MAE  TimesFM: %.1f pts   seasonal-naive: %.1f pts" % (mae_tfm, mae_naive))
print("\nForecast (point [p10..p90]):")
for l, p, lo, hi in zip(fut_labels, out['point'], out['p10'], out['p90']):
    print(f"  {l}: {p:5.1f}%  [{lo:.0f} .. {hi:.0f}]")
