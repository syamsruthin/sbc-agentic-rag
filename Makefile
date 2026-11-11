# Shortcuts for the local stack (docs/local/RUNBOOK.md). Each target runs a script in scripts/local/.
.PHONY: up up-image api down wipe status logs

up:        ## start the stand-ins and the edge; then `make api`
	scripts/local/up.sh
up-image:  ## the same, plus the API image Lambda runs
	scripts/local/up.sh --image
api:       ## the API natively, with reload
	scripts/local/api.sh
down:      ## stop the stack, keeping its state
	scripts/local/down.sh
wipe:      ## stop the stack and drop its state
	scripts/local/down.sh --wipe
status:    ## what is up
	scripts/local/status.sh
logs:      ## follow the containers' logs
	scripts/local/logs.sh
