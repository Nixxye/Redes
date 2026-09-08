"""Socket UDP — Trabalho 1 (ICSR30/UTFPR).

O UNICO arquivo do projeto que fala com o sistema operacional. `servidor.py` e
`cliente.py` usam esta classe (composicao), nao herdam dela: nenhum dos dois *e*
um socket, os dois *usam* um. O servidor, alias, usa dois ao mesmo tempo — o de
escuta e um por sessao — o que ja mostra por que heranca nao caberia.

E este o arquivo a abrir no video para provar uso direto da API de sockets:
socket(), bind(), sendto(), recvfrom(), setsockopt(), getsockname(). Sem framework.

A mesma classe serve os tres usos do projeto:

    escuta  = SocketUDP(porta=5000)                            # servidor
    sessao  = SocketUDP()                                      # porta efemera
    cliente = SocketUDP(timeout=0.3, buffer_recepcao=4 << 20)  # cliente

Ver docs/REDES.md secoes 2, 3, 5, 6 e 9.
"""

import socket

import protocolo as proto

# 4 MB. Sem isso, uma rajada de 12 MB em loopback perde centenas de datagramas
# por estouro do buffer do kernel, sem rede nenhuma envolvida (REDES.md secao 9).
BUFFER_RECEPCAO_PADRAO = 4 * 1024 * 1024


class SocketUDP:
    """Envelope fino sobre um socket UDP, com (des)empacotamento do protocolo.

    Nao esconde nada da API: so evita repetir a mesma sequencia de chamadas nos
    dois lados. Cada metodo abaixo corresponde a uma chamada do SO.
    """

    def __init__(self, porta=0, timeout=None, buffer_recepcao=None):
        """Cria e liga o socket.

        porta            0 = efemera (o kernel escolhe; leia depois em .porta)
        timeout          segundos; None = bloqueia para sempre
        buffer_recepcao  bytes de SO_RCVBUF; use BUFFER_RECEPCAO_PADRAO no cliente

        TODO:
          - self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
          - se buffer_recepcao: setsockopt(SOL_SOCKET, SO_RCVBUF, buffer_recepcao)
            (definir ANTES do bind; o kernel pode conceder menos — confira com
             getsockopt e logue o valor real)
          - self.sock.bind(("0.0.0.0", porta))
            "0.0.0.0" e nao "127.0.0.1": senao o cliente de outra maquina nao chega
          - se timeout is not None: self.sock.settimeout(timeout)
        """
        raise NotImplementedError

    # ------------------------------------------------------------- endereco

    @property
    def porta(self) -> int:
        """Porta local. Necessario quando se liga na porta 0 e se quer saber qual saiu.

        TODO: return self.sock.getsockname()[1]
        """
        raise NotImplementedError

    def conectar(self, destino):
        """connect() em UDP nao conversa com ninguem: so fixa o destino padrao,
        filtra datagramas de outras origens e habilita a entrega de erros ICMP
        neste socket — e assim que se detecta "servidor nao esta no ar" com
        ConnectionRefusedError em vez de so timeout (REDES.md secao 8).

        Opcional. Use apenas se for demonstrar essa deteccao.

        TODO: self.sock.connect(destino)
        """
        raise NotImplementedError

    # ------------------------------------------------------------- envio

    def enviar(self, tipo, destino, *, sessao=0, seq=0, payload=b"", flags=0) -> int:
        """Empacota e envia um datagrama. Devolve os bytes enviados.

        Em UDP o envio e tudo ou nada: nao existe envio parcial como em TCP,
        entao nao precisa de laco de reenvio aqui.

        TODO:
          dados = proto.empacotar(tipo, sessao, seq, payload, flags)
          return self.sock.sendto(dados, destino)
        """
        raise NotImplementedError

    # ------------------------------------------------------------- recepcao

    def receber(self, origem_esperada=None):
        """Recebe um datagrama, valida e desempacota.

        -> (cabecalho, payload, origem) se o pacote for utilizavel
        -> None                          se for descartavel

        Descartavel = PacoteInvalido (magic errado, versao, tamanho incoerente ou
        CRC ruim) ou origem diferente de `origem_esperada`. Em UDP, lixo chegando
        na porta e normal: descarte em silencio e siga o laco.

        NAO captura TimeoutError: deixe subir. Timeout nao e erro, e o sinal para
        varrer o bitmap e mandar NACK — quem trata e o laco de quem chamou.

        TODO:
          dados, origem = self.sock.recvfrom(proto.TAM_BUFFER)
          se origem_esperada is not None e origem != origem_esperada: return None
              (a tupla (ip, porta) compara os dois campos de uma vez; sem isso,
               qualquer processo da maquina injeta datagramas na sua transferencia)
          try:  cab, payload = proto.desempacotar(dados)
          except proto.PacoteInvalido: return None
          return cab, payload, origem
        """
        raise NotImplementedError

    def esperar(self, tipos, origem_esperada=None):
        """Recebe ate chegar um datagrama de um dos `tipos` aceitos.

        Serve para os pontos em que so uma resposta especifica interessa (o cliente
        esperando META ou ERR; o servidor esperando ACK, NACK ou FIN_ACK).
        Datagramas descartaveis e de tipo inesperado sao ignorados.

        -> (cabecalho, payload, origem). Deixa TimeoutError subir.

        TODO: laco chamando self.receber() ate cab.tipo estar em `tipos`.
        """
        raise NotImplementedError

    # ------------------------------------------------------------- ciclo de vida

    def fechar(self):
        """Fecha o socket. Em UDP nao ha handshake de encerramento: sem FIN, sem
        TIME_WAIT, a porta e liberada na hora.

        TODO: self.sock.close()
        """
        raise NotImplementedError

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.fechar()
        return False


def enviar_com_retentativa(sock, tipo, destino, tipos_esperados, *,
                           sessao=0, seq=0, payload=b"", flags=0,
                           tentativas=proto.MAX_TENTATIVAS):
    """Envia e reenvia ate obter resposta de um dos `tipos_esperados`.

    Usado nos dois lados, porque a mensagem de controle tambem se perde:
      - cliente: REQ_GET esperando META ou ERR; NACK esperando DATA
      - servidor: FIN esperando FIN_ACK

    Sem isto, um NACK perdido trava os dois lados esperando para sempre.

    -> (cabecalho, payload, origem), ou None se esgotar as tentativas
       (e o caso "servidor fora do ar" e "servidor morto no meio da transferencia")

    TODO:
      para cada tentativa em 1..tentativas:
          sock.enviar(tipo, destino, sessao=..., seq=..., payload=..., flags=...)
          try:    return sock.esperar(tipos_esperados, origem_esperada=None)
          except TimeoutError: logar a tentativa e continuar
      return None
    """
    raise NotImplementedError
