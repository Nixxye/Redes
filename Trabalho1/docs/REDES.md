# Referência de rede — como implementar cada parte (Python)

Documento de apoio ao Trabalho 1. Cobre a API de sockets UDP do Python e as
armadilhas que costumam derrubar implementações de transferência confiável sobre UDP.

As decisões de projeto (tamanho de segmento, formato do cabeçalho, janela) estão no
[PLANO.md](../PLANO.md). Aqui é só o "como fazer" da parte de rede.

> **Sobre a restrição do enunciado.** O módulo `socket` do Python **não** é uma
> biblioteca de alto nível: é um envelope fino sobre a mesma API BSD do C, com os
> mesmos nomes (`socket`, `bind`, `sendto`, `recvfrom`, `setsockopt`) e a mesma
> semântica. O que ele elimina é gerenciamento de memória e cast, não o protocolo.
> `struct`, `zlib` e `hashlib` são serialização e hash — o enunciado pede
> explicitamente checksum e MD5/SHA.

---

## 1. O que muda em relação ao TCP

TCP entrega um **fluxo de bytes** confiável e ordenado. UDP entrega **datagramas**
independentes, e só isso.

| | TCP | UDP |
|---|---|---|
| Estabelecimento | `connect` / `listen` / `accept` | não existe — só `sendto` e `recvfrom` |
| Unidade | byte; `recv` pode devolver metade | datagrama; um `recvfrom` = um datagrama inteiro |
| Ordem | garantida | pode chegar fora de ordem |
| Perda | retransmite sozinho | some sem aviso |
| Duplicata | nunca | **pode chegar duplicado** |
| Fluxo/congestionamento | janela + AIMD | nada |
| Limite de tamanho | irrelevante | ~1472 B na prática (seção 10) |

Três consequências que o código precisa tratar:

1. **Um `recvfrom` = um datagrama.** Você nunca recebe "meio pacote", então não precisa
   de máquina de estados de framing como em TCP. Mas se o buffer que você pedir for menor
   que o datagrama, **o excedente é descartado em silêncio** — por isso `TAM_BUFFER = 2048`
   para datagramas de 1044 B.
2. **Duplicatas acontecem** (sua retransmissão cruza com o original atrasado). Ao receber
   um `DATA` cujo `seq` já está no bitmap, ignore em silêncio — não é erro.
3. **O socket não sabe com quem fala.** É o `recvfrom` que devolve o endereço do remetente,
   e é você que decide se aquele remetente pertence à sessão em andamento.

---

## 2. A API de sockets em Python

```python
import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # IPv4 + datagrama = UDP
sock.bind(("0.0.0.0", 5000))                              # servidor: porta fixa
sock.settimeout(0.3)                                      # segundos, não ms
sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)

n = sock.sendto(dados, ("127.0.0.1", 5000))               # -> bytes enviados
dados, origem = sock.recvfrom(2048)                       # -> (bytes, (ip, porta))

sock.getsockname()                                        # -> ("0.0.0.0", 55599)
sock.close()
```

Os argumentos de `socket.socket` são os mesmos do C: `AF_INET` é IPv4, `SOCK_DGRAM` é
datagrama. O terceiro (`IPPROTO_UDP`) é opcional — o padrão para `AF_INET`/`SOCK_DGRAM`
já é UDP.

Diferenças que facilitam a vida em relação ao C:

- **Endereço é uma tupla `(ip, porta)`**, não uma `struct sockaddr_in`. Não há
  `sin_family`, `sin_zero`, `htons` no endereço, nem `reinterpret_cast`. O Python faz a
  conversão de ordem de bytes do endereço por você — mas **não** dos campos do *seu*
  cabeçalho (seção 7).
- **`""` equivale a `INADDR_ANY`**: `bind(("", 5000))` e `bind(("0.0.0.0", 5000))` são
  a mesma coisa — escute em todas as interfaces, senão o cliente de outra máquina não chega.
- **Fechamento automático** com `with`:

```python
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    ...
# fechado aqui, mesmo se der exceção
```

O `bind` é obrigatório no servidor (o cliente precisa saber a porta de antemão) e
**opcional no cliente** — o primeiro `sendto` faz um bind implícito numa porta efêmera.

---

## 3. Erros são exceções

Em C você checa `-1` e lê `errno`. Em Python o erro vira exceção, e são poucas as que
importam. Todas descendem de `OSError`.

| situação | exceção |
|---|---|
| Timeout do `settimeout` | `TimeoutError` (também exposto como `socket.timeout`) |
| ICMP port unreachable, Windows | `ConnectionResetError` |
| ICMP port unreachable, Linux (socket `connect`-ado) | `ConnectionRefusedError` |
| Porta já em uso | `OSError` com `errno.EADDRINUSE` |
| IP malformado em `sendto` | `socket.gaierror` |

**O timeout não é erro.** É o sinal para varrer o bitmap e mandar o NACK. Trate-o num
`except` próprio, antes de qualquer outro:

```python
try:
    dados, origem = sock.recvfrom(proto.TAM_BUFFER)
except TimeoutError:
    pedir_retransmissao()      # caminho normal, não é falha
    continue
except ConnectionResetError:
    log.error("servidor não está escutando nessa porta")
    break
```

Confundir os dois faz o cliente abortar transferências perfeitamente recuperáveis.

---

## 4. Ordem de bytes

O endereço `("127.0.0.1", 5000)` o Python converte sozinho. Mas o **seu** cabeçalho de
20 bytes é responsabilidade sua, e o `struct` resolve com um caractere:

```python
struct.pack("!HBBIIHIH", ...)     # ! = network byte order (big-endian), sem padding
```

Sem o `!`, o `struct` usa a ordem nativa da máquina **e insere padding de alinhamento** —
o cabeçalho deixaria de ter 20 bytes. Como você vai testar tudo em x86, o erro só
apareceria na correção. Use `!` sempre; `>` faz o mesmo, mas `!` documenta a intenção.

---

## 5. Servidor: sequência e modelo de concorrência

```python
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    sock.bind(("0.0.0.0", 5000))          # porta > 1024, exigência do enunciado
    while True:
        dados, origem = sock.recvfrom(proto.TAM_BUFFER)
        try:
            cab, payload = proto.desempacotar(dados)
        except proto.PacoteInvalido:
            continue                       # lixo na porta é normal em UDP
        if cab.tipo is not proto.Tipo.REQ_GET:
            continue
        # resolver o nome (seção 13) e despachar para uma thread de sessão
```

Não há `listen` nem `accept`. O `bind` amarra o socket à porta 5000; daí em diante
qualquer datagrama endereçado a ela cai no seu `recvfrom`.

### Modelo TFTP: um socket por sessão

O servidor escuta na 5000 apenas para receber `REQ_GET`. Ao aceitar um pedido, cria uma
**thread com um socket UDP novo, em porta efêmera** (`bind(("", 0))`), e toda a
transferência acontece ali. O cliente descobre a porta nova pelo endereço de origem do
primeiro `META` e passa a falar com ela.

Por que assim:

- A porta 5000 fica livre para novos pedidos enquanto transferências rolam — é o que faz
  "dois clientes simultâneos" funcionar sem fila.
- **Duas threads não podem chamar `recvfrom` no mesmo socket de forma sã**: os datagramas
  seriam distribuídos ao acaso entre elas, e cada uma receberia pacotes da sessão da outra.
  Um socket por sessão elimina o problema na raiz, sem fila e sem lock.
- É exatamente o que o TFTP (RFC 1350) faz — a decisão é defensável no vídeo.

O `session_id` no cabeçalho continua útil: protege contra datagramas atrasados de uma
sessão anterior que tenha reusado a mesma porta efêmera.

**Alternativas**, caso o professor pergunte: socket único com tabela de sessões e
event loop (`select`) — mais escalável, mas exige máquina de estados explícita; ou socket
único com pool de threads. Thread-por-sessão não escalaria para milhares de clientes,
mas o enunciado pede dois.

**Defeito conhecido do modelo TFTP:** a resposta sai de uma porta diferente daquela para
onde o cliente enviou, e NATs/firewalls com estado descartam esse retorno. É por isso que
o Linux tem um módulo `nf_conntrack_tftp` dedicado. Em loopback e na LAN não aparece.

---

## 6. Cliente: sequência

```python
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    sock.settimeout(0.3)
    sock.sendto(proto.empacotar(proto.Tipo.REQ_GET, payload=nome.encode()), (ip, porta))

    dados, sessao = sock.recvfrom(proto.TAM_BUFFER)   # sessao = porta efêmera do servidor
    ...
    while True:
        dados, origem = sock.recvfrom(proto.TAM_BUFFER)
        if origem != sessao:
            continue                                  # datagrama de terceiro
```

A comparação `origem != sessao` não é paranoia: sem ela, qualquer processo na máquina pode
injetar datagramas na sua transferência. Em Python a tupla compara IP **e** porta de uma vez.

---

## 7. Serialização com `struct`

Todo o cabeçalho de 20 bytes cabe em duas linhas:

```python
FORMATO = "!HBBIIHIH"   # magic, versão, tipo, sessão, seq, tam, crc, flags
cab = struct.pack(FORMATO, MAGIC, VERSAO, tipo, sessao, seq, len(dados), crc, flags)
magic, versao, tipo, sessao, seq, tam, crc, flags = struct.unpack(FORMATO, buf[:20])
```

| letra | bytes | usado para |
|---|---|---|
| `B` | 1 | versão, tipo |
| `H` | 2 | magic, tam, flags |
| `I` | 4 | sessão, seq, crc |
| `Q` | 8 | tamanho do arquivo no META |
| `32s` | 32 | digest SHA-256 |

Ordem do CRC: empacote com o campo `crc` em zero, calcule `zlib.crc32` sobre
cabeçalho + payload, e reempacote com o valor. Na recepção, guarde o valor recebido,
zere o campo, recalcule e compare. É o que `protocolo.py` já faz.

**Valide antes de usar.** `desempacotar` confere magic, versão, coerência entre
`payload_len` e o tamanho recebido, e só então o CRC. Qualquer falha vira `PacoteInvalido`
e o datagrama é descartado em silêncio.

---

## 8. Timeouts, retransmissão e servidor fora do ar

`sock.settimeout(0.3)` faz o `recvfrom` levantar `TimeoutError` após 300 ms.

### O NACK também se perde

Erro clássico: o cliente manda o NACK, ele se perde, e os dois lados esperam para sempre.
O timeout tem que rearmar — se nada chegar em 300 ms depois de enviar o NACK, **reenvie o
NACK**, até `MAX_TENTATIVAS` (10). Só então aborte com erro. É isso que faz o item
"servidor interrompido durante a transferência" terminar com mensagem clara em vez de travar.

### "Cliente executado antes do servidor"

Em UDP não há handshake: o `sendto` para uma porta morta **tem sucesso**, porque só entrega
o datagrama ao kernel. O erro só volta pelo ICMP *Port Unreachable*, e o comportamento diverge:

- **Socket não conectado, Linux:** o ICMP é descartado; você só percebe pelo timeout.
- **Socket conectado** (`sock.connect((ip, porta))` — legítimo em UDP, só fixa o destino
  padrão e filtra a origem): o `recvfrom` seguinte levanta `ConnectionRefusedError`.
- **Windows:** levanta `ConnectionResetError` **mesmo sem `connect`**. Comportamento que
  não existe no Linux — se for usar como detecção, documente que é específico do Windows.

Demonstre as duas formas no vídeo e explique que a detecção não vem do UDP, e sim do ICMP.

---

## 9. Buffers do kernel e a perda que não é sua culpa

Ao mandar 12 MB em rajada por loopback, é normal perder centenas de segmentos **sem rede
nenhuma envolvida**: o servidor produz mais rápido do que o cliente drena, o buffer de
recepção do kernel enche, e o kernel descarta datagramas em silêncio.

```python
sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
print(sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF))   # confira o valor concedido
```

Mais uma pausa curta no servidor a cada janela (~50 µs). No Linux o valor é limitado por
`net.core.rmem_max`, e o kernel dobra o que você pede — por isso conferir com `getsockopt`.

Isso rende um bom ponto no vídeo: é demonstração ao vivo de que **UDP não tem controle de
fluxo**, e de que seu mecanismo de NACK recupera perda real, não só a simulada.

---

## 10. MTU e tamanho do datagrama

- Payload UDP máximo teórico: **65507 B** (65535 − 8 de UDP − 20 de IPv4).
- MTU típica de Ethernet: **1500 B**. Menos IPv4 (20) e UDP (8), sobram **1472 B** que
  cabem num único quadro.
- Acima disso o IP **fragmenta**: o datagrama vira vários pacotes e o destino só entrega à
  aplicação se **todos** chegarem. Perder um fragmento descarta o datagrama inteiro.
- Por isso: 1024 B de dados + 20 B de cabeçalho = **1044 B**, folgado abaixo de 1472.
- O valor conservador para atravessar qualquer caminho IPv4 é **508 B** (mínimo de
  remontagem de 576 B da RFC 1122, menos cabeçalhos). Se funcionar em loopback mas falhar
  por VPN, é esse o motivo.

**Trade-off que o enunciado pede para explicar:** segmento maior = menos pacotes, menos
overhead de cabeçalho e menos chamadas de sistema; mas cada perda custa mais bytes
retransmitidos, e passar de 1472 B traz fragmentação. 1024 B é o meio-termo, e dá um
número redondo para `offset = seq * 1024`.

---

## 11. Threads e o GIL

Pergunta natural: "com o GIL, duas sessões rodam mesmo em paralelo?" Sim — **o GIL é
liberado durante chamadas de socket**. Como o trabalho aqui é I/O, não CPU, `threading`
entrega paralelismo real. Não precisa de `multiprocessing` nem `asyncio`.

- O `logging` da stdlib já é thread-safe: as linhas de duas sessões não saem intercaladas.
  (Em C isso exigiria um mutex; aqui vem de graça.)
- Use `daemon=True` nas threads de sessão para o Ctrl+C encerrar o servidor sem travar.
- Uma sessão precisa terminar sozinha por timeout se o cliente sumir, senão a thread vaza.

---

## 12. Desempenho

12 MB em segmentos de 1024 B são 12288 datagramas. Em Python isso é tranquilo — a
transferência é limitada por I/O, não por CPU. Dois detalhes se quiser apertar:

- `zlib.crc32` é C compilado, não Python. Rápido.
- Evite concatenar `bytes` em laço (`buf += x` copia tudo a cada vez). Use `bytearray`,
  ou escreva direto no arquivo com `seek`.
- Nunca guarde o arquivo inteiro em memória: `f.seek(seq * 1024); f.write(dados)`.

---

## 13. Sanitização de caminho (Path Traversal)

Exigido explicitamente pelo enunciado. Um servidor que faz `open(raiz / nome_recebido)`
entrega o disco inteiro para quem pedir `../../etc/passwd`.

```python
from pathlib import Path

def resolver(raiz: Path, nome: str):
    if not nome or "\x00" in nome:
        return None, CodigoErro.BAD_REQUEST
    if ".." in nome or "/" in nome or "\\" in nome or ":" in nome:
        return None, CodigoErro.ACCESS_DENIED

    raiz = raiz.resolve()
    caminho = (raiz / nome).resolve()          # resolve ".", ".." e symlinks

    if not caminho.is_relative_to(raiz):       # Python 3.9+
        return None, CodigoErro.ACCESS_DENIED
    if not caminho.is_file():
        return None, CodigoErro.FILE_NOT_FOUND
    return caminho, None
```

Dois pontos que costumam passar batido:

- **`is_relative_to`, não `str.startswith`.** `/srv/arquivos-secretos` começa com o prefixo
  `/srv/arquivos` e passaria numa checagem ingênua de string.
- **Não use `Path(nome).name` para "limpar" o nome.** Ele transforma `../../etc/passwd` em
  `passwd` e serve um arquivo diferente **sem erro nenhum** — mascarar o ataque é pior que
  recusá-lo, porque você perde o log.

Qualquer falha → `ERR ACCESS_DENIED`, sem revelar se o arquivo existe. Registre a tentativa:
rende demonstração no vídeo.

---

## 14. Como testar

- **Hello world primeiro.** Um servidor que ecoa uma string e um cliente que a envia,
  antes de qualquer protocolo. Valida endereçamento e firewall de uma vez.
- **Firewall do Windows** pergunta na primeira vez que o Python faz `bind`. Libere antes de
  gravar o vídeo, senão a gravação trava num diálogo.
- **`127.0.0.1` esconde problemas** (sem perda, sem reordenação, MTU de 65536). Teste também
  entre duas máquinas na mesma rede, ou entre Windows e uma VM.
- **Perda determinística antes da aleatória.** `--descartar 100,101,205` dá um cenário
  reproduzível para depurar o NACK; só depois ligue `--perda 0.03` nos 12 MB.
- **A lista fixa descarta só na primeira aparição.** Se o cliente jogar fora o seq 100 toda
  vez que ele chegar, a transferência nunca termina.
- **Wireshark** filtrando `udp.port == 5000` mostra o cabeçalho que você inventou byte a
  byte. Aparecer com isso no vídeo pesa a favor.
- **Conferência independente:** compare o hash do original e do baixado por fora do seu
  programa (`certutil -hashfile arquivo SHA256` no Windows, `sha256sum` no Linux) — prova
  que a verificação interna não está apenas concordando consigo mesma.

---

## 15. Referências

**Python**
- Módulo `socket` — https://docs.python.org/3/library/socket.html
- Socket Programming HOWTO — https://docs.python.org/3/howto/sockets.html
- `struct` (empacotamento binário) — https://docs.python.org/3/library/struct.html
- `zlib.crc32` — https://docs.python.org/3/library/zlib.html#zlib.crc32
- `hashlib` (SHA-256, MD5) — https://docs.python.org/3/library/hashlib.html
- `threading` — https://docs.python.org/3/library/threading.html
- `pathlib` — https://docs.python.org/3/library/pathlib.html
- UDP sockets em Python (link do enunciado) — https://wiki.python.org/moin/UdpCommunication

**Especificações e teoria**
- RFC 768 — User Datagram Protocol — https://www.rfc-editor.org/rfc/rfc768
- RFC 1122 — Requirements for Internet Hosts — https://www.rfc-editor.org/rfc/rfc1122
- RFC 1191 — Path MTU Discovery — https://www.rfc-editor.org/rfc/rfc1191
- RFC 1350 — TFTP (o modelo de porta efêmera por sessão) — https://www.rfc-editor.org/rfc/rfc1350
- Stop-and-Wait ARQ (link do enunciado) — https://en.wikipedia.org/wiki/Stop-and-wait_ARQ
- Repetição seletiva — https://en.wikipedia.org/wiki/Selective_Repeat_ARQ
- CRC-32 — https://en.wikipedia.org/wiki/Cyclic_redundancy_check

**Segurança**
- OWASP — Path Traversal (link do enunciado) — https://owasp.org/www-community/attacks/Path_Traversal
