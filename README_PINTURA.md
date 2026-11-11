# Controle de Produção — módulo Pintura / Monovia

O aplicativo chama-se **Controle de Produção**, com as três etapas no cabeçalho:
`PREPARAÇÃO BANHO | BANHO | PINTURA`. A etapa da tela aberta fica acesa (branca,
sublinhada em verde) e as outras ficam esmaecidas — assim o operador sabe onde
está sem ler o conteúdo. No celular só a etapa ativa aparece, por espaço.

A etapa é deduzida da rota (`_ETAPA_POR_ROTA` em `app.py`), não passada tela a
tela: uma tela nova entra no esquema sozinha. Telas de administração não acendem
nenhuma, porque não pertencem a uma etapa. No painel da gerência, que tem
cabeçalho próprio, a etapa acompanha o setor escolhido em Histórico & Análises.


Extensão do sistema de pré-banho e banho. `pintura.py` é um blueprint novo; o
`app.py` só recebeu ajustes pontuais. Desligando o módulo na configuração
(`ativo: false`), o sistema volta a se comportar exatamente como antes.

## 1ª fase (o que está ligado agora)

A pergunta desta fase é **o que entrou na linha para ser pintado no dia**. O ciclo
apontado vai da saída do banho até o fim do carregamento:

```
Banho → fila da pintura → operador escolhe monovia ou estacionária
      → carrega (cronômetro) → CESTO LIVRE para o pré-banho
```

O **descarregamento fica desligado** (`descarga_ativa: false`). Sem alguém
apontando a descarga, os lotes ficariam pendurados para sempre na tela e nada
encerraria; assim, o lote fecha no fim do carregamento e a coluna de descarga nem
aparece. Todo o código da descarga continua no lugar — repasse, tempo de forno,
rastreio por tempo na linha, KPIs. Para ligar:

```bash
curl -X POST https://SEU-APP/api/monovia/config \
  -H 'Content-Type: application/json' -d '{"descarga_ativa": true}'
```

Os módulos de câmera/CLP (`agente_monovia.py`, endpoints de telemetria) também
estão prontos e ociosos: sem telemetria, o operador informa os metros no fim da
carga e todo o resto funciona igual.

## Fluxo

```
Preparação → Banho → AGUARDANDO PINTURA → carregamento → NA LINHA → descarga → fim
                                              ↓
                                       cesto liberado aqui
```

O cesto é liberado **no fim do carregamento** (aí ele está vazio), mas o LOTE
continua vivo enquanto as peças estão na monovia. É isso que permite medir o
tempo de linha/forno e o tempo de descarregamento.

Ao sair do banho o cesto **não** volta para o pré-banho: fica "aguardando
pintura" e as peças viram um **lote**. O cesto só é liberado quando o operador de
pintura encerra um lançamento que esvazia o cesto.

## Modelo de dados

As peças passaram a ter vida própria fora do cesto, então duas entidades novas:

**LotePintura** — conjunto de peças (OPs + quantidades) aguardando pintura. Nasce
do cesto que saiu do banho ou de uma OP nova. `com_cesto = 0` quando as peças
foram para palete/arara: o cesto é liberado e o lote continua na tela do operador
como "peças sem cesto", identificadas pela OP e quantidade, mostrando de qual
cesto vieram.

**AtividadePintura** — um lançamento sobre um lote: tipo, operadores, início/fim,
paradas e observação. Mesma mecânica do pré-banho (parada não conta no tempo). Um
lote pode ter várias atividades em sequência — preparação → armazenamento →
carregar monovia — e é isso que dá o custo real de cada peça na pintura.

## Tela do operador de pintura

Mesma estrutura do banho: abas no topo com contador e colunas lado a lado.

| Aba | Coluna |
|---|---|
| Carregando | **Carregando agora** — quem está com o cronômetro rodando |
| Fila de espera | **Fila de espera da pintura** — todos os cestos que saíram do banho |
| Descarga | **Descarregamento** — o que está na linha e o que já chegou |

A fila de espera é uma só: cesto que saiu do banho e peças que ficaram em
palete/arara aparecem juntos, ordenados por quem espera há mais tempo, com a
posição ("1º na fila"). Peças sem cesto vêm marcadas com **S/C** e mostram de qual
cesto saíram e onde estão guardadas.

Cada card da fila traz **Monovia** e **Estacionária** lado a lado sob o rótulo
"Enviar para" — a decisão é do operador, ali. As outras quatro ações ficam em
"Outras opções".

O caminho de todo dia é igual ao do banho, em quatro toques:

```
[Carregar monovia] → [Iniciar agora] → ...carrega... → [Finalizar] → [Confirmar]
```

Os dois carregamentos ficam direto no card do cesto. As outras quatro ações
(preparação, armazenamento, OP nova, retrabalho) ficam em **Outras opções**, para
não atrapalhar o fluxo comum. Todas seguem o mesmo padrão: operadores →
cronômetro com paradas → observação.

| Ação | O que faz | Cesto ao final |
|---|---|---|

| Carregar monovia | operadores → cronômetro com paradas → observação | liberado |
| Carregar estacionária | idem | liberado |
| Carregar OP nova | destino (monovia/estacionária) → cadastro de OP sem cesto → operadores → cronômetro | não usa cesto |
| Preparação para pintura | Isolamento/Lixa/Limpeza/Outros → operadores → cronômetro → destino (armazenar, monovia ou estacionária) | liberado se armazenar |
| Armazenamento palete/arara | palete/arara/outro + justificativa → operadores → cronômetro | liberado, peças ficam sem cesto |
| Retrabalho | todas ou quantidade → precisa reposicionamento? | volta ao pré-banho/banho |
| Descarregar | aparece sozinho quando o tempo de percurso se cumpre | — |

### Descarregamento

Ao terminar o carregamento o lote vai para a coluna **Descarregamento**, mostrando
quanto falta e o ETA. Quando o tempo de percurso se cumpre — `offset da estação
÷ velocidade` depois do fim da carga — ele muda para **pronto** e ganha o botão. O operador dá iniciar e finalizar, como no
carregamento.

Sem telemetria, ou na estacionária, o lote fica pronto na hora — quem decide é o
operador ("Descarregar assim mesmo").

Daí saem dois indicadores novos: **tempo na linha** (do fim da carga ao início da
descarga — na prática o tempo de forno) e **tempo de descarregamento**.

De 1 a 3 operadores em cada etapa: o carregamento e a descarga são atividades
separadas, cada uma com os seus próprios operadores e tempo.

### Repasse — peças que não descem

Ao descarregar, o operador informa se **alguma peça não desceu**: desceu tudo,
parte volta ou tudo volta, com o motivo (falha de camada, escorrimento, casca de
laranja...). As peças de repasse ficam penduradas e dão outra volta na cabine.

O sistema marca o instante e espera **uma volta completa do trilho**
(`perimetro_m ÷ velocidade`, na configuração) antes de a peça reaparecer na
coluna de descarga, agora como "2º passe". Não há limite de passes.

Uma sutileza que vale conhecer: quando parte do lote volta, o lote encolhe. Se os
relatórios lessem o lote na hora de fechar o mês, o carregamento apareceria com o
número reduzido — o repasse reescreveria o passado. Por isso cada atividade grava
uma **foto** da quantidade e da área no momento em que aconteceu (`qtd_lote`,
`area_lote`), e os indicadores usam essa foto.

## Planilhas

| Botão (Administração) | Conteúdo |
|---|---|
| Excel pré-banho | inalterado |
| Excel banho | inalterado |
| **Excel pintura** | uma linha por lançamento: tipo, operadores, tempos, paradas, empilhamento, repasse |
| **Excel geral** | o ciclo inteiro do cesto, prep → banho → pintura → descarga |

### Tempos do ciclo na planilha geral

A linha do tempo de um cesto alterna trabalho e espera:

```
início prep ─[prep]─ fim prep ─(espera banho)─ início banho ─[banho]─ fim banho
   ─(aguardando pintura)─ [carga] ─(linha/forno)─ [descarga] ─ fim
```

Três colunas fecham a conta:

- **TOTAL do processo** — do início da preparação ao fim da descarga. Contado
  dentro da jornada, senão um cesto que atravessa o fim de semana marcaria 60 h.
- **TOTAL trabalhado** — só as etapas com operador: prep + banho + carga +
  preparação/armazenamento + descarga. É o que você pediu: sem as esperas.
- **TOTAL de espera** — a diferença. Cobre aguardando cadastro/banho, aguardando
  pintura e o tempo na linha.

Sempre vale `processo = trabalhado + espera` — há um teste que confere isso linha
a linha. "Aguardando cadastro" e "aguardando banho" são o mesmo intervalo no
sistema (fim da prep → início do banho), por isso aparecem numa coluna só.

## Painel da gerência — Histórico & Análises

Quatro setores: **Pré-banho**, **Banho**, **Pintura** e **Todos**.

Em **Todos** aparece o tempo médio de cada etapa do processo — preparação,
aguardando banho, banho, aguardando pintura, carregamento monovia, carregamento
estacionária — mais o **tempo médio total**, do início ao fim. O total só entra
na média de quem completou o ciclo; cesto em andamento não distorce o número.

A tabela é uma só, com **início do processo**, **fim do processo**, **total** e
**estado**. Enquanto o cesto não termina, o fim e o total ficam vazios e o estado
mostra onde ele está ("Aguardando pintura", "Na monovia"...). O botão **Ver**
abre o caminho completo: cada etapa com quem fez, quanto tempo levou, as paradas
e onde as peças foram parar.

## Indicadores

| Indicador | Como é calculado |
|---|---|
| Peças carregadas | soma das fotos das cargas |
| Área carregada | idem, em m² |
| **Área processada** | soma das áreas por passe — o repasse conta de novo |
| **Aproveitamento** | peças que desceram boas ÷ peças que chegaram na descarga |
| Taxa de repasse | 100% − aproveitamento |
| **Empilhamento** | peças/gancho, m²/metro linear |
| **Ocupação do trilho** | área ÷ (ganchos × área máxima por gancho) |
| Tempo na linha | do fim da carga ao início da descarga (forno) |
| Tempo de descarga | cronômetro da atividade de descarga |
| Paradas da monovia | detectadas pela telemetria, justificadas na tela |

### Monovia x estacionária

O destino é decisão do operador **no momento em que ele pega o cesto** — os dois
botões ficam lado a lado no card. Não há nada a configurar no cesto antes disso.

O painel da gerência mostra os dois separados: carregamentos, peças e minutos por
carga de cada destino. Metros de trilho e ocupação só aparecem na monovia, porque
a estacionária não tem trilho. A estacionária também fica pronta para descarregar
na hora (não há trajeto a esperar).

### No painel da gerência, ao vivo (atualiza a cada 5 s)

- Faixa **Pintado hoje**: peças e m² que entraram na linha no dia, cargas
  separadas por monovia/estacionária, quantos estão na fila e a maior espera.
- No bloco "Cestos ativos agora", duas abas novas — **Aguardando pintura** e
  **Carregando** — ao lado de preparando/banho, com o cronômetro correndo. A
  espera na fila conta só dentro da jornada, como no resto do sistema.

Os lotes da pintura entram na mesma lista dos cestos, traduzidos para o mesmo
formato de card. Peças sem cesto aparecem como **S/C**.

Tudo aparece no painel da gerência, na aba **Pintura** (ao lado de Pré-banho e
Banho), e no Excel `/api/download/pintura`. A aba some sozinha se o módulo estiver
desligado.

## Migração das tabelas do módulo

`Base.metadata.create_all` cria tabelas que faltam, mas **não** altera tabelas que
já existem. Colunas novas em `lotes_pintura` / `atividades_pintura` quebrariam a
saída do banho em produção — no Postgres a transação aborta e o operador vê erro
500. Por isso o boot compara os modelos com o schema real e cria o que faltar:

```
[pintura] coluna lotes_pintura.voltas criada.
[pintura] coluna atividades_pintura.qtd_lote criada.
```

Duas proteções a mais:

- **O banho fecha primeiro.** A saída do banho é confirmada antes de o lote ser
  criado, então nenhuma falha do módulo de pintura prende o operador do banho.
- **Reparo no boot.** Cesto que ficou em `FILA_PINTURA` sem lote (por causa de uma
  falha anterior) é recuperado e volta a aparecer na fila da pintura, liberando o
  número do cesto.

Escolhendo monovia ou estacionária ao fim da preparação, a tela já abre a seleção
de operadores do carregamento — sem voltar ao início.

## Retrabalho

**Com reposicionamento** → o cesto fica **AGUARDANDO MONTAGEM** na grade do
pré-banho. Ao clicar, o operador informa quantas pessoas e inicia o tempo de
montagem; daí segue o fluxo normal (parar → cadastro pré-preenchido e travado
como Retrabalho → fila do banho).

**Sem reposicionamento** → o cadastro pré-preenchido abre na hora, na própria tela
da pintura, e o cesto vai direto para a fila do banho.

Nos dois casos é criado um **cesto novo** vinculado ao original
(`card_origem_id`), em vez de reaproveitar o antigo — assim a primeira passagem
mantém seus tempos e o retrabalho tem um ciclo limpo.

**Retrabalho parcial:** as peças de retrabalho ficam no cesto (que volta ao
pré-banho) e o restante vira um lote sem cesto — por isso o sistema exige informar
onde o restante foi colocado. Se a sua operação faz o contrário (retrabalho sai e
o restante fica no cesto), me avise: é uma inversão pequena.

## Metros de trilho — velocidade × tempo

Não há odômetro. A régua é a **velocidade da monovia**, configurável em
Administração > Configurações (padrão **1,4 m/min**):

```
metros de trilho = velocidade (m/min) × tempo de carga (min)
```

A monovia anda enquanto o operador carrega, então o comprimento do lote é
exatamente quanto a linha andou nesse tempo. Com 1,4 m/min, 10 minutos de carga
são 14 m de trilho. Daí saem os ganchos, as peças por gancho e a ocupação.

Vale só para a **monovia**; a estacionária não tem trilho correndo, então não
acumula metros.

Pela mesma conta o sistema sabe **quando a peça chega em cada ponto da linha**:
`minutos = distância da estação ÷ velocidade`. A 1,4 m/min, a descarga a 90 m
fica a ~64 min do fim da carga. É isso que alimenta a coluna de descarga e o
rastreio por câmera, quando forem ligados.

**Troque a velocidade se a linha mudar de ritmo** — todos esses números dependem
dela. O valor aceita vírgula ou ponto.

Quem já tinha o módulo rodando guardava 3,5 m/min (o padrão antigo). Mudar o
padrão no código não alcança uma configuração já gravada, então o boot faz a
troca uma vez só, e avisa no log:

```
[pintura] velocidade da monovia: 3.5 -> 1.4 m/min (novo padrão).
```

Uma velocidade que você tenha editado na tela não é tocada.

## Empilhamento — sem precisar de câmera

Dos metros calculados acima mais o que já existe (quantidade e área do `area_peso.xlsx`):

| Métrica | Conta |
|---|---|
| Metros de trilho | `S_fim − S_ini` |
| Ganchos | `metros / passo_gancho_m` |
| Peças por gancho | `quantidade / ganchos` |
| m² por metro linear | `área / metros` |
| Ocupação % | `área / (ganchos × area_max_gancho_m2)` |

## Integração da fonte de dados

Um endpoint só, para CLP ou câmera:

```bash
curl -X POST https://SEU-APP/api/monovia/amostra \
  -H 'Content-Type: application/json' -H 'X-Monovia-Token: SEU_TOKEN' \
  -d '{"linha":"MV1","velocidade":1.4,"odometro":10234.5,"fonte":"clp"}'
```

`velocidade` em m/min, `odometro` em metros — pelo menos um dos dois. A ingestão
continua pronta, mas **hoje nada depende dela**: os metros saem de velocidade ×
tempo. Ligar a telemetria depois é trocar a régua estimada por uma medida.

`agente_monovia.py` roda num mini-PC na fábrica e faz isso sozinho, com buffer em
disco para quando a rede cair:

```bash
python agente_monovia.py --url https://SEU-APP --token SEU_TOKEN --fonte simulador
python agente_monovia.py --fonte clp --clp-ip 192.168.0.10 --registrador-encoder 100
python agente_monovia.py --fonte camera --px-por-metro 180 --medir-area
```

Para velocidade e área, visão clássica calibrada (fluxo óptico + subtração de
fundo) é mais precisa e leve que rede neural, e não exige dados de treino — é o
que o agente traz pronto. Rede treinada só compensa para classificar (gancho
vazio, defeito). Ordem sugerida: CLP primeiro, câmera como plano B, IA de
classificação depois, com as imagens que o próprio sistema já vai rotular
corretamente pelo tempo de percurso.

## Paradas da monovia

Velocidade abaixo de `parada_limiar_m_min` por mais de `parada_min_seg` vira
registro automático. Na tela `/monovia` o supervisor justifica cada uma. Daí saem
disponibilidade, MTBF e Pareto de motivos.

## Usuários

Logins de fábrica (criados só quando o banco está vazio):

| Login | Senha | Perfil | Vê |
|---|---|---|---|
| `admin` | `admin123` | admin | tudo |
| `op1` … `op6` | `op1234` | prep | pré-banho |
| `banho` | `banho123` | banho | banho **e pintura** |
| `pintura` | `pintura123` | pintura | pintura e monovia |
| `gerencia` | `painel123` | painel | painel da gerência |

**Troque essas senhas** em Administração > Usuários antes de usar em produção.

O **admin sempre vê todos os painéis** na tela de escolha, mesmo que o cadastro
dele seja anterior ao módulo — o `login_required` já o deixava entrar em qualquer
tela, e a lista agora acompanha isso. Para os demais usuários, marque **Pintura**
em Administração > Usuários.

Num banco que já existia antes do módulo, o seed acima não roda — ninguém teria
acesso à tela de pintura. Por isso o boot cria o login `pintura` (senha
`pintura123`) uma única vez, e só se nenhum usuário tiver esse acesso. O log avisa:

```
[usuarios] criado o login "pintura" (senha: pintura123). TROQUE A SENHA...
```

O perfil `banho` também alcança `/pintura`, para operação enxuta em que a mesma
pessoa cuida das duas etapas. Se quiser separar, tire `'banho'` dos decoradores
`_lr(...)` em `pintura.py`.

Operadores com perfil `pintura` aparecem na lista de seleção de operadores das
atividades, junto com os de `prep`. Um usuário pode ter vários acessos marcados
(ex.: prep + pintura); no login ele escolhe em qual painel entrar.

## Configuração

`GET/POST /api/monovia/config` (admin):

```json
{
  "ativo": true, "linha_padrao": "MV1",
  "linhas": [{
    "id": "MV1", "nome": "Monovia 1",
    "passo_gancho_m": 0.40, "area_max_gancho_m2": 1.20,
    "velocidade_nominal_m_min": 1.4,
    "estacoes": [
      {"id": "cam1", "nome": "Câmera", "offset_m": 12.0},
      {"id": "cabine", "nome": "Cabine", "offset_m": 25.0},
      {"id": "estufa", "nome": "Estufa", "offset_m": 45.0},
      {"id": "descarga", "nome": "Descarga", "offset_m": 90.0}
    ]
  }],
  "parada_limiar_m_min": 0.10, "parada_min_seg": 20,
  "guardar_amostra_seg": 5, "token_ingestao": ""
}
```

**A velocidade é o número mais importante** (editável na tela de configurações):
dela saem os metros, os ganchos e a ocupação. Os `offset_m`, medidos com trena
seguindo o trilho, só entram quando a descarga for ligada. Token: defina `MONOVIA_TOKEN` no Railway.

## Endpoints

| Rota | Perfil | O que faz |
|---|---|---|
| `/pintura` | pintura, banho | tela do operador |
| `/monovia` | pintura, painel | painel ao vivo |
| `GET /api/pintura/painel` | pintura | cestos, peças sem cesto, atividades |
| `GET /api/pintura/lote/<id>` | pintura, painel | detalhes, cesto de origem e histórico |
| `POST /api/pintura/atividade/iniciar` | pintura | inicia um lançamento |
| `POST /api/pintura/atividade/pausar` | pintura | lança/encerra parada |
| `POST /api/pintura/atividade/finalizar` | pintura | encerra, aplica destino |
| `POST /api/pintura/op_nova` | pintura | OP avulsa sem cesto |
| `POST /api/pintura/retrabalho` | pintura | manda peças ao pré-banho |
| `POST /api/pintura/retrabalho/cadastrar` | pintura | confirma cadastro → fila do banho |
| `POST /api/prep/montagem/iniciar` | prep | inicia o tempo de montagem |
| `POST /api/monovia/amostra` \| `/amostras` | token | telemetria |
| `POST /api/monovia/deteccao` | token | evento da câmera, já vinculado ao lote |
| `GET /api/monovia/estado` | logado ou token | velocidade e rastreio na linha |
| `GET /api/monovia/paradas` | pintura, painel | paradas do período |
| `GET /api/pintura/dados` | pintura, painel, admin | KPIs |
| `GET /api/download/pintura` | admin | Excel do setor |

## Banco

Tabelas novas: `lotes_pintura`, `atividades_pintura`, `monovia_estado`,
`monovia_amostras`, `monovia_paradas`, `monovia_deteccoes`. Colunas novas em
`cards` (migração automática no boot). Com `guardar_amostra_seg = 5` são ~17 mil
linhas/dia de telemetria — mas só se você ligar o agente; hoje nada é gravado ali.

## Correções no código existente

1. O dashboard e o export de banho filtravam por `estado == CONCLUIDO`. Como o
   cesto agora fica na pintura depois do banho, os banhos do dia sumiriam do
   painel. Passou a filtrar por `banho_fim IS NOT NULL`.
2. O dataset de pré-banho ganhou os estados novos, pelo mesmo motivo.
3. `Session` é `scoped_session`: `Session()` devolve a **mesma** sessão da thread.
   Por isso nenhum helper do módulo chama `db.close()` — se chamasse, fecharia a
   sessão de quem chamou e as alterações pendentes se perderiam em silêncio.

## Teste

```bash
python teste_pintura.py
```

281 verificações cobrindo os seis lançamentos, retrabalho total e parcial,
liberação do cesto, paradas, rastreio, export, o painel da gerência e as travas
contra cesto duplicado. Use um banco de teste (ela apaga os lotes da pintura).

## O que falta decidir

1. **Retrabalho parcial:** hoje as peças de retrabalho ficam no cesto e o restante
   vai para palete/arara. Confirmar se é assim na prática.
2. **Capacidade de cestos:** com a pintura no caminho, os cestos ficam ocupados
   por mais tempo. Vale medir se os 20 bastam no pico.
3. `passo_gancho_m` e `area_max_gancho_m2` reais da linha.
4. Uma monovia só ou mais de uma? (a estrutura já é multi-linha)
5. A estacionária precisa de rastreio/telemetria própria, ou só tempo?

## Juntar o banco de teste com o de produção

Tudo roda num único `DATABASE_URL` — as tabelas de pintura e a `cards` precisam
estar no mesmo banco, porque o módulo lê e escreve o cesto o tempo todo. Um banco
separado serve como **ambiente de teste**; "juntar" é apontar o `DATABASE_URL`
para o banco de produção.

Na virada:

1. **Não rode as duas versões contra o mesmo banco ao mesmo tempo.** A versão
   antiga não conhece `FILA_PINTURA`, então mostraria como *livre* um cesto que
   está na pintura, e o pré-banho abriria um cesto duplicado. O corte tem que ser
   completo.
2. **Faça backup antes** (`/api/admin/backup`).
3. As tabelas novas e as colunas são criadas no boot. O índice único de cestos
   ativos é recriado sozinho (veja abaixo).
4. **Reconfigure a monovia em produção**: `offset_m` das estações,
   `passo_gancho_m`, `area_max_gancho_m2` e o token ficam na tabela `config` do
   banco de teste e não vão junto.
5. Cestos que estiverem em banho no momento da virada seguem normalmente: ao
   finalizar o banho já caem em "aguardando pintura".

### O índice único parcial

`ux_cards_num_ativo` impede dois cestos ativos com o mesmo número. Ele é um índice
**parcial** — o `WHERE` lista os estados um a um. Como o módulo de pintura
acrescentou quatro estados, o `CREATE UNIQUE INDEX IF NOT EXISTS` original não
resolvia: vendo o nome já existente, ele não fazia nada, e o índice continuaria
valendo só para os estados antigos — em silêncio, com o log dizendo "OK".

O `_criar_indice_unico_ativos()` agora compara a definição gravada no banco com a
lista atual de `ESTADOS_ATIVOS` e recria o índice quando falta algum estado. Vale
para Postgres e SQLite. No boot sobre um banco antigo aparece:

```
[indice] índice desatualizado (sem FILA_PINTURA, ...) — recriando.
[indice] índice único de cestos ativos OK.
```

Se você acrescentar outros estados no futuro, isso volta a se resolver sozinho.

## Tela sempre acesa

O tablet/TV do chão de fábrica não pode apagar enquanto o sistema está aberto.
`static/wakelock.js` cuida disso em todas as telas (via `base.html`, e no painel
da gerência, que tem HTML próprio).

São três camadas, porque nenhuma cobre todos os aparelhos sozinha:

1. **Screen Wake Lock API** — o caminho oficial. Exige **HTTPS** (no Railway já
   é) ou `localhost`; em HTTP comum o navegador recusa.
2. **Vigia a cada 20 s** — o sistema pode soltar o bloqueio por conta própria
   (bateria fraca, economia de energia) sem avisar. Sem alguém conferindo, a
   tela volta a apagar e ninguém percebe. O vigia repõe.
3. **Vídeo mudo de 1 px em loop** — para navegadores sem a API (Safari antigo,
   WebView). Um vídeo tocando segura a tela acesa na prática.

Um ícone de lâmpada na barra do topo mostra o estado: **verde** quando a tela
está travada acesa, **âmbar** quando não foi possível. Passando o mouse, o texto
explica. Para diagnóstico, `window.wakeLockInfo()` no console do navegador
devolve o modo em uso, se a API existe e se o contexto é seguro.

O bloqueio só vale com a aba **visível** — essa é uma regra do navegador, não do
sistema: ao minimizar ou trocar de aba ele cai, e volta sozinho quando a tela
reaparece.

**O que isto não resolve:** bloqueio de tela por política do Windows/Android
(senha após X minutos) e o botão de desligar do aparelho. Se mesmo assim apagar,
vale tirar a suspensão nas configurações de energia do próprio aparelho.

## Validação completa (v17) — o que foi encontrado e corrigido

Validação feita com navegador de verdade (clicando em todos os botões das cinco
telas), com a suíte de testes e com um teste de migração em PostgreSQL rodando
dois processos, como no Railway.

### Cestos duplicados — eram sete causas

Cada cesto que sai do banho vira um **lote** na fila da pintura. As duas tabelas
(cestos e lotes) podiam se desencontrar; o sintoma era sempre cesto repetido ou
"fantasma" na fila da pintura e no painel.

| Causa | Correção |
| --- | --- |
| Painel da gerência mostrava o cesto **e** o lote dele (o mesmo número duas vezes, contagem inflada) | `_ativos_sem_repeticao`: cada coisa uma vez |
| Cancelar no pré-banho um cesto que já tinha saído do banho deixava o lote na fila; preparado de novo, o número aparecia 2x | o pré-banho não cancela mais cesto que está na pintura |
| Excluir/alterar o cesto pelo administrador (e o reset em massa) não mexia nos lotes | `reconciliar_lotes()` roda depois de cada uma dessas operações; o reset passa a incluir a pintura |
| Dois toques simultâneos em "sair do banho" criavam dois lotes | troca de estado atômica + índice único `ux_lote_card_vivo` |
| Dois toques simultâneos em "iniciar" abriam várias atividades no mesmo lote | troca de estado atômica (`_transicao`) |
| Lote **já pintado** podia ser carregado de novo por um tablet com a tela velha (contava 2x no "pintado hoje") | `iniciar` só aceita lote na fila |
| Retrabalho aceitava lote em carga ou já pintado e criava um cesto novo | `retrabalho` só aceita lote na fila |

`reconciliar_lotes()` também roda no boot e **arruma o que já está no banco**:
lote duplicado ou órfão fica `FINALIZADO` com destino `CANCELADO` (nada é
apagado) e sai de todas as contas. O resumo aparece no log:
`[pintura] reconciliação: {'cancelados': 2, 'duplicados': 10, ...}`.

### Dois processos no servidor

O Railway sobe `gunicorn --workers 2`. Três problemas vinham daí:

- **inicialização em paralelo**: os dois processos criavam tabelas/índices ao
  mesmo tempo (num banco novo um deles caía). Agora há uma trava do PostgreSQL
  (`pg_advisory_lock`) em volta de `init_db()`;
- **configuração**: o cache de configuração não vencia, então só o processo que
  salvou enxergava a mudança (quantidade de cestos, velocidade, jornada). Agora
  vence em 10 s (`_CacheCurto`) e é lido por conexão própria (`_ler_config`),
  sem tocar na sessão do pedido em andamento;
- **arquivos estáticos**: CSS/JS agora saem com `?v=<data do arquivo>`, para o
  tablet não ficar com JavaScript antigo depois de uma atualização.

### Tela de pintura

- as abas (Fila, Carregando monovia, …) davam erro de JavaScript e não filtravam;
- "Carregar OP nova" abandonada no meio fazia o próximo "Monovia" de um cesto
  criar uma OP fantasma em vez de carregar o cesto;
- "Lançar parada" pelo card abria duas janelas empilhadas;
- OP nova na estacionária oferecia "mudar para estacionária";
- "Voltar" dos detalhes caía no menu de ações de um lote em carga;
- toque repetido em Iniciar/Confirmar gerava alerta de erro;
- falha de rede deixava o botão "morto" (agora avisa).

### Painel da gerência

- cestos duplicados em "Cestos ativos agora" e contagem errada;
- tabelas de Pré-banho e Banho desalinhadas (14 colunas num cabeçalho de 13) e
  com estado cru (`FILA_PINTURA`);
- erro de JavaScript nas abas Pintura e Todos (gráficos ficavam com dado velho);
- o período selecionado não aparecia; gráfico de área alto demais;
- indicadores da pintura com rótulo de banho; "Ver" da pintura abria vazio.

A tabela agora é uma só, com cabeçalho por setor: início/fim/tempo **da etapa**
em Pré-banho, Banho e Pintura, e do **processo inteiro** em Todos.

### Pré-banho e banho

- **Ver detalhes em cada cesto** (botão "Detalhes" abaixo de cada número):
  OPs, peças, área, peso, todas as etapas com início/fim/tempo e operadores, e
  onde as peças estão agora — inclusive na pintura. Cesto livre mostra o último
  uso. Endpoint: `GET /api/cesto/<numero>/detalhes`;
- cestos na pintura e aguardando montagem ganharam cor e legenda;
- no banho, erro ao iniciar/finalizar era ignorado em silêncio (a janela fechava
  como se tivesse dado certo).

### Administração

- a edição de cesto não tinha os estados da pintura; "Somente ativos" escondia
  os cestos que estavam na pintura;
- o backup não incluía a pintura (agora inclui lotes e atividades, e a
  restauração acerta os contadores de id do PostgreSQL).

### Como repetir a validação

    python teste_pintura.py                       # 281 verificações

A suíte roda em SQLite e em PostgreSQL (`DATABASE_URL=postgresql://...`). Use um
banco de teste: ela apaga os lotes da pintura ao começar e ao terminar.

## v18 — quadrados na pintura e registro de velocidade

### Pré-banho

O olho de detalhes fica **dentro do quadrado**, no canto superior esquerdo (onde
ficava o selo com a quantidade de operadores). O botão "Detalhes" de baixo saiu.
Tocar no olho abre os detalhes; tocar no resto do quadrado abre o cesto.

### Pintura em quadrados

A tela mostra os cestos em quadrados, iguais aos do pré-banho, em cinco seções:
Aguardando pintura, Carregando monovia, Carregando estacionária, Preparação para
pintura e Armazenamento. Cada quadrado traz o número (ou `S/C`), a situação, a
OP, as peças e o tempo. As abas de cima filtram por seção.

- **tocar num cesto da fila** abre a janela para destinar: Monovia e
  Estacionária em destaque; Preparação, Palete, Arara e Retrabalho logo abaixo;
- **tocar num cesto em atividade** abre o cronômetro: Lançar parada/Retomar,
  Finalizar, Mudar para monovia/estacionária e Ver detalhes;
- o **olho** abre só os detalhes e o histórico.

O módulo de descarga (desligado) continua em cards.

### Velocidade da monovia

- o campo "Monovia — sem leitura" saiu. Fica a **velocidade configurada**, com o
  botão **Alterar**, que pede a nova velocidade e **o nome de quem está
  alterando**. A barra mostra quem fez a última alteração e quando;
- cada alteração (pela produção ou pelo administrador) vai para a tabela
  `monovia_velocidade_log`: data, hora, quem, login, de quanto para quanto;
- cada carga na monovia grava a velocidade do **instante em que começou**
  (`atividades_pintura.vel_m_min`). Trocar a velocidade no meio **não** muda a
  carga em andamento: os metros dela continuam `tempo × velocidade do início`;
- ao trocar uma carga de estacionária para monovia no meio, vale a velocidade
  que estava configurada quando a carga começou (pelo histórico);
- na primeira subida, as cargas antigas recebem a velocidade vigente
  (`congelar_velocidades`), para que alterações futuras não mudem o passado.

Endpoints: `GET/POST /api/pintura/velocidade` (perfil pintura) e
`GET /api/download/velocidade` (só administrador).

### Onde o registro aparece

| Lugar | O que mostra |
| --- | --- |
| Dashboard do administrador → **Excel velocidade monovia** | abas "Alterações de velocidade" e "Velocidade por cesto" |
| Administrador → Monovia | últimas alterações + botão de baixar |
| **Excel geral** | coluna "Velocidade monovia (m/min)" por cesto + as duas abas |
| Excel pintura | coluna "Velocidade monovia (m/min)" + as duas abas |
| Painel da gerência | **nada** — nem velocidade, nem quem alterou |
