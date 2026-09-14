.PHONY: ci test deploy

ci:
	bash ci/run.sh

test:
	bash ci/run.sh

deploy:
	bash ci/deploy-local.sh
