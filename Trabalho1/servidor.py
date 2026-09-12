"""Servidor UDP — Trabalho 1 (ICSR30/UTFPR).

    python servidor.py --porta 5000 --raiz ./arquivos

Modelo estilo TFTP: escuta na porta conhecida so para receber REQ_GET; cada
transferencia aceita ganha uma thread com socket UDP proprio em porta efemera.
O cliente descobre a porta nova pelo endereco de origem do primeiro META.
Ver docs/REDES.md secao 5.
"""

import argparse
import hashlib
import logging
import math
import random
import threading
import time
from pathlib import Path

import protocolo as proto
import rede

# O logging da stdlib ja e thread-safe: as linhas de duas sessoes simultaneas nao
# saem intercaladas no meio da palavra. No video isso e o que mantem o terminal legivel.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(threadName)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("servidor")

# Limite de transferencias simultaneas. Alem disso o servidor responde SERVER_BUSY,
# em vez de aceitar sessoes sem limite.
MAX_SESSOES = 10

# Pausa a cada janela enviada, para o servidor nao produzir mais rapido do que o
# cliente drena e estourar o buffer do kernel dele (docs/REDES.md secao 9).
PAUSA_JANELA = 0.0005

# Controle de concorrencia e de-duplicacao de REQ_GET reenviado pelo mesmo cliente.
_ativos = set()                 # enderecos de clientes com sessao em andamento
_lock = threading.Lock()


# ----------------------------------------------------------------- sanitizacao

def resolver(raiz: Path, nome: str):
    """Traduz o nome pedido pelo cliente num caminho seguro dentro de `raiz`.

    -> (Path, None) se ok, ou (None, CodigoErro) se recusado.
    ALERTA DE SEGURANCA do enunciado. Ver docs/REDES.md secao 13.
    """
    if not nome or "\x00" in nome:
        return None, proto.CodigoErro.BAD_REQUEST

    # Recusa qualquer separador ou travessia antes mesmo de tocar no sistema de arquivos.
    if ".." in nome or "/" in nome or "\\" in nome or ":" in nome:
        return None, proto.CodigoErro.ACCESS_DENIED

    raiz = raiz.resolve()
    caminho = (raiz / nome).resolve()          # resolve ".", ".." e symlinks

    # is_relative_to, e NAO str.startswith: "/srv/arquivos-secretos" comecaria com
    # o prefixo "/srv/arquivos" e passaria numa checagem ingenua de string.
    if not caminho.is_relative_to(raiz):
        return None, proto.CodigoErro.ACCESS_DENIED
    if not caminho.is_file():                   # bloqueia diretorio e symlink pendurado
        return None, proto.CodigoErro.FILE_NOT_FOUND

    return caminho, None


def _resumo_arquivo(caminho: Path):
    """-> (tamanho, total_segmentos, sha256_bytes), lendo o arquivo em blocos."""
    tamanho = caminho.stat().st_size
    total = math.ceil(tamanho / proto.TAM_PAYLOAD)
    sha = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 16), b""):
            sha.update(bloco)
    return tamanho, total, sha.digest()


# ----------------------------------------------------------------- sessao

def _enviar_segmentos(sock, cliente, sessao, seqs, total, ler_seg):
    """Envia (ou retransmite) a lista de seq, com pausa periodica anti-estouro."""
    for i, seq in enumerate(seqs):
        flags = proto.FLAG_ULTIMO if seq == total - 1 else 0
        sock.enviar(proto.Tipo.DATA, cliente, sessao=sessao, seq=seq,
                    payload=ler_seg(seq), flags=flags)
        if (i + 1) % proto.JANELA == 0:
            time.sleep(PAUSA_JANELA)


def atender(cliente, caminho: Path, sessao: int, modo: str):
    """Uma transferencia completa, na sua propria thread e socket."""
    try:
        tamanho, total, sha = _resumo_arquivo(caminho)
        log.info("%s: %d bytes, %d segmentos, sessao=0x%08X",
                 caminho.name, tamanho, total, sessao)

        with rede.SocketUDP(timeout=proto.TIMEOUT) as sock, caminho.open("rb") as f:

            def ler_seg(seq):
                f.seek(seq * proto.TAM_PAYLOAD)
                return f.read(proto.TAM_PAYLOAD)

            meta = proto.montar_meta(tamanho, total, proto.TAM_PAYLOAD, sha)
            sock.enviar(proto.Tipo.META, cliente, sessao=sessao, payload=meta)

            retransmitidos = 0

            if modo == "sw":
                # Stop-and-Wait: um segmento por vez, so avanca com ACK daquele seq.
                for seq in range(total):
                    for tentativa in range(1, proto.MAX_TENTATIVAS + 1):
                        _enviar_segmentos(sock, cliente, sessao, (seq,), total, ler_seg)
                        try:
                            cab, payload, _ = sock.esperar(
                                (proto.Tipo.ACK, proto.Tipo.NACK, proto.Tipo.FIN_ACK),
                                origem_esperada=cliente)
                        except (TimeoutError, ConnectionResetError):
                            retransmitidos += tentativa > 1
                            continue
                        if cab.tipo is proto.Tipo.FIN_ACK:
                            return
                        # ACK do seq atual (ou NACK): em ambos os casos seguimos.
                        if cab.tipo is proto.Tipo.ACK and proto.ler_ack(payload) >= seq:
                            break
                        retransmitidos += 1
                    else:
                        log.warning("cliente parou de responder no seq %d", seq)
                        return
            else:
                # Janela: dispara tudo de uma vez; a recuperacao e dirigida por NACK.
                _enviar_segmentos(sock, cliente, sessao, range(total), total, ler_seg)

            # Fim de transmissao: anuncia FIN e atende NACK ate o cliente confirmar.
            for tentativa in range(1, proto.MAX_TENTATIVAS + 1):
                sock.enviar(proto.Tipo.FIN, cliente, sessao=sessao)
                try:
                    cab, payload, _ = sock.esperar(
                        (proto.Tipo.FIN_ACK, proto.Tipo.NACK),
                        origem_esperada=cliente)
                except (TimeoutError, ConnectionResetError):
                    continue
                if cab.tipo is proto.Tipo.FIN_ACK:
                    extra = f", {retransmitidos} retransmitidos" if retransmitidos else ""
                    log.info("%s: transferencia concluida (sessao=0x%08X%s)",
                             caminho.name, sessao, extra)
                    return
                if cab.tipo is proto.Tipo.NACK:
                    faltantes = proto.ler_nack(payload)
                    retransmitidos += len(faltantes)
                    log.info("retransmitindo %d segmento(s): %s%s", len(faltantes),
                             faltantes[:8], " ..." if len(faltantes) > 8 else "")
                    _enviar_segmentos(sock, cliente, sessao, faltantes, total, ler_seg)
                    tentativa = 0  # progresso: nao conta como tentativa vazia

            log.warning("%s: cliente nao confirmou o fim (sessao=0x%08X)",
                        caminho.name, sessao)
    except Exception:
        log.exception("erro na sessao 0x%08X", sessao)
    finally:
        with _lock:
            _ativos.discard(cliente)


# ----------------------------------------------------------------- laco principal

def servir(porta: int, raiz: Path, modo: str):
    """Escuta REQ_GET e despacha cada pedido para uma thread de sessao."""
    # timeout de 1 s so para o Ctrl+C ser atendido no Windows (recvfrom bloqueante
    # nao e interrompido por sinal la); nao tem relacao com a confiabilidade.
    with rede.SocketUDP(porta=porta, timeout=1.0) as sock:
        while True:
            try:
                recebido = sock.receber()
            except TimeoutError:
                continue                       # volta ao laco; deixa o Ctrl+C passar
            if recebido is None:
                continue

            cab, payload, origem = recebido
            if cab.tipo is not proto.Tipo.REQ_GET:
                continue                       # so REQ_GET nesta porta

            nome = payload.decode("utf-8", errors="replace")

            with _lock:
                if origem in _ativos:
                    continue                   # REQ_GET reenviado; sessao ja existe
                if len(_ativos) >= MAX_SESSOES:
                    sock.enviar(proto.Tipo.ERR, origem,
                                payload=proto.montar_erro(proto.CodigoErro.SERVER_BUSY))
                    log.warning("recusado (ocupado): %s pediu '%s'", origem, nome)
                    continue

                caminho, erro = resolver(raiz, nome)
                if erro is not None:
                    sock.enviar(proto.Tipo.ERR, origem, payload=proto.montar_erro(erro))
                    log.warning("recusado (%s): %s pediu '%s'", erro.name, origem, nome)
                    continue

                _ativos.add(origem)

            sessao = random.getrandbits(32)
            log.info("aceito: %s pediu '%s' -> nova sessao", origem, nome)
            threading.Thread(target=atender, args=(origem, caminho, sessao, modo),
                             name=f"sessao-{sessao & 0xFFFF:04X}", daemon=True).start()


def main():
    p = argparse.ArgumentParser(description="servidor UDP de transferencia de arquivos")
    p.add_argument("--porta", type=int, default=5000)
    p.add_argument("--raiz", type=Path, default=Path("./arquivos"))
    p.add_argument("--modo", choices=("janela", "sw"), default="janela",
                   help="janela deslizante (padrao) ou Stop-and-Wait")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    # Exigencia do enunciado: portas abaixo de 1024 pedem privilegio de administrador.
    # A checagem vai aqui, e nao no bind da sessao, que usa porta 0 de proposito.
    if not 1024 < args.porta <= 65535:
        p.error("--porta deve estar entre 1025 e 65535")
    if not args.raiz.is_dir():
        p.error(f"--raiz '{args.raiz}' nao e um diretorio")

    if args.verbose:
        log.setLevel(logging.DEBUG)

    log.info("raiz servida: %s", args.raiz.resolve())
    log.info("escutando UDP em 0.0.0.0:%d (modo %s)", args.porta, args.modo)

    try:
        servir(args.porta, args.raiz, args.modo)
    except KeyboardInterrupt:
        log.info("encerrado pelo usuario")


if __name__ == "__main__":
    main()
