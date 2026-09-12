"""Cliente UDP — Trabalho 1 (ICSR30/UTFPR).

    python cliente.py @127.0.0.1:5000/grande.bin --saida ./downloads
    python cliente.py @127.0.0.1:5000/grande.bin --descartar 100,101,205
    python cliente.py @127.0.0.1:5000/grande.bin --perda 0.03 --modo sw

Ver docs/REDES.md secao 6 para a sequencia de chamadas.
"""

import argparse
import hashlib
import logging
import random
import re
import time
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

    Nunca guarda o arquivo em RAM: escreve cada segmento direto no disco, no offset
    calculado a partir do numero de sequencia. O bitmap `recebidos` (1 byte por
    segmento) e o que permite ordenar, detectar buracos e ignorar duplicatas.
    """

    def __init__(self, caminho: Path, tamanho, total, tam_segmento, sha256_esperado):
        self.caminho = caminho
        self.tamanho = tamanho
        self.total = total
        self.tam_segmento = tam_segmento
        self.sha_esperado = sha256_esperado
        self.recebidos = bytearray(total)      # 0 = falta, 1 = ok
        self.n_recebidos = 0                    # contador para completo() em O(1)

        # Abre e pre-aloca, para poder escrever em qualquer offset por seek.
        self.f = caminho.open("wb+")
        if tamanho:
            self.f.truncate(tamanho)

    def guardar(self, seq: int, dados: bytes) -> bool:
        """Escreve o segmento no lugar certo. -> True se era novo, False se duplicata."""
        if seq >= self.total or self.recebidos[seq]:
            return False                        # fora da faixa ou ja tinha: ignore
        self.f.seek(seq * self.tam_segmento)
        self.f.write(dados)
        self.recebidos[seq] = 1
        self.n_recebidos += 1
        return True

    def faltantes(self, limite):
        """Ate `limite` numeros de sequencia ainda nao recebidos (para o NACK)."""
        faltam = []
        for seq in range(self.total):
            if not self.recebidos[seq]:
                faltam.append(seq)
                if len(faltam) >= limite:
                    break
        return faltam

    def completo(self) -> bool:
        return self.n_recebidos == self.total

    def conferir(self) -> bool:
        """SHA-256 do arquivo montado x o hash anunciado no META."""
        self.f.flush()
        sha = hashlib.sha256()
        self.f.seek(0)
        for bloco in iter(lambda: self.f.read(1 << 16), b""):
            sha.update(bloco)
        return sha.digest() == self.sha_esperado

    def fechar(self):
        self.f.close()


# ----------------------------------------------------------------- transferencia

def _pedir_nack(sock, sessao, receptor, motivo):
    """Monta e envia um NACK com os buracos atuais. -> lista de seq faltantes."""
    faltam = receptor.faltantes(proto.MAX_SEQS_POR_NACK)
    if faltam:
        sock.enviar(proto.Tipo.NACK, sessao, payload=proto.montar_nack(faltam))
        log.info("%s: solicitando retransmissao de %d segmento(s): %s%s", motivo,
                 len(faltam), faltam[:8], " ..." if len(faltam) > 8 else "")
    return faltam


def baixar(ip, porta, arquivo, saida: Path, perda: SimuladorDePerda,
           modo: str, timeout: float) -> bool:
    """Pede o arquivo e o recebe inteiro. -> True em caso de sucesso."""
    servidor = (ip, porta)
    saida_arquivo = saida / Path(arquivo).name

    with rede.SocketUDP(timeout=timeout,
                        buffer_recepcao=rede.BUFFER_RECEPCAO_PADRAO) as sock:

        # 1. Pede o arquivo, tolerando REQ_GET ou META perdido. None = ninguem
        #    respondeu apos MAX_TENTATIVAS -> "cliente antes do servidor" ou porta
        #    errada (docs/REDES.md secao 8).
        resposta = rede.enviar_com_retentativa(
            sock, proto.Tipo.REQ_GET, servidor, (proto.Tipo.META, proto.Tipo.ERR),
            payload=arquivo.encode("utf-8"))

        if resposta is None:
            log.error("servidor nao respondeu em %s:%d — ele esta no ar?", ip, porta)
            return False

        cab, payload, sessao = resposta

        if cab.tipo is proto.Tipo.ERR:
            codigo, mensagem = proto.ler_erro(payload)
            nome = codigo.name if isinstance(codigo, proto.CodigoErro) else codigo
            log.error("Erro do servidor: %s (%s)", mensagem, nome)
            return False

        # cab.tipo == META. `sessao` = endereco de ORIGEM do META = porta efemera
        # da sessao no servidor (NAO a porta 5000). Toda a conversa segue com ela.
        tamanho, total, tam_seg, sha = proto.ler_meta(payload)
        log.info("META: %d bytes, %d segmentos de %d B (sessao em %s)",
                 tamanho, total, tam_seg, sessao)

        receptor = Receptor(saida_arquivo, tamanho, total, tam_seg, sha)
        inicio = time.monotonic()
        tentativas = 0
        proximo_log = 0

        try:
            # 2. Laco de recepcao.
            while not receptor.completo():
                try:
                    recebido = sock.receber(origem_esperada=sessao)
                except TimeoutError:
                    # Silencio: pede os buracos. O NACK tambem se perde, entao isto
                    # se repete ate MAX_TENTATIVAS (servidor morto no meio da transfer.)
                    tentativas += 1
                    if tentativas >= proto.MAX_TENTATIVAS:
                        log.error("servidor parou de responder; abortando "
                                  "(%d/%d segmentos)", receptor.n_recebidos, total)
                        return False
                    _pedir_nack(sock, sessao, receptor, "timeout")
                    continue

                if recebido is None:
                    continue                    # lixo ou origem de terceiro
                cab, payload, _ = recebido
                tentativas = 0                  # houve trafego valido: progresso

                if cab.tipo is proto.Tipo.DATA:
                    if perda.deve_descartar(cab.seq):
                        continue                # perda simulada: nao entrega
                    receptor.guardar(cab.seq, payload)
                    if modo == "sw":
                        # Stop-and-Wait exige ACK por segmento (mesmo duplicata).
                        sock.enviar(proto.Tipo.ACK, sessao, payload=proto.montar_ack(cab.seq))
                    if receptor.n_recebidos >= proximo_log:
                        log.info("recebidos %d/%d (%.0f%%)", receptor.n_recebidos,
                                 total, 100 * receptor.n_recebidos / max(total, 1))
                        proximo_log = receptor.n_recebidos + max(total // 10, 1)

                elif cab.tipo is proto.Tipo.FIN:
                    # Servidor terminou de enviar. Se ainda faltam, pede; senao o
                    # while sai sozinho na proxima checagem.
                    if not receptor.completo():
                        _pedir_nack(sock, sessao, receptor, "FIN recebido, faltam segmentos")

            # 3. Completo: confere a integridade do arquivo inteiro.
            if not receptor.conferir():
                log.error("SHA-256 nao confere — arquivo corrompido apos a montagem")
                return False

            decorrido = time.monotonic() - inicio
            mbps = (tamanho * 8 / 1e6 / decorrido) if decorrido else 0
            log.info("OK: %s (%d bytes) em %.2fs (%.1f Mbit/s); SHA-256 confere",
                     saida_arquivo, tamanho, decorrido, mbps)
            if perda.descartados:
                log.info("recuperados apos %d descarte(s) simulado(s)", perda.descartados)

            # 4. Confirma o fim e responde eventuais FIN reenviados (FIN_ACK perdido).
            sock.enviar(proto.Tipo.FIN_ACK, sessao)
            for _ in range(3):
                try:
                    r = sock.receber(origem_esperada=sessao)
                except TimeoutError:
                    break
                if r is not None and r[0].tipo is proto.Tipo.FIN:
                    sock.enviar(proto.Tipo.FIN_ACK, sessao)
            return True
        finally:
            receptor.fechar()


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
        ok = baixar(m["ip"], porta, m["arquivo"], args.saida, perda, args.modo, args.timeout)
    except KeyboardInterrupt:
        log.info("interrompido pelo usuario")
        return 130
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
