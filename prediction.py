"""Loads the trained model once and suggests a category and priority for new tickets."""
from pathlib import Path
import joblib

MODEL_FILE = "models/ticket_model.joblib"
MIN_CONFIDENCE = 0.5   # below this, the suggestion is NOT applied to the ticket

# Load once when the app starts. (Only ever load model files you trained yourself.)
bundle = joblib.load(MODEL_FILE) if Path(MODEL_FILE).is_file() else None

def predict(text):
    result = {"category": None, "priority": None,
              "category_score": None, "priority_score": None, "model_version": "no-model"}
    if bundle is None:                       # no model trained yet: everything goes to manual review
        return result
    result["model_version"] = bundle["version"]
    for target in ["category", "priority"]:
        model = bundle[target]
        probabilities = model.predict_proba([text])[0]   # one probability per label, adding up to 1
        best = probabilities.argmax()                    # position of the most likely label
        result[target] = str(model.classes_[best])
        result[target + "_score"] = float(probabilities[best])
    return result