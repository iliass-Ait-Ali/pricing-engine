# AI Pricing & Revenue Optimization Engine
PY ?= python

.PHONY: help install download data validate features eda elasticity train evaluate \
        price-response optimize backtest monitor demo test lint api dashboard smoke all clean \
        estimate-elasticity compare-response audit-zero-price audit-cost audit \
        audit-constraints audit-model-value audit-guardrails audit-shrinkage \
        audit-inference audit-out-of-time audit-stability audit-funnel \
        audit-decision-states audit-claims review-list benchmark-scale

help:
	@echo "install        install the package (editable) + dev extras"
	@echo "download       download the official Dominick's Cereals files"
	@echo "data           download + build the canonical parquet + data audit"
	@echo "validate       re-validate the canonical dataset"
	@echo "features       build the modelling feature table"
	@echo "eda            pricing EDA report + figures"
	@echo "elasticity     elasticity analysis report (Phase D)"
	@echo "estimate-elasticity  fit the pricing elasticity table (training weeks only)"
	@echo "train          train + compare models, save the selected artifact"
	@echo "evaluate       re-evaluate the saved artifact on the test window"
	@echo "price-response price-response validation of the native ML response (Phase F gate)"
	@echo "compare-response  compare ml / pooled / shrunk price responses + sensitivity"
	@echo "optimize       batch price recommendations"
	@echo "backtest       offline policy comparison"
	@echo "monitor        drift / schema / performance checks + local threshold alerts + history"
	@echo "review-list    list pending recommendations in the review/approval queue"
	@echo "audit-zero-price  deep audit of the price = 0 exclusion"
	@echo "audit-cost     prove decision-time cost is leakage-safe"
	@echo "audit          Phase M scientific audit (reports 11-20)"
	@echo "demo           end-to-end demo on one real UPC x store"
	@echo "benchmark-batch  loop vs vectorised batch benchmark + equivalence check"
	@echo "benchmark-scale  batch throughput/memory sweep across replicated context counts"
	@echo "readme-metrics regenerate the README generated-metrics block"
	@echo "check-readme   fail if the README metrics are stale"
	@echo "report         rebuild the DOCX edition of the full report"
	@echo "pdf            export the PDF edition from the DOCX"
	@echo "test           run the test suite"
	@echo "lint           ruff"
	@echo "api            run the FastAPI service"
	@echo "dashboard      run the Streamlit dashboard"
	@echo "all            full pipeline from raw data to reports"

install:
	$(PY) -m pip install -e ".[api,dashboard,dev]"

download:
	$(PY) scripts/download_dominicks.py

data: download
	$(PY) scripts/build_dataset.py

validate:
	$(PY) scripts/validate_data.py

features:
	$(PY) scripts/build_features.py

eda:
	$(PY) scripts/run_eda.py

elasticity:
	$(PY) scripts/run_elasticity.py

estimate-elasticity:
	$(PY) scripts/estimate_elasticity.py

train:
	$(PY) scripts/train.py

evaluate:
	$(PY) scripts/evaluate.py

price-response:
	$(PY) scripts/price_response.py

compare-response:
	$(PY) scripts/compare_price_response.py --n-contexts 300

optimize:
	$(PY) scripts/optimize.py --batch 3000 --profile standard

backtest:
	$(PY) scripts/backtest.py --weeks 10 --contexts-per-week 400

monitor:
	$(PY) scripts/monitor.py

review-list:
	$(PY) scripts/review.py --list

audit-zero-price:
	$(PY) scripts/audit_zero_price.py

audit-cost:
	$(PY) scripts/audit_cost_leakage.py

# --- Phase M: final scientific audit ---------------------------------------
audit-constraints:
	$(PY) scripts/audit_constraints.py

audit-model-value:
	$(PY) scripts/audit_model_value.py

audit-guardrails:
	$(PY) scripts/audit_guardrails.py

audit-shrinkage:
	$(PY) scripts/audit_shrinkage.py

audit-inference:
	$(PY) scripts/audit_elasticity_inference.py

audit-out-of-time:
	$(PY) scripts/audit_out_of_time_response.py

audit-stability:
	$(PY) scripts/audit_elasticity_stability.py

audit-funnel:
	$(PY) scripts/audit_eligibility_funnel.py

audit-decision-states:
	$(PY) scripts/audit_decision_states.py

audit-claims:
	$(PY) scripts/audit_claims.py --strict

audit: audit-zero-price audit-cost audit-constraints audit-model-value \
       audit-guardrails audit-shrinkage audit-inference audit-out-of-time \
       audit-stability audit-funnel audit-decision-states audit-claims
	@echo "Phase M scientific audit complete - see reports/11..20"

demo:
	$(PY) scripts/run_demo.py

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .

readme-metrics:
	$(PY) scripts/update_readme_metrics.py --refresh-tests

check-readme:
	$(PY) scripts/update_readme_metrics.py --check

benchmark-batch:
	$(PY) scripts/benchmark_batch.py --contexts 3000

benchmark-scale:
	$(PY) scripts/benchmark_scale.py --sizes 500,3000,10000,30000,100000

smoke:
	$(PY) scripts/smoke_dashboard.py

api:
	$(PY) -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload

dashboard:
	$(PY) -m streamlit run dashboard/app.py

all: data validate features eda elasticity train evaluate price-response \
     estimate-elasticity compare-response optimize backtest monitor \
     audit-zero-price audit-cost audit
	@echo "pipeline complete - see reports/"

clean:
	rm -rf .pytest_cache .ruff_cache artifacts/figures/*.png

# --- v1.1 -------------------------------------------------------------------
value:
	$(PY) scripts/size_value.py

roles:
	$(PY) scripts/segment_roles.py

audit-risk:
	$(PY) scripts/audit_risk_calibration.py

ground-truth:
	$(PY) scripts/ground_truth_study.py

demo-build:
	$(PY) scripts/make_demo.py

eval-copilot:
	$(PY) scripts/eval_copilot.py --mode replay

v11: value roles audit-risk ground-truth
	@echo "v1.1 analyses complete - see reports/23..26"
