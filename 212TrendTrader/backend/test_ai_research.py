import copy
import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from ai_research_data import clean_bars,connect,save
from ai_train_candidate import examples

START=int(datetime(2026,10,29,13,30,tzinfo=timezone.utc).timestamp())

class ArchiveTests(unittest.TestCase):
    def test_filters_partial_invalid_and_outside_session_bars(self):
        chart={"meta":{"currency":"USD"},"timestamp":[START,START+300,START+600,START+23400],
               "indicators":{"quote":[{"open":[100]*4,"high":[101]*4,"low":[99]*4,
                                      "close":[100,float("nan"),100,100],"volume":[10]*4}]}}
        rows=clean_bars(chart,START+650)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][0],START)

    def test_deduplication_and_revision_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            db=connect(Path(folder)/"bars.sqlite")
            row=(START,"2026-10-29",100.,101.,99.,100.,1000.)
            save(db,"AAPL",[row],1)
            save(db,"AAPL",[row],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bars").fetchone()[0],1)
            changed=(*row[:5],100.5,row[6])
            save(db,"AAPL",[changed],3)
            self.assertEqual(db.execute("SELECT revisions FROM bars").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0],1)
            db.close()

    def sample_db(self):
        db=connect(":memory:")
        rows=[(START+i*300,"2026-10-29",100.,100.1,99.9,100.,10000.) for i in range(78)]
        save(db,"AAPL",rows,1)
        return db

    def test_features_use_only_history_and_timeout_label(self):
        db=self.sample_db()
        before=examples(db)
        self.assertTrue(before)
        self.assertTrue(all(r[3]==1 for r in before))
        self.assertLess(before[0][4],0) # flat raw prices still incur costs
        db.execute("UPDATE bars SET high=110 WHERE stamp=?",(START+21*300,))
        after=examples(db)
        self.assertEqual(before[0][2],after[0][2])
        self.assertEqual(after[0][3],2)
        db.close()

    def test_ambiguous_bar_and_missing_bar_are_excluded(self):
        db=self.sample_db()
        original=len(examples(db))
        db.execute("UPDATE bars SET high=110,low=90 WHERE stamp=?",(START+21*300,))
        self.assertLess(len(examples(db)),original)
        db=self.sample_db()
        db.execute("DELETE FROM bars WHERE stamp=?",(START+21*300,))
        self.assertLess(len(examples(db)),original)

if __name__=="__main__":
    unittest.main()
