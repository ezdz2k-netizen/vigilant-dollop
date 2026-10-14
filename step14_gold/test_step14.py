import tempfile,unittest,sqlite3
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
from paper_core14 import connect,seed,paper_open,manage_quotes,assess_first_full_bar
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=connect(Path(self.tmp.name)/'p.db');seed(self.db,10000)
  self.info=SimpleNamespace(trade_tick_size=.01,trade_tick_value=1.,volume_min=.01,volume_max=10.,volume_step=.01)
  self.sig={'side':'BUY','atr':2.,'sweep_low':99.,'sweep_high':105.,'middle':110.,'time':pd.Timestamp('2026-10-14T00:15:00Z'),'episode':'H1:BUY:1'}
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def test_portfolios(self):self.assertEqual(self.db.execute('SELECT count(*) FROM portfolios').fetchone()[0],6)
 def test_duplicate_entry(self):
  a=paper_open(self.db,self.sig,'H1','baseline',103,103.2,'2026-10-14T00:15:05+00:00',self.info)
  b=paper_open(self.db,self.sig,'H1','baseline',103,103.2,'2026-10-14T00:15:05+00:00',self.info)
  self.assertTrue(a[0]);self.assertFalse(b[0])
 def test_quote_target_and_balance(self):
  self.assertTrue(paper_open(self.db,self.sig,'H1','baseline',103,103.2,'2026-10-14T00:15:05+00:00',self.info)[0]);manage_quotes(self.db,110.5,110.7)
  p=self.db.execute('SELECT * FROM positions').fetchone();self.assertEqual(p['status'],'CLOSED');self.assertEqual(p['exit_reason'],'TARGET')
  self.assertAlmostEqual(self.db.execute("SELECT equity FROM portfolios WHERE mode='H1' AND policy='baseline'").fetchone()[0],10000+p['pnl'])
 def test_first_full_m15_not_partial(self):
  self.assertTrue(paper_open(self.db,self.sig,'H1','weak_be',103,103.2,'2026-10-14T00:16:00+00:00',self.info)[0]);p=self.db.execute('SELECT * FROM positions').fetchone();self.assertIn('00:45',p['first_full_boundary'])
  bar={'open':104.,'high':104.2,'low':103.5,'close':103.7}
  assess_first_full_bar(self.db,pd.Timestamp('2026-10-14T00:30:00Z'),bar)
  self.assertEqual(self.db.execute('SELECT momentum_checked FROM positions').fetchone()[0],0)
  assess_first_full_bar(self.db,pd.Timestamp('2026-10-14T00:45:00Z'),bar)
  p=self.db.execute('SELECT * FROM positions').fetchone();self.assertEqual(p['momentum_checked'],1);self.assertEqual(p['be_pending'],1)
 def test_be_never_widens(self):
  self.assertTrue(paper_open(self.db,self.sig,'H1','weak_be',103,103.2,'2026-10-14T00:15:00+00:00',self.info)[0]);p=self.db.execute('SELECT * FROM positions').fetchone();initial=p['stop']
  self.db.execute('UPDATE positions SET be_pending=1');manage_quotes(self.db,105.0,105.2)
  p=self.db.execute('SELECT * FROM positions').fetchone();self.assertGreaterEqual(p['stop'],initial)
if __name__=='__main__':unittest.main()
