# Atalho opcional para quem roda no shell MSYS2/Git Bash (onde existe `make`).
# So delega para tarefas.py — a logica de verdade esta la, e funciona igual no
# PowerShell/CMD com `python tarefas.py <alvo>`.
#
#   make demo          make demo PERDA=0.05          make duplo
#   make servidor      make cliente ARQ=pequeno.bin  make setup   make limpar

PY   ?= python
ARQ  ?= grande.bin

.PHONY: demo duplo setup servidor cliente limpar

demo:
	$(PY) tarefas.py demo $(ARQ) $(if $(PERDA),--perda $(PERDA)) $(if $(DESCARTAR),--descartar $(DESCARTAR))

duplo:
	$(PY) tarefas.py duplo

setup:
	$(PY) tarefas.py setup

servidor:
	$(PY) tarefas.py servidor

cliente:
	$(PY) tarefas.py cliente $(ARQ)

limpar:
	$(PY) tarefas.py limpar
