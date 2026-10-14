"""Read-only forensic audit of GOLD Triple-BB entry-comparison backtest.

Runs offline from CSV/JSON and (optionally) source .py files. Never calls MT5.
Use --help for CLI options. Does not certify tick fills or strategy profitability.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

NUMERIC = ['entry_price','exit_price','stop_price','target_price','lots','spread_price_at_entry','atr_m15','gross_pnl','commission','net_pnl','r_multiple','equity_before','equity_after']
DATE_COLS = ['entry_broker_clock','exit_broker_clock','signal_broker_clock','h4_event_broker_clock','h1_event_broker_clock']

def record(flags, severity, category, df, note, maximum=1000):
    if df is None or len(df)==0:return
    for i,row in df.head(maximum).iterrows():
        flags.append({'severity':severity,'category':category,'dataset':row['_dataset'], 'row_number':int(row['_row_number']), 'entry_style':str(row.get('entry_style','')), 'signal_broker_clock':str(row.get('signal_broker_clock','')), 'entry_broker_clock':str(row.get('entry_broker_clock','')), 'note':note})

def read_ledger(path, name):
    df=pd.read_csv(path)
    mandatory={'side','entry_price','exit_price','gross_pnl','commission','net_pnl','r_multiple','equity_before','equity_after','signal_broker_clock','entry_broker_clock','exit_broker_clock','exit_reason'}
    missing=mandatory-set(df.columns)
    if missing:raise ValueError(f'{path}: missing columns {sorted(missing)}')
    df['_dataset']=name
    df['_row_number']=np.arange(2,len(df)+2)
    for c in NUMERIC:
        if c in df:df[c]=pd.to_numeric(df[c],errors='coerce')
    for c in DATE_COLS:
        if c in df:df['_dt_'+c]=pd.to_datetime(df[c],utc=True,format='mixed',errors='coerce')
    return df

def static_findings(paths):
    out=[]
    for p in paths:
        if not p or not Path(p).exists():continue
        txt=Path(p).read_text(encoding='utf8')
        name=Path(p).name
        for label,pattern,severity,remark in [
            ('Signal-to-entry time selection',r"ts\.searchsorted\(s\['time'\],\s*side='left'\)",'REVIEW','Entry index is first M5 bar opening >= completed M15 signal time. This is causally plausible but cannot verify quote-level fills.'),
            ('Within-bar stop/target ordering',r"exit_reason\s*=\s*'STOP'\s*if\s*hit_stop\s*else\s*'TARGET'",'LIMITATION','STOP wins when stop and target are both hit inside same M5 bar; exact intrabar order remains unknown.'),
            ('Initial-bar stop handling',r'for r in rows\[start_i:\]','REVIEW','Exits are evaluated in entry M5 bar; requires actual first-bar OHLC/tick replay to verify.'),
            ('Wrong window period labels',r"base\.simulate\(m5,chosen,info,args,'opposite',end\)",'CONFIRMED','Passing each window end as the training/holdout split labels every trade before end as training.'),
            ('H4/H1 event availability',r"'time': r\.close_time",'DESIGN','Sweep events become available at completed higher-timeframe candle closes, not intrabar touches.'),
            ('Higher-timeframe aggregation',r'resample\(rule,\s*label=\'left\',\s*closed=\'left\'','REVIEW','Aggregating H1/H4 from M5 with epoch origin requires checking broker H4 session/calendar boundaries.'),
            ('Time-based close',r'exit_price\s*=\s*float\(r\.close\)','REVIEW','Time exits use last processed bar close; confirm 48-hour cutoff semantics and weekend gaps.'),
            ('Commission semantics',r'commission\s*=\s*lot\*cfg\.commission_lot_roundtrip','ASSUMPTION','Commission is a fixed modeled round-trip amount per lot.'),
        ]:
            if re.search(pattern,txt):out.append({'file':name,'finding':label,'status':severity,'detail':remark})
    return out

def audit_frame(d):
    flags=[];counts={}
    def check(category,mask,severity,note):
        sub=d.loc[mask.fillna(True)] if hasattr(mask,'fillna') else d.loc[mask]
        counts[category]=len(sub)
        record(flags,severity,category,sub,note)
    nanmask=d[['entry_price','exit_price','net_pnl','gross_pnl','commission','lots']].isna().any(axis=1)
    check('missing_required_numbers',nanmask,'FAIL','Missing or unparsable numerical field')
    badtime=d[['_dt_'+x for x in ['signal_broker_clock','entry_broker_clock','exit_broker_clock']]].isna().any(axis=1)
    check('invalid_timestamps',badtime,'FAIL','Missing/unparsable event timestamps')
    check('entry_before_signal',d['_dt_entry_broker_clock']<d['_dt_signal_broker_clock'],'FAIL','Entry opens earlier than completed signal')
    check('entry_equals_signal',d['_dt_entry_broker_clock']==d['_dt_signal_broker_clock'],'REVIEW','Bar-open entry exactly at M15 signal close; no quote-level proof of executable fill')
    check('exit_at_or_before_entry',d['_dt_exit_broker_clock']<=d['_dt_entry_broker_clock'],'FAIL','Exit time <= entry time')
    check('entry_not_m5_aligned',d['_dt_entry_broker_clock'].dt.minute.mod(5).ne(0) | d['_dt_entry_broker_clock'].dt.second.ne(0),'FAIL','Entry timestamp not M5 boundary')
    check('exit_not_m5_aligned',d['_dt_exit_broker_clock'].dt.minute.mod(5).ne(0) | d['_dt_exit_broker_clock'].dt.second.ne(0),'REVIEW','Exit timestamp not M5 boundary')
    check('pnl_identity_mismatch',(d.gross_pnl-d.commission-d.net_pnl).abs()>0.035,'FAIL','gross - commission must equal net, within rounding')
    check('equity_delta_mismatch',(d.equity_after-d.equity_before-d.net_pnl).abs()>0.045,'FAIL','Ending equity - beginning equity must equal net P&L')
    check('negative_commission_or_lots',(d.commission<0)|(d.lots<=0),'FAIL','Invalid commission/lot size')
    check('invalid_side',~d.side.isin(['BUY','SELL']),'FAIL','Unexpected side')
    check('unknown_exit_reason',~d.exit_reason.isin(['STOP','TARGET','TIME']),'REVIEW','Unknown exit type')
    check('period_label_mismatch',d.period.eq('training') & d._dataset.eq('holdout'),'CONFIRMED','Holdout row incorrectly labeled training')
    # Trade-level contract inference from observed entry and exit prices; not a full broker spec validation.
    move=np.where(d.side=='BUY',d.exit_price-d.entry_price,d.entry_price-d.exit_price)
    x=move*d.lots
    implied=pd.Series(np.where(np.abs(x)>1e-8,d.gross_pnl/x,np.nan),index=d.index)
    counts['implied_price_value_median_usd_per_unit_per_lot']=float(implied.replace([np.inf,-np.inf],np.nan).median()) if len(d) else None
    counts['implied_price_value_p05']=float(implied.quantile(.05)) if len(d) else None
    counts['implied_price_value_p95']=float(implied.quantile(.95)) if len(d) else None
    counts['trade_count']=len(d)
    counts['net_pnl_sum']=round(float(d.net_pnl.sum()),2)
    counts['commission_sum']=round(float(d.commission.sum()),2)
    counts['gross_pnl_sum']=round(float(d.gross_pnl.sum()),2)
    counts['same_bar_5m_exits']=int(((d['_dt_exit_broker_clock']-d['_dt_entry_broker_clock']).dt.total_seconds()<=300).sum())
    bystyle=d.groupby('entry_style',dropna=False).agg(trades=('net_pnl','size'),net_usd=('net_pnl','sum'),commission_usd=('commission','sum'),avg_r=('r_multiple','mean'),win_rate=('net_pnl',lambda x:100*(x>0).mean())).reset_index()
    return counts,flags,bystyle

def sensitivity(df,add_slip_price,spread_mults):
    # Audit-only cost perturbation. Reprices P&L but DOES NOT rerun stops, targets, sizing, or equity.
    rows=[]
    for name,sub in df.groupby('_dataset'):
        move=np.where(sub.side=='BUY',sub.exit_price-sub.entry_price,sub.entry_price-sub.exit_price)
        exposure=move*sub.lots
        unit=np.where(abs(exposure)>1e-8,sub.gross_pnl/exposure,np.nan)
        med=float(np.nanmedian(unit)) if len(unit) else float('nan')
        for m in spread_mults:
            # Additional spread impacts buy entry or sell exit once, approximated per trade.
            extra_spread=(m-1)*sub.spread_price_at_entry.clip(lower=0).fillna(0)*sub.lots*med
            for slip in add_slip_price:
                extra_slip=2*slip*sub.lots*med
                adjusted=sub.net_pnl-extra_spread-extra_slip
                rows.append({'dataset':name,'spread_multiplier':m,'additional_slippage_price_per_side':slip,'assumed_usd_per_price_unit_per_lot':med,'base_net_usd':round(float(sub.net_pnl.sum()),2),'approx_net_usd':round(float(adjusted.sum()),2),'extra_cost_usd':round(float((extra_spread+extra_slip).sum()),2),'method':'POST-HOC COST SHOCK ONLY: unchanged trade outcomes/lot sizes'})
    return pd.DataFrame(rows)

def report_md(summary,static,style,sensitivity):
    def e(s):return str(s)
    lines=['# Step 10A — GOLD Triple BB Backtest Execution Audit','',f"Generated: {summary['generated_utc']}", '', '**Scope:** offline audit of uploaded holdout and rolling-OOS trades and source scripts. No MT5 orders or API calls. **IMPORTANT:** holdout includes four mutually alternative strategies; their P&L sum is NOT one realizable portfolio return.','', '## Findings by dataset','']
    for k,c in summary['datasets'].items():
        lines+= [f'### {k}',f"- Trades: **{c['trade_count']}**; net P&L: **${c['net_pnl_sum']:,.2f}**; recorded commissions: **${c['commission_sum']:,.2f}**.",f"- Confirmed wrong period labels: **{c['period_label_mismatch']}**; entries at signal timestamp: **{c['entry_equals_signal']}**; exits within one M5 bar: **{c['same_bar_5m_exits']}**.",f"- P&L identity failures: **{c['pnl_identity_mismatch']}**; equity delta failures: **{c['equity_delta_mismatch']}**; entries before signals: **{c['entry_before_signal']}**.", '']
    lines+=['## Source-code inspection','']
    for f in static:lines.append(f"- **{f['status']} — {f['finding']}** ({f['file']}): {f['detail']}")
    lines+=['','## Limits to verification','', '- **Look-ahead bias:** no entry-before-signal issue in the ledger can be checked; the original decision-time code uses completed M15/H1/H4 events, but the source does not prove that every fill was executable at that exact M5 open. Tick replay is required for fill-level certification.','- **Timing:** timestamps represent raw broker-clock labels, not historically verified UTC; H4 resampling anchored to epoch and DST changes need comparison against native broker H4 bars.','- **Spread/slippage:** recorded spread and fixed modeled slippage are assumptions. Adjusted-cost scenarios do NOT replay trades and must not be interpreted as fully rebacktested returns.','- **Stop/target:** bar-level stop-first tie-break is conservative for ambiguous same-bar touches but order, gap fills and stop slippage cannot be verified without tick data.','- **Ledger:** P&L and equity identities are checked row-by-row; independent contract-size/tick-value validation requires saved symbol specifications.','- **Win-rate significance:** the four holdout methods share setup history and are not independent trials; selecting a method now based on the final holdout would introduce selection bias.','', '## Priority fixes','', '1. Correct the `window_trades` split/period annotation. Set period at the caller using the actual fold or holdout boundary; preserve separate fold identity.','2. Save explicit signal-observed time, entry bar time, entry fill model, stop-hit and target-hit booleans, and M5 bar OHLC/spread for each trade.','3. Build an optional tick-replay verifier on a bounded sample of trades. Reject any historical decision that uses future candles.','4. Match historical M5→H1/H4 aggregation against native FxPro broker timeframe bars across DST transitions.','5. Re-run 6m/2m walk-forward folds and final holdout using corrected labels and verified execution policy.','', '## Cost-shock sensitivity', '', 'The bundled `cost_sensitivity.csv` varies spread multiplier and extra per-side slippage using inferred USD/price-unit exposure. It is only a post-hoc ledger stress test and **not** a new execution simulation.','', '## Audit status','', '**PARTIALLY VERIFIED — NOT CERTIFIED FOR LIVE EXECUTION.** The code and ledger support limited accounting and chronology checks, but not exact historical bid/ask fills or absence of all look-ahead errors.']
    return '\n'.join(lines)+'\n'

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--holdout',type=Path,default=Path('holdout_trades.csv'))
    p.add_argument('--rolling',type=Path,default=Path('rolling_oos_trades.csv'))
    p.add_argument('--entry-report',type=Path,default=Path('entry_report.json'))
    p.add_argument('--entry-source',type=Path,default=Path('gold_entry_comparison.py'))
    p.add_argument('--base-source',type=Path,default=Path('gold_bb_reversal.py'))
    p.add_argument('--output',type=Path,default=Path('step10a_results'))
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    tables=[]
    for name,path in [('holdout',args.holdout),('rolling_oos',args.rolling)]:
        if path.exists():tables.append(read_ledger(path,name))
    if not tables:raise SystemExit('No trade ledgers found. Supply --holdout/--rolling paths.')
    data=pd.concat(tables,ignore_index=True)
    summary={'generated_utc':datetime.now(timezone.utc).isoformat(),'scope':'OFFLINE TRADE LEDGER + SOURCE INSPECTION; NO TICK REPLAY', 'datasets':{}, 'limitations':['Broker/DST clock not certified','M5 bar-based fills not tick-level','Cost scenarios do not re-execute trades']}
    flags=[];styles=[]
    for name,d in data.groupby('_dataset'):
        c,f,s=audit_frame(d);summary['datasets'][name]=c;flags.extend(f);s.insert(0,'dataset',name);styles.append(s)
    sources=static_findings([args.entry_source,args.base_source]);summary['source_checks']=sources
    shock=sensitivity(data,[0,.10,.25],[1,1.5,2])
    pd.DataFrame(flags,columns=['severity','category','dataset','row_number','entry_style','signal_broker_clock','entry_broker_clock','note']).to_csv(args.output/'audit_flags.csv',index=False)
    pd.concat(styles,ignore_index=True).to_csv(args.output/'trade_style_summary.csv',index=False)
    shock.to_csv(args.output/'cost_sensitivity.csv',index=False)
    (args.output/'audit_report.json').write_text(json.dumps(summary,indent=2,allow_nan=False),encoding='utf8')
    (args.output/'audit_report.md').write_text(report_md(summary,sources,styles,shock),encoding='utf8')
    print(json.dumps({k:{n:v for n,v in d.items() if n in ['trade_count','period_label_mismatch','entry_equals_signal','pnl_identity_mismatch','equity_delta_mismatch','entry_before_signal','same_bar_5m_exits']} for k,d in summary['datasets'].items()},indent=2))
    print('Saved audit reports to',args.output.resolve(),'— NO TRADES EXECUTED')

if __name__=='__main__':main()
