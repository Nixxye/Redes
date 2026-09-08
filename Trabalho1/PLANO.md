# Trabalho 1 — Transferência de Arquivos Confiável sobre UDP (C++)

Disciplina ICSR30 — Prof. Mauro Fonseca (UTFPR/DAINF)
Fonte: `https://pessoal.dainf.ct.utfpr.edu.br/maurofonseca/doku.php?id=cursos:icsr30:trab2`

---

## 0. Pré-requisitos de ambiente

**Linguagem: Python 3.9+**, só biblioteca padrão. Sem build system, sem `pip install`.

- [ ] Ter `python --version` funcionando no terminal
      (há um Python 3.14 em `C:\msys64\ucrt64\bin`, mas ele fica atrás do atalho da
      Microsoft Store no PATH; para um `python` limpo: `winget install Python.Python.3.13`)

**Restrição do enunciado:** só a API de sockets, sem biblioteca que abstraia UDP.
O módulo `socket` **atende** — é um envelope fino sobre a API BSD, com os mesmos nomes e a
mesma semântica. Nada de `asyncio`, `socketserver`, `twisted`, `zmq` ou `requests`.

Permitido e usado: `struct` (empacotamento binário), `zlib.crc32`, `hashlib` (SHA-256),
`threading`, `pathlib`, `argparse`, `logging`. São serialização, hash e utilidades —
o enunciado pede explicitamente checksum e MD5/SHA.

---

## 1. Decisões de projeto do protocolo (a parte que vale nota no vídeo)

O enunciado exige **projetar e justificar** cada item abaixo. Definir antes de codar.

### 1.1 Segmentação e tamanho de buffer

- **Payload de dados fixo: 1024 bytes.**
- Justificativa: MTU Ethernet = 1500 B. Descontando cabeçalho IPv4 (20 B) e UDP (8 B),
  sobram **1472 B** de payload UDP seguro. Com o cabeçalho de aplicação de 20 B,
  o datagrama fica em **1044 B** — bem abaixo de 1472, logo **nunca há fragmentação IP**.
- Por que evitar fragmentação: se um fragmento IP se perde, o datagrama UDP inteiro é
  descartado → a taxa de perda efetiva sobe. Um segmento = um datagrama = uma unidade de perda.
- Tamanho **fixo** (só o último segmento é menor) → simplifica a indexação: `offset = seq * 1024`.
- Buffer de recepção do cliente: `recvfrom` com buffer de 2048 B (folga) e `SO_RCVBUF`
  aumentado para ~4 MB, para não perder rajadas dentro do próprio kernel.

### 1.2 Detecção de erros

- **CRC32 (IEEE 802.3, polinômio 0xEDB88320) por segmento** — cobre header + payload,
  com o campo `crc` zerado durante o cálculo. Barato e detecta erros em rajada.
- **SHA-256 do arquivo inteiro**, enviado no `META` e reconferido no final pelo cliente.
- Justificativa da dupla: CRC32 é barato o bastante para rodar em ~12 mil pacotes;
  SHA-256 dá garantia forte de que a montagem final está correta. Atende ao
  "checksum ou resumo criptográfico (MD5/SHA)" do enunciado.

### 1.3 Ordenação e detecção de perda

- **Número de sequência de 32 bits** por segmento, começando em 0.
- Cliente mantém `bytearray(total_segmentos)` como bitmap — nunca guarda o arquivo
  em RAM: escreve direto no disco com `f.seek(seq * 1024)`.
- Detecção de perda por **duas vias**:
  1. *Timeout de inatividade* (300 ms sem nenhum datagrama) → dispara varredura de buracos.
  2. *Fim de janela / fim de arquivo* → varre `recebidos` e monta a lista de faltantes.
- Segmento com CRC inválido é tratado como **não recebido** (não marca no bitmap) e entra no NACK.

### 1.4 Controle de fluxo / janela

- **Janela deslizante com repetição seletiva**, `W = 64` segmentos em voo.
- Servidor envia a janela, aguarda `ACK` (última seq contígua) ou `NACK` (lista de faltantes).
- **Modo Stop-and-Wait selecionável por flag** (`--modo sw`) — o enunciado sugere Stop-and-Wait
  e é ótimo para o vídeo: dá para mostrar os dois e comparar o tempo em 12 MB.
- Justificativa: Stop-and-Wait em 12 MB = 12288 RTTs. Com RTT de 1 ms já são 12 s; em rede real
  fica inviável. A janela amortiza o RTT sem exigir controle de congestionamento completo.

### 1.5 Mensagens de controle

Cabeçalho comum, **20 bytes, todos os inteiros em network byte order** (`htonl`/`htons`):

```
off  tam  campo
 0    2   magic      = 0x5544 ("UD")   -> descarta lixo / porta errada
 2    1   version    = 1
 3    1   type       -> ver tabela abaixo
 4    4   session_id -> aleatório, isola sessões concorrentes
 8    4   seq        -> nº de sequência (DATA/NACK) ou 0
12    2   payload_len
14    4   crc32      -> do header (com crc=0) + payload
18    2   flags      -> bit0 = LAST
                        total = 20 B  |  + 1024 B de dados = 1044 B por datagrama
```

| type | nome | direção | payload |
|-----:|------|---------|---------|
| 1 | `REQ_GET` | C → S | nome do arquivo (string, sem separador de path) |
| 2 | `META`    | S → C | `tamanho(8) total_segmentos(4) tam_segmento(2) sha256(32)` |
| 3 | `DATA`    | S → C | até 1024 B do arquivo |
| 4 | `ACK`     | C → S | `ultima_seq_contigua(4)` |
| 5 | `NACK`    | C → S | `qtd(2) + seq[0..qtd-1](4 cada)`, máx. 255 por datagrama |
| 6 | `ERR`     | S → C | `codigo(2) + mensagem` |
| 7 | `FIN`     | S → C | fim da transmissão |
| 8 | `FIN_ACK` | C → S | arquivo íntegro, sessão encerrada |

Códigos de erro: `1 FILE_NOT_FOUND`, `2 ACCESS_DENIED` (path traversal), `3 SERVER_BUSY`,
`4 BAD_REQUEST`, `5 SESSION_EXPIRED`.

### 1.6 Segurança — Path Traversal (exigido explicitamente)

Validação em camadas no servidor, antes de qualquer `open`:

1. Rejeitar nome contendo `..`, `/`, `\`, `:` ou byte nulo.
2. `(raiz / nome).resolve()`.
3. Conferir com `caminho.is_relative_to(raiz.resolve())`; senão → `ERR 2 ACCESS_DENIED`.
   (Não use `str.startswith`: `/srv/arquivos-secretos` passaria na checagem contra `/srv/arquivos`.)
4. Exigir `caminho.is_file()` — bloqueia diretório e symlink pendurado.

Testar no vídeo com `GET ../../etc/passwd` e `GET ..\..\Windows\win.ini`.

---

## 2. Arquitetura e arquivos

Modelo **estilo TFTP**: o servidor escuta numa porta bem conhecida; ao receber um `REQ_GET`,
cria uma **thread com socket UDP efêmero próprio** para aquela sessão. O cliente aprende a nova
porta pelo endereço de origem do primeiro `META`. Isso resolve "dois clientes simultâneos"
sem contenção num socket único.

Cinco arquivos, sem pacote nem subpasta de código — em Python a divisão em muitos
módulos custa mais do que rende num projeto deste tamanho.

```
Trabalho1/
├── PLANO.md
├── README.md            <- como rodar (vai no .zip)
├── docs/REDES.md        <- referência da API de sockets em Python
├── protocolo.py         <- cabeçalho, tipos, empacotar/desempacotar, CRC32
├── rede.py              <- SocketUDP: o único módulo que chama a API do SO
├── servidor.py          <- escuta, sanitização de path, sessões em thread
├── cliente.py           <- requisição, recepção, simulação de perda, NACK
├── gerar_arquivo.py     <- gera grande.bin de 12 MB determinístico
├── arquivos/            <- raiz servida: pequeno.bin, grande.bin (12 MB)
└── downloads/           <- saída do cliente
```

Três camadas: `protocolo.py` é bytes puros e não abre socket nenhum (dá para testá-lo
sem rede); `rede.py` concentra `socket`/`bind`/`sendto`/`recvfrom`/`setsockopt`;
`servidor.py` e `cliente.py` têm a lógica de transferência.

**Composição, não herança.** Servidor e cliente *usam* um `SocketUDP`, não *são* um.
O servidor, aliás, usa dois ao mesmo tempo — o de escuta na 5000 e um por sessão em
porta efêmera — o que já mostraria por que herança não caberia. A mesma classe atende
os três usos sem nenhuma subclasse:

```python
escuta  = SocketUDP(porta=5000)                            # servidor
sessao  = SocketUDP()                                      # porta efêmera
cliente = SocketUDP(timeout=0.3, buffer_recepcao=4 << 20)  # cliente
```

### Linha de comando

```
python gerar_arquivo.py arquivos/grande.bin 12

python servidor.py --porta 5000 --raiz ./arquivos [--modo sw|janela]

python cliente.py @127.0.0.1:5000/grande.bin --saida ./downloads
       [--descartar 100,101,205] [--perda 0.03] [--modo sw|janela] [--timeout 0.3]
```

- `--descartar` = lista explícita de seq a jogar fora (determinístico, ideal para o vídeo)
- `--perda` = probabilidade aleatória (ideal para estressar o arquivo de 12 MB)

Em ambos os casos o cliente **imprime cada seq descartado** — o enunciado exige isso.

---

## 3. Etapas de implementação (ordem sugerida)

| # | Etapa | Entregável verificável |
|---|-------|------------------------|
| 1 | Ambiente + eco UDP descartável | cliente/servidor trocando uma string |
| 2 | `protocolo.py` — **pronto e testado** | round-trip de cada tipo, CRC rejeita corrompido |
| 3 | Servidor: `REQ_GET` + `resolver()` com sanitização | responde `ERR FILE_NOT_FOUND` e `ERR ACCESS_DENIED` |
| 4 | `META` + `DATA` sem confiabilidade (envia tudo direto) | transfere `pequeno.txt` em loopback |
| 5 | Cliente: bitmap + escrita por offset + `sha256` final | `grande.bin` bate o hash em rede limpa |
| 6 | Simulação de perda no cliente (`--descartar`, `--perda`) | log mostra os seq jogados fora |
| 7 | **Recuperação:** timeout → varredura → `NACK` → retransmissão | 12 MB íntegro com 3 % de perda |
| 8 | Modo Stop-and-Wait + janela `W=64` | comparativo de tempo entre os dois modos |
| 9 | Concorrência: thread + socket efêmero por sessão | 2 clientes baixando arquivos diferentes ao mesmo tempo |
| 10 | Robustez: servidor fora do ar, servidor morto no meio, retry limitado | erros claros, sem travar |
| 11 | `README.md` + arquivos de teste + gerar o `.zip` | roda direto a partir do zip, sem instalar nada |
| 12 | Roteiro e gravação do vídeo (≤ 10 min) | link no YouTube |

**Atenção na etapa 10:** UDP não tem conexão, então "servidor não iniciado" não gera erro
sozinho. Duas formas de demonstrar: (a) timeout do cliente após N tentativas do `REQ_GET`;
(b) usar `sock.connect()` no socket UDP — aí o ICMP *port unreachable* faz o `recvfrom`
levantar `ConnectionRefusedError` (Linux) ou `ConnectionResetError` (Windows).
Vale mostrar as duas e explicar a diferença.

---

## 4. Mapa: item obrigatório do enunciado → onde é demonstrado

| Item exigido | Onde | Etapa |
|---|---|---|
| Execução do servidor e do(s) cliente(s) | terminais lado a lado | 1 |
| Cliente especifica IP e porta | `@127.0.0.1:5000/arquivo` | 3 |
| Uso direto da API de sockets | mostrar as chamadas `socket`/`bind`/`sendto`/`recvfrom` e o `import socket` — sem framework | 1 |
| Protocolo próprio + 2 arquivos diferentes | `GET pequeno.txt`, `GET grande.bin` | 4 |
| Arquivo > 10 MB e segmentação | `grande.bin` 12 MB = 12288 segmentos | 5 |
| Tamanho do buffer e relação com MTU | seção 1.1 | — |
| Ordenação por nº de sequência | bitmap + escrita por offset | 5 |
| Integridade (checksum/hash) | CRC32 por pacote + SHA-256 do arquivo | 2, 5 |
| Montagem e conferência final | log "SHA-256 OK" | 5 |
| Simulação de perda | `--descartar` / `--perda`, com log dos seq | 6 |
| Detecção da falta de segmentos | timeout + varredura do bitmap | 7 |
| Solicitação de retransmissão | `NACK` com lista de seq | 7 |
| Explicar o disparo da retransmissão | timer de 300 ms + NACK | 7 |
| Arquivo inexistente → erro do servidor | `ERR 1 FILE_NOT_FOUND` | 3 |
| Outros erros tratados | `ACCESS_DENIED` (path traversal), `BAD_REQUEST` | 3 |
| Dois clientes simultâneos | thread + socket por sessão | 9 |
| Cliente antes do servidor | timeout / ICMP port unreachable | 10 |
| Servidor morto no meio da transferência | timeout, N retries, aborta com erro | 10 |

---

## 5. Riscos e armadilhas conhecidas

- **Rajada perde pacote no próprio kernel** ao enviar 12 MB em loopback sem pausa.
  Mitigar com `SO_RCVBUF` grande no cliente e uma pausa de ~50 µs a cada janela no servidor.
  Vale citar no vídeo: é perda real, não simulada — ilustra bem a ausência de controle de
  fluxo no UDP.
- **Firewall do Windows** bloqueia o primeiro bind UDP → liberar antes de gravar.
- **Endianness**: o `!` no formato do `struct` é obrigatório. Sem ele o Python usa a ordem
  nativa **e insere padding de alinhamento** — o cabeçalho deixaria de ter 20 bytes.
- **O NACK também se perde.** O cliente precisa reenviar o NACK se nada chegar em 300 ms,
  com limite de tentativas (ex.: 10) antes de abortar.
- **`TimeoutError` não é erro** — é o sinal para varrer o bitmap e pedir retransmissão.
  Tratar num `except` separado, antes de qualquer outro.
- **Nunca concatenar `bytes` em laço** (`buf += x` copia tudo a cada iteração). Escrever
  direto no arquivo com `f.seek(seq * 1024)`.
- **Não commitar `grande.bin`** no git — gerar com `gerar_arquivo.py`; já está no `.gitignore`.

---

## 6. Entrega

- [ ] `.ZIP` com todo o código no Google Classroom (incluir `README.md` com instruções de build)
- [ ] Vídeo de até 10 min no YouTube — **link nos comentários particulares**, nunca o arquivo
- [ ] Vídeo em duas partes: (1) demonstração de todos os itens obrigatórios funcionando;
      (2) explicação do código, item por item, seguindo a tabela da seção 4
