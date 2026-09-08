"""Gera um arquivo binario grande e deterministico para os testes.

    python gerar_arquivo.py arquivos/grande.bin 12

Deterministico de proposito (semente fixa): rodando duas vezes sai exatamente o
mesmo arquivo, entao o SHA-256 impresso aqui pode ser comparado com o do arquivo
baixado pelo cliente.
"""

import argparse
import hashlib
import random
from pathlib import Path

BLOCO = 64 * 1024
SEMENTE = 20260908


def gerar(caminho: Path, megabytes: int) -> str:
    total = megabytes * 1024 * 1024
    rng = random.Random(SEMENTE)
    sha = hashlib.sha256()

    caminho.parent.mkdir(parents=True, exist_ok=True)
    escritos = 0
    with caminho.open("wb") as f:
        while escritos < total:
            n = min(BLOCO, total - escritos)
            bloco = rng.randbytes(n)
            f.write(bloco)
            sha.update(bloco)
            escritos += n

    return sha.hexdigest()


def main():
    p = argparse.ArgumentParser(description="gera arquivo binario de teste")
    p.add_argument("caminho", type=Path)
    p.add_argument("megabytes", type=int, nargs="?", default=12)
    args = p.parse_args()

    if args.megabytes <= 0:
        p.error("megabytes deve ser positivo")

    digest = gerar(args.caminho, args.megabytes)
    tamanho = args.caminho.stat().st_size

    print(f"{args.caminho}: {tamanho} bytes ({args.megabytes} MB)")
    print(f"sha256: {digest}")


if __name__ == "__main__":
    main()
