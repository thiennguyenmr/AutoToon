SHELL := /bin/bash
COMPOSE := docker compose

# Max parallel jobs khi build/start. Override: `make up JOBS=1`
JOBS ?= 4

# Group dịch vụ theo độ nặng để start staged
INFRA   := postgres minio
LIGHT   := chromium openclaw-gateway n8n-runner
APP     := n8n
HEAVY   := vllm comfyui omnivoice

.PHONY: help up up-staged up-infra up-light up-app up-heavy down restart ps logs build pull stop clean swap-clear status

help:
	@echo "Targets:"
	@echo "  up           Up tất cả với --parallel \$$JOBS (default $(JOBS))"
	@echo "  up-staged    Up theo từng tầng (infra → light → app → heavy)"
	@echo "  up-infra     Chỉ postgres + minio"
	@echo "  up-light     chromium + openclaw + n8n-runner"
	@echo "  up-app       n8n"
	@echo "  up-heavy     vllm + comfyui (mỗi cái tuần tự)"
	@echo "  down         Stop + remove containers"
	@echo "  stop         Stop only (giữ containers)"
	@echo "  restart      Restart all"
	@echo "  ps           Trạng thái services"
	@echo "  logs S=name  Tail logs 1 service (vd: make logs S=vllm)"
	@echo "  build        Build images với --parallel \$$JOBS"
	@echo "  pull         Pull images"
	@echo "  status       GPU + RAM + container size"
	@echo "  swap-clear   Xoá swap (cần sudo)"
	@echo "  clean        Down + xoá orphan + prune build cache"
	@echo ""
	@echo "Vars: JOBS=N (default $(JOBS))   S=service"

up:
	$(COMPOSE) --parallel $(JOBS) up -d --no-build

up-staged: up-infra up-light up-app up-heavy
	@echo "[done] all services up"

up-infra:
	@echo "[1/4] infra: $(INFRA)"
	$(COMPOSE) --parallel $(JOBS) up -d --wait $(INFRA)

up-light: up-infra
	@echo "[2/4] light: $(LIGHT)"
	$(COMPOSE) --parallel $(JOBS) up -d $(LIGHT)

up-app: up-light
	@echo "[3/4] app: $(APP)"
	$(COMPOSE) up -d --wait $(APP)

up-heavy:
	@echo "[4/4] heavy: serial start để không tràn RAM"
	@for svc in $(HEAVY); do \
		echo "  → starting $$svc"; \
		$(COMPOSE) up -d --wait-timeout 600 $$svc || $(COMPOSE) up -d $$svc; \
		sleep 5; \
	done

down:
	$(COMPOSE) down --remove-orphans

stop:
	$(COMPOSE) stop

restart:
	$(COMPOSE) restart

ps:
	$(COMPOSE) ps --format "table {{.Service}}\t{{.Status}}\t{{.Ports}}"

logs:
	@if [ -z "$(S)" ]; then echo "Usage: make logs S=<service>"; exit 1; fi
	$(COMPOSE) logs -f --tail=100 $(S)

build:
	$(COMPOSE) --parallel $(JOBS) build --progress plain $(S)

pull:
	$(COMPOSE) --parallel $(JOBS) pull

status:
	@echo "=== GPU summary ===" ; nvidia-smi --query-gpu=index,name,memory.used,memory.free,utilization.gpu,utilization.memory,temperature.gpu,power.draw --format=csv 2>/dev/null || echo "no nvidia"
	@echo ""
	@echo "=== GPU per-process ===" ; { printf "GPU\tPID\tPROCESS\tMEM\tGPU_FREE\tGPU_TOTAL\n"; nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory --format=csv,noheader 2>/dev/null | awk -F', ' '{cmd="nvidia-smi --query-gpu=index,uuid,memory.free,memory.total --format=csv,noheader | awk -F\", \" -v u="$$1" \"{if(\\$$2==u){print \\$$1\\\"|\\\"\\$$3\\\"|\\\"\\$$4}}\""; cmd | getline info; close(cmd); n = split(info, a, "|"); printf "%s\t%s\t%s\t%s\t%s\t%s\n", a[1], $$2, $$3, $$4, a[2], a[3]}'; } | column -t -s $$'\t'
	@echo ""
	@echo "=== RAM ===" ; free -h | head -3
	@echo ""
	@echo "=== Swap ===" ; free -h | tail -1
	@echo ""
	@echo "=== Containers ===" ; docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}" 2>/dev/null | head -15

swap-clear:
	@echo "cần sudo, sẽ chậm vài giây tùy lượng swap đang dùng"
	sudo swapoff -a && sudo swapon -a
	@free -h | tail -1

clean:
	$(COMPOSE) down --remove-orphans
	docker builder prune -f
	docker image prune -f
