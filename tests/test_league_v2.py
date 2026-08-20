from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.database import Database
from src.elo import chronological_ratings, update_elo
from src.league_config import get_league
from src.league_features import build_feature_rows
from src.league_model import LeagueModel
from src.league_model import chronological_split
from src.league_api import LeagueAPIClient

ROOT = Path(__file__).resolve().parent.parent


def match(mid, day, home, away, hs=None, aws=None, status="completed"):
    return {"id":str(mid),"league_code":"PL","season":"2025","matchday":day,"kickoff":f"2025-01-{day:02d}T15:00:00Z","status":status,"home_team_id":home,"home_team":home,"away_team_id":away,"away_team":away,"home_score":hs,"away_score":aws}


class LeagueV2Tests(unittest.TestCase):
    def test_elo_is_zero_sum_and_winner_gains(self):
        home,away=update_elo(1500,1500,2,0)
        self.assertGreater(home,1500); self.assertLess(away,1500); self.assertAlmostEqual(home+away,3000)

    def test_future_result_does_not_change_earlier_features(self):
        first=match(1,1,"A","B",None,None,"scheduled"); future=match(2,2,"A","B",5,0)
        before=build_feature_rows([first])[0]["features"]
        after=build_feature_rows([first,future])[0]["features"]
        self.assertEqual(before,after)

    def test_pre_match_ratings_are_captured_before_result(self):
        games=[match(1,1,"A","B",2,0),match(2,2,"A","B",0,1)]
        _,pre=chronological_ratings(games)
        self.assertEqual(pre["1"],(1500,1500)); self.assertGreater(pre["2"][0],pre["2"][1])

    def test_prediction_is_insert_once_and_evaluates_draw(self):
        handle=tempfile.NamedTemporaryFile(suffix=".db",dir=ROOT / "data",delete=False); handle.close()
        try:
            db=Database(Path(handle.name)); league=get_league("PL")
            kickoff=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat(); m=match(1,1,"A","B",None,None,"scheduled"); m["kickoff"]=kickoff
            db.upsert_league_matches(league,[m]); p={"H":.2,"D":.6,"A":.2}
            self.assertTrue(db.lock_league_prediction("1","PL",p,[0.0])); self.assertFalse(db.lock_league_prediction("1","PL",{"H":.9,"D":.05,"A":.05},[9.0]))
            m.update(status="completed",home_score=1,away_score=1); db.upsert_league_matches(league,[m])
            self.assertEqual(db.evaluate_league_predictions("PL"),1); self.assertEqual(db.league_metrics("PL")["correct"],1)
        finally:
            Path(handle.name).unlink(missing_ok=True)

    def test_model_outputs_three_probabilities(self):
        rows=[]
        for i in range(90):
            result=("H","D","A")[i%3]; rows.append({"features":[float(i%3),float(i),0,0,0,0,0,0,0,0,0],"result":result})
        model=LeagueModel(); metrics=model.train(rows); probs=model.predict([1,31,0,0,0,0,0,0,0,0,0])
        self.assertEqual(set(probs),{"H","D","A"}); self.assertAlmostEqual(sum(probs.values()),1.0); self.assertIn("log_loss",metrics["models"][metrics["selected_model"]]["test"])

    def test_historical_api_parsing_and_season(self):
        raw={"id":99,"season":{"startDate":"2024-08-01"},"matchday":2,"utcDate":"2024-08-20T19:00:00Z","status":"FINISHED","homeTeam":{"id":1,"name":"A"},"awayTeam":{"id":2,"name":"B"},"score":{"winner":"HOME_TEAM","fullTime":{"home":2,"away":1}}}
        parsed=LeagueAPIClient._match("PL",raw)
        self.assertEqual(parsed["season"],"2024"); self.assertEqual(parsed["winner"],"HOME_TEAM"); self.assertEqual(parsed["status"],"completed")

    def test_chronological_three_way_split(self):
        rows=[{"match_id":str(i),"kickoff":f"2024-01-{i+1:02d}","result":("H","D","A")[i%3]} for i in range(20)]
        train,validation,test=chronological_split(rows)
        self.assertEqual((len(train),len(validation),len(test)),(14,3,3)); self.assertLess(train[-1]["kickoff"],validation[0]["kickoff"]); self.assertLess(validation[-1]["kickoff"],test[0]["kickoff"])

    def test_cross_season_form_and_promoted_fallback(self):
        prior=match(1,1,"A","B",3,0); prior["season"]="2024"; prior["kickoff"]="2025-05-20T15:00:00Z"
        target=match(2,2,"A","Promoted",None,None,"scheduled"); target["season"]="2025"; target["kickoff"]="2025-08-20T15:00:00Z"
        row=build_feature_rows([prior,target])[1]
        self.assertEqual(row["features"][1],3.0)  # A carries its prior-season win
        self.assertEqual(row["features"][2],1.0)  # promoted club gets neutral PPG

    def test_duplicate_upsert_and_live_metrics_separation(self):
        handle=tempfile.NamedTemporaryFile(suffix=".db",dir=ROOT / "data",delete=False); handle.close()
        try:
            db=Database(Path(handle.name)); league=get_league("PL"); m=match(77,1,"A","B",2,1)
            self.assertEqual(db.upsert_league_matches(league,[m]),(1,0)); self.assertEqual(db.upsert_league_matches(league,[m]),(0,1)); self.assertEqual(len(db.league_matches("PL")),1)
            with db.connect() as c: c.execute("INSERT INTO model_metrics(league_code,model_name,created_at,split_type,metrics_json) VALUES('PL','test','now','backtest','{}')")
            self.assertEqual(db.league_metrics("PL")["total"],0)
        finally: Path(handle.name).unlink(missing_ok=True)

    def test_provisional_refreshes_but_locked_does_not(self):
        handle=tempfile.NamedTemporaryFile(suffix=".db",dir=ROOT / "data",delete=False); handle.close()
        try:
            db=Database(Path(handle.name)); league=get_league("PL"); now=datetime(2026,1,1,tzinfo=timezone.utc)
            far=match(88,1,"A","B",None,None,"scheduled"); far["kickoff"]=(now+timedelta(days=10)).isoformat(); db.upsert_league_matches(league,[far])
            self.assertEqual(db.save_league_prediction("88","PL",{"H":.4,"D":.3,"A":.3},[1.0],now=now),"provisional")
            self.assertEqual(db.save_league_prediction("88","PL",{"H":.2,"D":.3,"A":.5},[9.0],now=now+timedelta(days=1)),"provisional")
            refreshed=db.rows("SELECT * FROM league_predictions WHERE match_id='88'")[0]
            self.assertAlmostEqual(refreshed["away_probability"],.5); self.assertEqual(refreshed["feature_json"],"[9.0]")
            self.assertEqual(db.save_league_prediction("88","PL",{"H":.7,"D":.2,"A":.1},[10.0],now=now+timedelta(days=8)),"locked")
            locked=db.rows("SELECT * FROM league_predictions WHERE match_id='88'")[0]
            self.assertIsNone(db.save_league_prediction("88","PL",{"H":.1,"D":.1,"A":.8},[99.0],now=now+timedelta(days=8,hours=1)))
            unchanged=db.rows("SELECT * FROM league_predictions WHERE match_id='88'")[0]
            self.assertEqual((locked["home_probability"],locked["feature_json"]),(unchanged["home_probability"],unchanged["feature_json"]))
        finally: Path(handle.name).unlink(missing_ok=True)

    def test_after_kickoff_is_rejected_and_provisional_not_scored(self):
        handle=tempfile.NamedTemporaryFile(suffix=".db",dir=ROOT / "data",delete=False); handle.close()
        try:
            db=Database(Path(handle.name)); league=get_league("PL"); now=datetime(2026,1,10,tzinfo=timezone.utc)
            past=match(89,1,"A","B",None,None,"scheduled"); past["kickoff"]=(now-timedelta(hours=1)).isoformat(); db.upsert_league_matches(league,[past])
            self.assertIsNone(db.save_league_prediction("89","PL",{"H":.5,"D":.3,"A":.2},[1],now=now))
            future=match(90,2,"A","B",None,None,"scheduled"); future["kickoff"]=(now+timedelta(days=5)).isoformat(); db.upsert_league_matches(league,[future])
            self.assertEqual(db.save_league_prediction("90","PL",{"H":.5,"D":.3,"A":.2},[1],now=now),"provisional")
            future.update(status="completed",home_score=2,away_score=0); db.upsert_league_matches(league,[future])
            self.assertEqual(db.evaluate_league_predictions("PL"),0); self.assertEqual(db.league_metrics("PL")["total"],0)
        finally: Path(handle.name).unlink(missing_ok=True)


if __name__ == "__main__": unittest.main()
