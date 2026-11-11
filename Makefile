# Shortcuts for the local stack (docs/local/RUNBOOK.md). Each target runs a script in scripts/local/.
.PHONY: up up-image api api-record api-replay web web-dev smoke eval-http down wipe status logs

up:        ## start the stand-ins and the edge; then `make api`
	scripts/local/up.sh
up-image:  ## the same, plus the API image Lambda runs
	scripts/local/up.sh --image
api:       ## the API natively, with reload
	scripts/local/api.sh
api-record: ## the same, saving every model call to .local/llm-recordings
	LLM_MODE=record scripts/local/api.sh
api-replay: ## the same, answering model calls from the recordings (no OpenRouter, no cost)
	LLM_MODE=replay scripts/local/api.sh
web:       ## build the apps with the local settings (served at :8088)
	scripts/local/web.sh
web-dev:   ## the apps' Vite dev servers with the local settings (:5173)
	scripts/local/web.sh --dev
smoke:     ## the end-to-end smoke test (the API in replay mode: no cost)
	scripts/local/smoke.sh
eval-http: ## golden items through the API over HTTP (replay: no cost once recorded)
	scripts/local/eval-http.sh --sample 30
down:      ## stop the stack, keeping its state
	scripts/local/down.sh
wipe:      ## stop the stack and drop its state
	scripts/local/down.sh --wipe
status:    ## what is up
	scripts/local/status.sh
logs:      ## follow the containers' logs
	scripts/local/logs.sh
