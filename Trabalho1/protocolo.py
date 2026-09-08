"""Protocolo de aplicacao sobre UDP — Trabalho 1 (ICSR30/UTFPR).

Cabecalho de 20 bytes, tudo em network byte order (o '!' do struct):

    magic(2) versao(1) tipo(1) sessao(4) seq(4) tam(2) crc(4) flags(2)

O CRC32 cobre cabecalho + payload, com o proprio campo crc zerado no calculo.

Este modulo e so encanamento: empacota, desempacota e valida. A logica de rede
(segmentacao, timeout, NACK, retransmissao, janela) fica em servidor.py e cliente.py.
"""

import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum

# ----------------------------------------------------------------- constantes

MAGIC = 0x5544            # "UD" — descarta lixo que chegue na porta
VERSAO = 1

FORMATO_CABECALHO = "!HBBIIHIH"
TAM_CABECALHO = struct.calcsize(FORMATO_CABECALHO)   # 20

# 1024 + 20 = 1044 bytes por datagrama, folgado abaixo dos 1472 B que cabem numa
# MTU de 1500 sem fragmentacao IP. Ver PLANO.md secao 1.1.
TAM_PAYLOAD = 1024
TAM_DATAGRAMA = TAM_CABECALHO + TAM_PAYLOAD          # 1044
TAM_BUFFER = 2048         # folga no recvfrom: o excedente seria descartado calado

JANELA = 64               # segmentos em voo no modo janela
TIMEOUT = 0.3             # segundos sem trafego antes de varrer buracos
MAX_TENTATIVAS = 10       # desiste depois disso e aborta com erro

FLAG_ULTIMO = 0x0001      # marca o ultimo segmento do arquivo


class Tipo(IntEnum):
    REQ_GET = 1           # C -> S: nome do arquivo
    META = 2              # S -> C: tamanho, total de segmentos, sha256
    DATA = 3              # S -> C: ate 1024 B do arquivo
    ACK = 4               # C -> S: ultima seq contigua
    NACK = 5              # C -> S: lista de seq faltantes
    ERR = 6               # S -> C: codigo + mensagem
    FIN = 7               # S -> C: fim da transmissao
    FIN_ACK = 8           # C -> S: arquivo integro, sessao encerrada


class CodigoErro(IntEnum):
    FILE_NOT_FOUND = 1
    ACCESS_DENIED = 2
    SERVER_BUSY = 3
    BAD_REQUEST = 4
    SESSION_EXPIRED = 5


MENSAGENS_ERRO = {
    CodigoErro.FILE_NOT_FOUND: "arquivo nao encontrado",
    CodigoErro.ACCESS_DENIED: "acesso negado",
    CodigoErro.SERVER_BUSY: "servidor ocupado",
    CodigoErro.BAD_REQUEST: "requisicao invalida",
    CodigoErro.SESSION_EXPIRED: "sessao expirada",
}


class PacoteInvalido(Exception):
    """Datagrama descartavel: magic errado, versao errada, tamanho incoerente ou
    CRC invalido. Em UDP isso e normal — descarte em silencio e siga."""


@dataclass(frozen=True)
class Cabecalho:
    tipo: Tipo
    sessao: int
    seq: int
    tam: int
    flags: int

    @property
    def ultimo(self) -> bool:
        return bool(self.flags & FLAG_ULTIMO)


def empacotar(tipo, sessao=0, seq=0, payload=b"", flags=0) -> bytes:
    """Monta um datagrama completo, ja com o CRC32 calculado."""
    if len(payload) > TAM_PAYLOAD:
        raise ValueError(f"payload de {len(payload)} B excede {TAM_PAYLOAD} B")

    campos = (MAGIC, VERSAO, int(tipo), sessao, seq, len(payload), 0, flags)
    cabecalho = struct.pack(FORMATO_CABECALHO, *campos)
    crc = zlib.crc32(cabecalho + payload) & 0xFFFFFFFF

    campos = (MAGIC, VERSAO, int(tipo), sessao, seq, len(payload), crc, flags)
    return struct.pack(FORMATO_CABECALHO, *campos) + payload


def desempacotar(datagrama: bytes):
    """Valida e separa (Cabecalho, payload). Levanta PacoteInvalido se algo nao bater.

    Valide SEMPRE antes de usar qualquer campo: o conteudo veio da rede.
    """
    if len(datagrama) < TAM_CABECALHO:
        raise PacoteInvalido(f"datagrama de {len(datagrama)} B, menor que o cabecalho")

    magic, versao, tipo, sessao, seq, tam, crc, flags = struct.unpack(
        FORMATO_CABECALHO, datagrama[:TAM_CABECALHO]
    )

    if magic != MAGIC:
        raise PacoteInvalido(f"magic 0x{magic:04X} inesperado")
    if versao != VERSAO:
        raise PacoteInvalido(f"versao {versao} nao suportada")

    payload = datagrama[TAM_CABECALHO:]
    if len(payload) != tam:
        raise PacoteInvalido(f"tam diz {tam} B mas chegaram {len(payload)} B")

    zerado = struct.pack(FORMATO_CABECALHO, magic, versao, tipo, sessao, seq, tam, 0, flags)
    if zlib.crc32(zerado + payload) & 0xFFFFFFFF != crc:
        raise PacoteInvalido(f"CRC invalido (seq={seq})")

    try:
        tipo = Tipo(tipo)
    except ValueError:
        raise PacoteInvalido(f"tipo {tipo} desconhecido") from None

    return Cabecalho(tipo, sessao, seq, tam, flags), payload


FORMATO_META = "!QIH32s"   # tamanho(8) total_segmentos(4) tam_segmento(2) sha256(32)


def montar_meta(tamanho: int, total: int, tam_segmento: int, sha256: bytes) -> bytes:
    return struct.pack(FORMATO_META, tamanho, total, tam_segmento, sha256)


def ler_meta(payload: bytes):
    """-> (tamanho, total_segmentos, tam_segmento, sha256)"""
    return struct.unpack(FORMATO_META, payload)


def montar_ack(ultima_contigua: int) -> bytes:
    return struct.pack("!I", ultima_contigua)


def ler_ack(payload: bytes) -> int:
    return struct.unpack("!I", payload)[0]


# (1024 - 2) // 4 = 255 numeros de sequencia por datagrama de NACK
MAX_SEQS_POR_NACK = (TAM_PAYLOAD - 2) // 4


def montar_nack(seqs) -> bytes:
    seqs = list(seqs)
    if len(seqs) > MAX_SEQS_POR_NACK:
        raise ValueError(f"{len(seqs)} seqs excede o limite de {MAX_SEQS_POR_NACK}")
    return struct.pack(f"!H{len(seqs)}I", len(seqs), *seqs)


def ler_nack(payload: bytes):
    (qtd,) = struct.unpack("!H", payload[:2])
    return list(struct.unpack(f"!{qtd}I", payload[2:2 + qtd * 4]))


def montar_erro(codigo: CodigoErro, mensagem: str = "") -> bytes:
    if not mensagem:
        mensagem = MENSAGENS_ERRO.get(codigo, "erro")
    return struct.pack("!H", int(codigo)) + mensagem.encode("utf-8")


def ler_erro(payload: bytes):
    """-> (CodigoErro | int, mensagem)"""
    (codigo,) = struct.unpack("!H", payload[:2])
    mensagem = payload[2:].decode("utf-8", errors="replace")
    try:
        codigo = CodigoErro(codigo)
    except ValueError:
        pass
    return codigo, mensagem
