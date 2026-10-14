"""Optional, user-triggered OpenAI review of CLOSED paper trades. No orders and no autonomous calls."""
import argparse,json,os
from pathlib import Path
from dotenv import load_dotenv
from paper_core14 import connect,utcnow

ROOT=Path(__file__).resolve().parent

def review(db_path,position_id,model=None):
 load_dotenv(ROOT.parent/'.env');load_dotenv(ROOT/'.env')
 model=model or os.getenv('OPENAI_REVIEW_MODEL','gpt-4.1-mini')
 if not os.getenv('OPENAI_API_KEY'):raise RuntimeError('OPENAI_API_KEY is not configured; no API call made')
 db=connect(db_path)
 try:
  row=db.execute("SELECT * FROM positions WHERE id=? AND status='CLOSED'",(int(position_id),)).fetchone()
  if row is None:raise ValueError('A closed paper position with that ID was not found')
  trade={k:row[k] for k in ('id','mode','policy','side','signal_at','opened_at','closed_at','entry','initial_stop','target','exit','exit_reason','pnl','atr','lots')}
  from openai import OpenAI
  result=OpenAI(timeout=35,max_retries=1).responses.create(
    model=model,
    instructions=('You are a trading research auditor, not a trading signal generator. Review one historical VIRTUAL GOLD paper trade. '
                  'Use only supplied numbers. Identify uncertainty, missing intrabar data and testable hypotheses. '
                  'Never claim causation or win probability, never propose live orders. Return short JSON.'),
    input=json.dumps(trade),
    text={'format':{'type':'json_schema','name':'trade_review','strict':True,'schema':{
      'type':'object','properties':{'summary':{'type':'string'},'plausible_factors':{'type':'array','items':{'type':'string'}},
      'missing_evidence':{'type':'array','items':{'type':'string'}},'research_question':{'type':'string'}},
      'required':['summary','plausible_factors','missing_evidence','research_question'],'additionalProperties':False}}})
  if not result.output_text:raise RuntimeError('Empty AI response')
  parsed=json.loads(result.output_text)
  db.execute('INSERT INTO ai_reviews(position_id,created_at,model,review_json,error) VALUES(?,?,?,?,NULL) '
             'ON CONFLICT(position_id) DO UPDATE SET created_at=excluded.created_at,model=excluded.model,review_json=excluded.review_json,error=NULL',
             (position_id,utcnow(),model,json.dumps(parsed)))
  db.commit()
  return parsed
 finally:db.close()

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--db',default=str(ROOT/'step14_paper.db'));p.add_argument('--position-id',type=int,required=True);p.add_argument('--model',default=None)
 a=p.parse_args();print(json.dumps(review(a.db,a.position_id,a.model),indent=2))
