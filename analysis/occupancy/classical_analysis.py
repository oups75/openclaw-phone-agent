import json, warnings
import numpy as np
warnings.filterwarnings('ignore')
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.statespace.sarimax import SARIMAX

d = json.load(open('trend_data.json'))
labels, monthly = d['labels'][:-1], np.array(d['monthly'][:-1])  # drop partial 2026-07

# --- STL decomposition (robust) ---
stl = STL(monthly, period=12, robust=True).fit()
trend, seas, resid = stl.trend, stl.seasonal, stl.resid

# seasonal profile (avg seasonal effect per calendar month)
prof = {}
for lab, s in zip(labels, seas):
    m = int(lab[5:7]); prof.setdefault(m, []).append(s)
profile = {m: round(float(np.mean(v)),1) for m,v in sorted(prof.items())}

# anomalies: residual beyond 2.5 sigma
sd = np.std(resid)
anoms = [(labels[i], round(float(monthly[i]),1), round(float(resid[i]),1))
         for i in range(len(labels)) if abs(resid[i]) > 2.5*sd]

# --- SARIMA backtest (12-month holdout) ---
hold=12
train, test = monthly[:-hold], monthly[-hold:]
def fit_forecast(y, h):
    m = SARIMAX(y, order=(1,0,1), seasonal_order=(1,1,1,12),
                enforce_stationarity=False, enforce_invertibility=False).fit(disp=False)
    f = m.get_forecast(h)
    return f.predicted_mean, f.conf_int(alpha=0.2)  # 80% interval
bt_pred,_ = fit_forecast(train, hold)
mae_sarima = float(np.mean(np.abs(bt_pred - test)))
mae_naive  = float(np.mean(np.abs(monthly[-2*hold:-hold] - test)))

# --- final 18-month forecast ---
H=18
pred, ci = fit_forecast(monthly, H)
def ym_add(label,k):
    y,m=map(int,label.split('-')); m+=k; y+=(m-1)//12; m=(m-1)%12+1
    return f"{y}-{m:02d}"
fut=[ym_add(labels[-1],i+1) for i in range(H)]
clip=lambda v: round(float(max(0,min(100,v))),1)
out={
 'method':'STL + SARIMA(1,0,1)(1,1,1,12) — interim until TimesFM runs',
 'seasonal_profile_pts': profile,
 'trend_now': round(float(trend[-1]),1),
 'trend_peak': {'value':round(float(trend.max()),1),'at':labels[int(trend.argmax())]},
 'anomalies': anoms,
 'backtest': {'mae_sarima':round(mae_sarima,1),'mae_seasonal_naive':round(mae_naive,1)},
 'forecast': [{'month':fut[i],'point':clip(pred[i]),'lo80':clip(ci[i][0]),'hi80':clip(ci[i][1])} for i in range(H)],
}
json.dump(out, open('classical_forecast.json','w'), indent=1)
print(json.dumps(out, indent=1))
