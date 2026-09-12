"""Atalhos do projeto — o "Makefile" deste trabalho, em Python puro.

Funciona em qualquer terminal (PowerShell, CMD, MSYS2), sem depender do `make`.
Usa o mesmo interpretador que o chamou (sys.executable), entao respeita venv.

    python tarefas.py demo               # sobe servidor + cliente sozinho (12 MB)
    python tarefas.py demo --perda 0.05  # o mesmo, com 5% de perda simulada
    python tarefas.py demo --descartar 10,11,5000
    python tarefas.py setup              # gera os arquivos de teste
    python tarefas.py servidor           # so o servidor (fica em primeiro plano)
    python tarefas.py cliente grande.bin # so um cliente
    python tarefas.py duplo              # dois clientes simultaneos
    python tarefas.py limpar             # apaga downloads/ e __pycache__

Opcoes gerais: --porta 5000  --modo janela|sw
"""

import argparse
import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
PY = sys.executable                      # mesmo interpretador que rodou este script
PORTA_PADRAO = 5000


def _sha(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 16), b""):
            h.update(bloco)
    return h.hexdigest()


def _gerar(nome: str, mb: int) -> Path:
    destino = RAIZ / "arquivos" / nome
    if destino.exists():
        return destino
    subprocess.run([PY, "gerar_arquivo.py", str(destino), str(mb)], cwd=RAIZ, check=True)
    return destino


# ----------------------------------------------------------------- comandos

def cmd_setup(args):
    _gerar("pequeno.bin", 2)
    _gerar("grande.bin", 12)
    print("arquivos de teste prontos em", RAIZ / "arquivos")


def cmd_servidor(args):
    subprocess.run([PY, "servidor.py", "--porta", str(args.porta),
                    "--raiz", "./arquivos", "--modo", args.modo], cwd=RAIZ)


def cmd_cliente(args):
    alvo = f"@127.0.0.1:{args.porta}/{args.arquivo}"
    cmd = [PY, "cliente.py", alvo, "--saida", "./downloads", "--modo", args.modo]
    if args.perda:
        cmd += ["--perda", str(args.perda)]
    if args.descartar:
        cmd += ["--descartar", args.descartar]
    return subprocess.run(cmd, cwd=RAIZ).returncode


def _subir_servidor(args):
    """Sobe o servidor em segundo plano; devolve o Popen. Os logs vao para o console."""
    p = subprocess.Popen([PY, "servidor.py", "--porta", str(args.porta),
                          "--raiz", "./arquivos", "--modo", args.modo], cwd=RAIZ)
    time.sleep(1.5)                      # espera o bind acontecer
    return p


def _parar(p):
    p.terminate()
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()


def cmd_demo(args):
    """Sobe servidor + cliente num comando so, e confere o SHA-256 no fim."""
    cmd_setup(args)
    origem = RAIZ / "arquivos" / args.arquivo
    destino = RAIZ / "downloads" / Path(args.arquivo).name
    destino.unlink(missing_ok=True)

    print(f"\n== subindo servidor (porta {args.porta}, modo {args.modo}) ==")
    srv = _subir_servidor(args)
    try:
        print(f"== cliente baixando {args.arquivo} ==\n")
        rc = cmd_cliente(args)
    finally:
        _parar(srv)

    print("\n== verificacao ==")
    if rc == 0 and destino.exists() and _sha(destino) == _sha(origem):
        print(f"SUCESSO: {destino.name} identico ao original (SHA-256 confere)")
        return 0
    print("FALHA: o arquivo baixado nao confere")
    return 1


def cmd_duplo(args):
    """Dois clientes simultaneos baixando arquivos diferentes."""
    cmd_setup(args)
    print(f"\n== subindo servidor (porta {args.porta}) ==")
    srv = _subir_servidor(args)
    try:
        print("== dois clientes simultaneos: grande.bin + pequeno.bin ==\n")
        procs = [
            subprocess.Popen([PY, "cliente.py", f"@127.0.0.1:{args.porta}/grande.bin",
                              "--saida", "./downloads"], cwd=RAIZ),
            subprocess.Popen([PY, "cliente.py", f"@127.0.0.1:{args.porta}/pequeno.bin",
                              "--saida", "./downloads"], cwd=RAIZ),
        ]
        rcs = [p.wait() for p in procs]
    finally:
        _parar(srv)

    ok = rcs == [0, 0]
    for nome in ("grande.bin", "pequeno.bin"):
        d, o = RAIZ / "downloads" / nome, RAIZ / "arquivos" / nome
        ok = ok and d.exists() and _sha(d) == _sha(o)
    print("\n== verificacao ==")
    print("SUCESSO: ambos conferem" if ok else "FALHA")
    return 0 if ok else 1


def cmd_limpar(args):
    for f in (RAIZ / "downloads").glob("*"):
        if f.name != ".gitkeep":
            f.unlink()
    shutil.rmtree(RAIZ / "__pycache__", ignore_errors=True)
    print("downloads/ e __pycache__ limpos")


# ----------------------------------------------------------------- CLI

def main():
    p = argparse.ArgumentParser(description="atalhos do Trabalho 1 (UDP)")
    p.add_argument("--porta", type=int, default=PORTA_PADRAO)
    p.add_argument("--modo", choices=("janela", "sw"), default="janela")

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup", help="gera os arquivos de teste")

    sp = sub.add_parser("demo", help="sobe servidor + cliente num comando")
    sp.add_argument("arquivo", nargs="?", default="grande.bin")
    sp.add_argument("--perda", type=float, default=0.0)
    sp.add_argument("--descartar", default="")

    sub.add_parser("servidor", help="so o servidor, em primeiro plano")

    sp = sub.add_parser("cliente", help="so um cliente")
    sp.add_argument("arquivo", nargs="?", default="grande.bin")
    sp.add_argument("--perda", type=float, default=0.0)
    sp.add_argument("--descartar", default="")

    sub.add_parser("duplo", help="dois clientes simultaneos")
    sub.add_parser("limpar", help="apaga downloads/ e __pycache__")

    args = p.parse_args()
    # defaults para comandos que nao declaram essas opcoes
    for attr, valor in (("arquivo", "grande.bin"), ("perda", 0.0), ("descartar", "")):
        if not hasattr(args, attr):
            setattr(args, attr, valor)

    return {
        "setup": cmd_setup, "demo": cmd_demo, "servidor": cmd_servidor,
        "cliente": cmd_cliente, "duplo": cmd_duplo, "limpar": cmd_limpar,
    }[args.cmd](args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
