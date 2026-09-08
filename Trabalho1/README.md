# Trabalho 1 — Transferência de arquivos confiável sobre UDP

ICSR30 — UTFPR/DAINF — Prof. Mauro Fonseca

Cliente/servidor de transferência de arquivos sobre **UDP puro**, em Python, com os
mecanismos de confiabilidade (segmentação, numeração, checksum, detecção de perda e
retransmissão) implementados na camada de aplicação.

Usa apenas a biblioteca padrão, e o módulo `socket` diretamente — nenhuma biblioteca
que abstraia UDP.

## Documentos

| arquivo | conteúdo |
|---|---|
| [PLANO.md](PLANO.md) | decisões de projeto do protocolo, etapas e mapa dos itens obrigatórios |
| [docs/REDES.md](docs/REDES.md) | referência da API de sockets em Python: como implementar cada parte |

## Arquivos

```
protocolo.py       cabeçalho de 20 bytes, tipos de mensagem, CRC32   [pronto]
rede.py            SocketUDP — o único módulo que fala com o SO      [roteiro]
servidor.py        escuta, sanitização de path, sessões em thread     [roteiro]
cliente.py         requisição, recepção, simulação de perda, NACK     [roteiro]
gerar_arquivo.py   cria o binário de teste                            [pronto]
arquivos/          raiz servida pelo servidor  (não versionada)
downloads/         saída do cliente            (não versionada)
```

`servidor.py` e `cliente.py` **usam** `SocketUDP` por composição, não herdam dela —
nenhum dos dois *é* um socket, e o servidor usa dois ao mesmo tempo (o de escuta e
um por sessão).

## Requisitos

Python 3.9 ou superior. Nada além da biblioteca padrão — sem `pip install`.

```
python --version
```

## Uso

Gerar o arquivo grande de teste:

```
python gerar_arquivo.py arquivos/grande.bin 12
```

Servidor (porta obrigatoriamente > 1024):

```
python servidor.py --porta 5000 --raiz ./arquivos
```

Cliente, noutro terminal:

```
python cliente.py @127.0.0.1:5000/grande.bin --saida ./downloads
```

### Flags do cliente

| flag | efeito |
|---|---|
| `--descartar 100,101,205` | descarta esses números de sequência de propósito (perda determinística) |
| `--perda 0.03` | descarta 3 % dos segmentos ao acaso |
| `--modo sw` \| `janela` | Stop-and-Wait ou janela deslizante W=64 (padrão) |
| `--timeout 0.3` | segundos sem tráfego antes de pedir retransmissão |
| `--verbose` | log detalhado |

Em ambos os modos de perda o cliente imprime cada número de sequência descartado.

## Protocolo, em uma tela

Cabeçalho de 20 bytes, network byte order (`!` do `struct`):

```
magic(2) versão(1) tipo(1) sessão(4) seq(4) tam(2) crc32(4) flags(2)
```

Segmento de dados de 1024 B → datagrama de 1044 B, abaixo dos 1472 B que cabem numa
MTU de 1500 sem fragmentação IP.

| tipo | direção | conteúdo |
|---|---|---|
| `REQ_GET` | C → S | nome do arquivo |
| `META` | S → C | tamanho, total de segmentos, SHA-256 |
| `DATA` | S → C | até 1024 B do arquivo |
| `ACK` / `NACK` | C → S | última seq contígua / lista de faltantes |
| `ERR` | S → C | código + mensagem |
| `FIN` / `FIN_ACK` | S ↔ C | fim da transmissão |

Justificativas de cada escolha na seção 1 do [PLANO.md](PLANO.md).

## Estado

`protocolo.py` e `gerar_arquivo.py` prontos e testados. `servidor.py` e `cliente.py`
têm a linha de comando funcionando e a lógica de rede marcada como roteiro nos `TODO`.
Ordem de implementação na seção 3 do [PLANO.md](PLANO.md).
