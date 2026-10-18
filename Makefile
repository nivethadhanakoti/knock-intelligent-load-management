PY ?= python3
.PHONY: setup dev test demo train twist global

setup:            ## install dependencies, create local users, train and sign the model for this machine
	$(PY) -m pip install -r requirements.txt
	$(PY) scripts/ensure_model.py
	$(PY) scripts/manage_users.py init

dev:              ## run API + console at http://localhost:8000
	set -a; [ -f .env ] && . ./.env; set +a; $(PY) -m uvicorn app.main:app --app-dir backend --port 8000 --reload

test:             ## run the test suite (trains the model first if this machine has none)
	KNOCK_TRAIN_SEEDS=12 $(PY) scripts/ensure_model.py
	cd backend && $(PY) -m pytest -q tests

demo:             ## headless comparison table: KNOCK + AI vs core KNOCK vs the usual way
	$(PY) scripts/demo.py

train:            ## retrain the fault classifier from simulated fleet history (~2 min)
	$(PY) scripts/train_models.py 30

global:           ## recompute the five-region comparison shown on the Around the world tab (~3 min)
	$(PY) scripts/global_results.py

twist:            ## scaffold a live twist: make twist NAME=substation_fire
	$(PY) scripts/new_twist.py $(NAME)
