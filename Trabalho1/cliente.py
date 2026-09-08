"""Cliente UDP — Trabalho 1 (ICSR30/UTFPR).

    python cliente.py @127.0.0.1:5000/grande.bin --saida ./downloads
    python cliente.py @127.0.0.1:5000/grande.bin --descartar 100,101,205
    python cliente.py @127.0.0.1:5000/grande.bin --perda 0.03 --modo sw

Ver docs/REDES.md secao 6 para a sequencia de chamadas.
"""

import argparse
import logging
import random
import re
from pathlib import Path

import protocolo as proto
import rede

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("cliente")

# @127.0.0.1:5000/grande.bin  (o @ inicial e opcional)
ALVO = re.compile(r"^@?(?P<ip>[^:/]+):(?P<porta>\d+)/(?P<arquivo>.+)$")


class SimuladorDePerda:
    """Descarta segmentos de proposito, para testar a recuperacao.

    Exigido pelo enunciado, que pede tambem INFORMAR quais seq foram descartados.

    `fixos`: descarta cada seq da lista UMA unica vez. Tem que ser so na primeira
    aparicao — se descartar tambem a retransmissao, a transferencia nunca termina.
    `probabilidade`: descarte aleatorio, com semente fixa para ser reproduzivel.
    """

    def __init__(self, fixos=(), probabilidade=0.0, semente=42):
        self.pendentes = set(fixos)
        self.probabilidade = probabilidade
        self.rng = random.Random(semente)
        self.descartados = 0

    def deve_descartar(self, seq: int) -> bool:
        if seq in self.pendentes:
            self.pendentes.discard(seq)     # so na primeira vez
            self.descartados += 1
            log.info("DESCARTANDO seq=%d (perda simulada, lista fixa)", seq)
            return True
        if self.probabilidade > 0 and self.rng.random() < self.probabilidade:
            self.descartados += 1
            log.info("DESCARTANDO seq=%d (perda simulada, aleatoria)", seq)
            return True
        return False


class Receptor:
    """Ordena, verifica e monta o arquivo.

    TODO:
      - __init__(caminho_saida, total_segmentos, tam_segmento, sha256_esperado):
          abrir o arquivo em "wb", pre-alocar com truncate(tamanho)
          self.recebidos = bytearray(total_segmentos)   # 1 byte por segmento
      - guardar(seq, dados):
          f.seek(seq * proto.TAM_PAYLOAD); f.write(dados)
          self.recebidos[seq] = 1
          (seq ja marcado = duplicata, ignore em silencio — e normal em UDP)
      - faltantes(limite): varre o bitmap e devolve ate `limite` seq com 0
      - completo(): all(self.recebidos)
      - conferir(): sha256 do arquivo montado x o que veio no META
    """

    def __init__(self, caminho, total, tam_segmento, sha256_esperado):
        raise NotImplementedError


# ----------------------------------------------------------------- transferencia

def baixar(ip, porta, arquivo, saida: Path, perda: SimuladorDePerda,
           modo: str, timeout: float):
    """Pede o arquivo e o recebe inteiro.

    TODO:
      - with rede.SocketUDP(timeout=timeout,
                            buffer_recepcao=rede.BUFFER_RECEPCAO_PADRAO) as sock:
      - pedir o arquivo, tolerando REQ_GET perdido:
          rede.enviar_com_retentativa(sock, Tipo.REQ_GET, (ip, porta),
                                      (Tipo.META, Tipo.ERR),
                                      payload=arquivo.encode("utf-8"))
        devolve None se esgotar as tentativas — e aqui que "cliente antes do
        servidor" aparece (docs/REDES.md secao 8)
      - primeira resposta:
          Tipo.ERR  -> proto.ler_erro(), exibir e sair
          Tipo.META -> `sessao` = endereco de ORIGEM do META (a porta efemera do
                       servidor, NAO a 5000) e criar o Receptor
      - laco de recepcao:
          recebido = sock.receber(origem_esperada=sessao)   # None = ignore
          DATA: se perda.deve_descartar(cab.seq): continue
                senao receptor.guardar(cab.seq, payload)
          mandar ACK periodicamente
      - TimeoutError: montar NACK com receptor.faltantes(proto.MAX_SEQS_POR_NACK)
        e enviar; contar tentativas; o NACK tambem se perde, entao reenvie
      - FIN ou bitmap completo -> receptor.conferir()
          ok       -> log de sucesso + FIN_ACK
          faltando -> NACK e continuar
      - apos proto.MAX_TENTATIVAS sem resposta, abortar com mensagem clara
        (e aqui que "servidor morto no meio da transferencia" aparece)
    """
    raise NotImplementedError


def main():
    p = argparse.ArgumentParser(description="cliente UDP de transferencia de arquivos")
    p.add_argument("alvo", help="@IP:PORTA/arquivo, ex: @127.0.0.1:5000/grande.bin")
    p.add_argument("--saida", type=Path, default=Path("./downloads"))
    p.add_argument("--descartar", default="",
                   help="seq a descartar de proposito, ex: 100,101,205")
    p.add_argument("--perda", type=float, default=0.0,
                   help="probabilidade de descarte aleatorio, ex: 0.03")
    p.add_argument("--modo", choices=("janela", "sw"), default="janela")
    p.add_argument("--timeout", type=float, default=proto.TIMEOUT,
                   help="segundos sem trafego antes de pedir retransmissao")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    m = ALVO.match(args.alvo)
    if not m:
        p.error("alvo deve ser @IP:PORTA/arquivo (ex: @127.0.0.1:5000/grande.bin)")

    porta = int(m["porta"])
    if not 0 < porta <= 65535:
        p.error("porta fora da faixa valida")
    if not 0.0 <= args.perda < 1.0:
        p.error("--perda deve estar entre 0.0 e 1.0")

    try:
        fixos = [int(x) for x in args.descartar.split(",") if x.strip()]
    except ValueError:
        p.error("--descartar espera numeros separados por virgula")

    if args.verbose:
        log.setLevel(logging.DEBUG)

    args.saida.mkdir(parents=True, exist_ok=True)
    perda = SimuladorDePerda(fixos, args.perda)

    log.info("servidor %s:%d, arquivo '%s'", m["ip"], porta, m["arquivo"])
    log.info("saida %s, modo %s, timeout %.1fs", args.saida.resolve(), args.modo, args.timeout)
    if fixos:
        log.info("vai descartar de proposito os seq: %s", fixos)
    if args.perda:
        log.info("perda aleatoria simulada: %.1f%%", args.perda * 100)

    try:
        baixar(m["ip"], porta, m["arquivo"], args.saida, perda, args.modo, args.timeout)
    except KeyboardInterrupt:
        log.info("interrompido pelo usuario")


if __name__ == "__main__":
    main()
