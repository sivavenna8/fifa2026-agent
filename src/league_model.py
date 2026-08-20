from __future__ import annotations

import pickle
import os
import math
from pathlib import Path
from typing import Any

CLASSES = ("H", "D", "A")


def chronological_split(rows: list[dict[str, Any]], train_fraction: float=.70, validation_fraction: float=.15) -> tuple[list, list, list]:
    usable=sorted((r for r in rows if r.get("result")),key=lambda r:(r.get("kickoff") or "",r.get("match_id") or ""))
    n=len(usable); train_end=int(n*train_fraction); validation_end=int(n*(train_fraction+validation_fraction))
    return usable[:train_end],usable[train_end:validation_end],usable[validation_end:]


def _metrics(model: Any, rows: list[dict[str, Any]]) -> dict[str,float]:
    from sklearn.metrics import accuracy_score
    if not rows: raise ValueError("Evaluation split is empty")
    truth=[r["result"] for r in rows]; raw=model.predict_proba([r["features"] for r in rows]); labels=list(model.classes_)
    aligned=[[float(row[labels.index(c)]) for c in CLASSES] for row in raw]
    picks=[CLASSES[max(range(3),key=lambda i:p[i])] for p in aligned]
    brier=sum(sum((p[i]-(1 if y==CLASSES[i] else 0))**2 for i in range(3)) for p,y in zip(aligned,truth))/len(truth)
    multiclass_log_loss=-sum(math.log(max(1e-15,p[CLASSES.index(y)])) for p,y in zip(aligned,truth))/len(truth)
    return {"accuracy":float(accuracy_score(truth,picks)),"log_loss":float(multiclass_log_loss),"brier":float(brier),"samples":len(rows)}


class LeagueModel:
    def __init__(self): self.pipeline: Any=None; self.model_name="untrained"; self.metadata: dict[str,Any]={}

    @staticmethod
    def _candidates() -> dict[str,Any]:
        # Avoid joblib's restricted Windows CPU-probe subprocess; both models
        # train deterministically in-process for this small dataset.
        os.environ.setdefault("LOKY_MAX_CPU_COUNT","1")
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        return {"logistic-regression":make_pipeline(StandardScaler(),LogisticRegression(max_iter=1500,class_weight="balanced")),"hist-gradient-boosting":HistGradientBoostingClassifier(max_iter=180,learning_rate=.06,l2_regularization=1.0,random_state=42)}

    def train(self, rows: list[dict[str,Any]]) -> dict[str,Any]:
        usable=[r for r in rows if r.get("result")]
        if len(usable)<60 or len({r["result"] for r in usable})<3: raise ValueError("Need at least 60 completed matches spanning home wins, draws and away wins")
        train,validation,test=chronological_split(usable)
        if any(len({r["result"] for r in split})<3 for split in (train,validation,test)): raise ValueError("Every chronological split must contain home wins, draws and away wins")
        comparison={}; candidates=self._candidates()
        for name,model in candidates.items():
            model.fit([r["features"] for r in train],[r["result"] for r in train])
            comparison[name]={"validation":_metrics(model,validation),"test":_metrics(model,test)}
        selected=min(comparison,key=lambda name:comparison[name]["validation"]["log_loss"])
        production=self._candidates()[selected]; production.fit([r["features"] for r in train+validation],[r["result"] for r in train+validation])
        self.pipeline=production; self.model_name=selected
        distribution={c:sum(r["result"]==c for r in usable) for c in CLASSES}; seasons=sorted({str(r.get("season")) for r in usable if r.get("season")})
        self.metadata={"selected_model":selected,"training_seasons":seasons,"samples":len(usable),"train_size":len(train),"validation_size":len(validation),"test_size":len(test),"class_distribution":distribution,"models":comparison}
        return self.metadata

    def predict(self,features:list[float])->dict[str,float]:
        if self.pipeline is None: raise ValueError("Model is not trained")
        raw=self.pipeline.predict_proba([features])[0]; labels=list(self.pipeline.classes_); probs={c:float(raw[labels.index(c)]) for c in CLASSES}; total=sum(probs.values())
        return {c:probs[c]/total for c in CLASSES}

    def save(self,path:Path)->None:
        path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(pickle.dumps({"pipeline":self.pipeline,"model_name":self.model_name,"metadata":self.metadata}))

    @classmethod
    def load(cls,path:Path)->"LeagueModel":
        payload=pickle.loads(path.read_bytes()); model=cls()
        if isinstance(payload,dict): model.pipeline=payload["pipeline"]; model.model_name=payload.get("model_name","unknown"); model.metadata=payload.get("metadata",{})
        else: model.pipeline=payload; model.model_name="legacy-logistic-regression"
        return model
