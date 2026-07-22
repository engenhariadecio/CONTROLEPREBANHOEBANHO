# Controle de Produtividade — Pré-Banho e Banho

## Ajustes v19
- Painel "Histórico & análises" totalmente DIVIDIDO por setor: seletor Pré-banho / Banho
  no topo controla TODOS os indicadores, gráficos e a tabela ao mesmo tempo.
  • Pré-banho: cestos cadastrados (na fila, em banho ou concluídos), contados pela
    finalização da PREPARAÇÃO e turno da prep -> IGUAL ao Excel pré-banho.
  • Banho: cestos concluídos, contados pela finalização do BANHO e turno do banho ->
    IGUAL ao Excel banho.
- Horários dos turnos agora vêm da jornada (foto): 1º 06:01–15:29, 2º 15:30–00:40,
  3º 00:41–06:00. O que acontece após a meia-noite (até 00:40) conta no 2º turno.
- Filtros (período + turno) valem para cada setor pelo seu próprio horário. Botão "Ver"
  em cada cesto. _coletar_dados reescrito e limpo (um bloco de métricas por setor).
## Ajustes v18
- Histórico do painel agora tem DUAS ABAS no final: "Pré-banho" e "Banho", cada uma
  com contador de cestos.
- O filtro de turno passou a valer para CADA SETOR pelo seu próprio horário:
  • Aba Pré-banho: cestos cuja PREPARAÇÃO foi finalizada no turno/período (turno da prep).
  • Aba Banho: cestos cujo BANHO foi concluído no turno/período (turno do banho).
  Ex.: ao filtrar o 2º turno, a aba Pré-banho mostra o que foi preparado no 2º turno e a
  aba Banho mostra o que foi banhado no 2º turno — são setores diferentes e contam
  diferente. Cada aba tem botão "Ver" com os detalhes do cesto.

## Ajustes v17
- Turno agora é calculado pelos HORÁRIOS CONFIGURADOS na jornada (não mais fixos no
  código). Corrige o caso do 2º turno até 00:40: os minutos após a meia-noite passam a
  contar no turno certo. Vale para painel e Excel.
- Removida a comparação por turno do painel (tabelas "Produção por turno" e o gráfico).
  O turno agora é apenas FILTRO. O histórico mostra o turno de prep e o de banho de cada
  cesto.
- Excel agora FILTRA POR DATA (antes baixava o histórico inteiro) e pelo turno certo de
  cada relatório: pré-banho pela finalização da preparação (prep_fim) e turno da prep;
  banho/geral pela finalização do banho e turno do banho. Assim a contagem bate com o
  painel. Cada planilha traz TOTAL DE CESTOS e TOTAL DE PEÇAS para conferência.
- Histórico do painel: novo botão "Ver" em cada cesto (detalhes completos: OPs, tempos,
  horários, turnos e operadores). Colunas separadas de Turno prep e Turno banho.

## Ajustes v16
- OEE removido do sistema (painel e configuração).
- CORRIGIDO o bug da "média exorbitante que depois volta". Causa raiz: no carregamento
  do painel, a primeira busca de dados disparava ANTES de a linha do tempo aplicar o
  filtro do dia — então a média saía de TODO o histórico (incluindo cestos deixados
  ligados por dias) e, segundos depois, o filtro entrava e o número voltava ao normal.
  Agora o filtro do dia já vem preenchido pelo servidor no 1º render.
- Proteção contra respostas fora de ordem: uma busca lenta não sobrescreve mais o
  resultado de uma busca mais recente (números "piscando").
- O painel ao vivo passou a usar um endpoint leve (/api/painel/ativos) em vez de
  recalcular todo o histórico a cada 5 segundos.
- Médias robustas: registros com tempos negativos/inválidos ou com horários invertidos
  (banho antes do fim da preparação) são ignorados no cálculo.

## Ajustes v15
- Painel gerencial enxuto: removidas as tabelas "Produção diária por turno" (pré-banho
  e banho) e "Produção por operador". Ficaram apenas "Produção por turno — Pré-banho"
  e "Produção por turno — Banho".
- NOVO: OEE do pré-banho e do banho (Disponibilidade × Performance × Qualidade).
  • Disponibilidade = tempo rodando ÷ tempo planejado (jornada, já sem PARADAS).
  • Performance = (tempo padrão × cestos) ÷ tempo rodando.
  • Qualidade = cestos normais ÷ total (retrabalho = perda).
  Parâmetros em Admin -> Jornada: tempo padrão por cesto (prep e banho), nº de postos
  de preparação e nº de tanques de banho, e liga/desliga do OEE.

## Ajustes v14 (auditoria completa do painel gerencial)
Corrigidos 3 erros que faziam o painel mostrar produção errada por turno:
1. Cestos com a preparação concluída mas AGUARDANDO CADASTRO (estado PREENCHER) não
   entravam na produção do pré-banho — subcontagem, principalmente no fim do turno.
   Agora entram (contados pelo horário da preparação).
2. O gráfico "Concluídos por dia" (e peso/área por dia) ordenava os dias como texto
   ("01/07" antes de "30/06"). Agora ordena cronologicamente por data.
3. "Produção por operador" creditava só o 1º operador do cesto e era filtrada pelo
   turno do BANHO. Agora credita TODOS os operadores (até 4) e usa o turno da
   PREPARAÇÃO, incluindo cestos ainda não banhados.

## Ajustes v13 (correção da produção por turno)
- CORRIGIDO o cálculo de "o que foi produzido em cada turno". Antes todas as tabelas
  saíam de um único conjunto (cestos concluídos, filtrados pela DATA e TURNO do BANHO),
  então a produção do PRÉ-BANHO ficava amarrada ao evento do banho — contava/datava
  errado e ignorava cestos preparados que ainda não foram banhados.
- Agora cada etapa tem seu próprio conjunto, pelo evento e data corretos:
  • Pré-banho: conta pela PREPARAÇÃO (horário/dia da prep) e inclui cestos na fila e
    em banho, não só os concluídos.
  • Banho: conta pelo BANHO concluído (horário/dia do banho).
  As tabelas de "Produção por turno" e "Produção diária por turno" mostram sempre os
  3 turnos e NÃO dependem do filtro de turno (só do período), refletindo fielmente a
  produção de cada turno. Cada tabela tem linha de Total (a conta fecha).

## Ajustes v12
- Painel gerencial: as OPs agora aparecem por completo. No histórico e na tabela do
  admin, todas as OPs do cesto são listadas (antes só a 1ª + contagem); nos cards ao
  vivo, aparecem os números das OPs (ex.: "OPs: 111, 222").
- Nova seção "Produção diária por turno" — Pré-banho e Banho separados: mostra, por
  dia, quantos cestos em cada turno (1º/2º/3º), pelos horários corretos dos turnos.
  Cada tabela tem linha de Total e a conta fecha.

## Ajustes v11
- Painel gerencial: o comparativo por turno foi DIVIDIDO em dois — "Produção por
  turno (Pré-banho)" e "Produção por turno (Banho)". Antes a preparação (feita em
  outro turno) era somada no turno do banho e a conta não fechava. Agora cada cesto
  é contado no turno em que a etapa aconteceu; cada tabela tem linha de TOTAL e o
  gráfico compara pré-banho × banho por turno.
- Turno sempre pelo HORÁRIO (nunca pelo operador), com o horário atual como base
  quando faltar data. Excel: pré-banho usa o turno da prep, banho usa o turno do
  banho e o relatório geral traz as duas colunas (Turno pré-banho e Turno banho).
- Mantida a lista_mestra.xlsx enviada por você.

## Ajustes v10
- Busca por código no cadastro: quando a OP não está na lista mestra, agora dá para
  digitar o código e ele PUXA a descrição e a quantidade automaticamente, mesmo com a
  OP já preenchida (não precisa mais apagar a OP). Busca tolerante a zeros à esquerda.
- Login: novo ícone de olho para mostrar/ocultar a senha antes de entrar.

## Ajustes v9
- Tipo (Normal/Retrabalho) agora é POR OP, não por cesto. No cadastro do pré-banho,
  cada OP tem um seletor Normal/Retrabalho (padrão Normal); ao marcar Retrabalho,
  aparece o motivo daquela OP (Pó de fosfato / Óleo nas peças / Sujeira / Outros).
- O cesto é considerado Retrabalho se QUALQUER OP for de retrabalho; o motivo do
  cesto reúne os motivos por OP. Detalhes (gerência/banho) e Excel mostram o tipo/
  motivo de cada OP. Cestos antigos continuam válidos (a OP herda o tipo do cesto).

## Ajustes v8
- Usuário com MÚLTIPLOS ACESSOS: no cadastro (Admin › Usuários) marque os painéis
  que a pessoa pode usar (Pré-banho, Banho, Gerência, Admin). Uma pessoa só, um login.
- No LOGIN, se o usuário tiver mais de um acesso, aparece a tela "Escolha o painel"
  e ele entra no que quiser. Com um acesso só, entra direto.
- Botão "Trocar painel" no topo para quem tem vários acessos (troca sem sair).
- Acessos de usuários já cadastrados podem ser editados na própria lista.

## Ajustes v7
- Textos padronizados no fluxo: Preparando cesto → Aguardando cadastro →
  Aguardando banho → Em banho (pré-banho, banho, painel de gerência e admin).
- Pré-banho: no cadastro de cesto de RETRABALHO há o campo "Motivo do retrabalho"
  (Pó de fosfato / Óleo nas peças / Sujeira / Outros). Aparece nos detalhes e no Excel.
- Painel de gerência (ao vivo): ordem por processo (Preparando cesto, Aguardando
  cadastro, Aguardando banho, Em banho).
- Banho: abas renomeadas (Todos / Em banho / Aguardando banho).
- Admin › Dashboard: removido o botão "Painel de gerência" (já há o ícone dele).
- Admin › Jornada: jornada padrão em 3 TURNOS (1º 06:01–15:30 e 2º 15:31–00:00
  seg–sex; 3º 00:01–06:00 seg–sáb) e lançamento de EXPEDIENTES FORA DO PADRÃO
  (TURNO EXTRA ou PARADA) com data, horário e justificativa.
- Admin › Manutenção de cestos: relatório de AUDITORIA (antes/depois) de cada
  edição/exclusão, por cesto ou geral; e correção do texto de aviso.

## Ajustes v6
- Pré-banho: até 4 operadores. No "Sim, escolher nomes" há um campo para DIGITAR
  um nome fora da lista (operador manual) e chips mostrando os selecionados.
  Ao iniciar, a aba do cesto FECHA automaticamente (o cronômetro segue no card).
- Banho: os cestos da fila de espera agora têm o MESMO layout dos "em banho"
  (tempo de espera como cronômetro inline). O botão "Iniciar banho" continua igual.

## Ajustes v5
- Pré-banho: após escolher 1, 2 ou 3 operadores, aparece a caixa
  "Deseja selecionar os operadores?".
  • "Sim, escolher nomes" abre a lista de operadores de preparação (perfil
    "prep"), com NOME COMPLETO e MATRÍCULA (o login é a matrícula). O líder marca
    quem está no cesto.
  • "Não, iniciar direto" registra no nome do líder logado.
- Banho: sempre 1 operador — puxa automaticamente o operador logado (nome + matrícula).
- Painel de Gerência: no "Ver detalhes" aparecem os operadores da preparação e do
  banho com nome + matrícula. Nos relatórios Excel, a coluna "Operador Prep" traz
  nome + matrícula de todos.
- Usuários: campo renomeado para "Matrícula (login)". Cadastre os operadores com
  perfil "Operador de preparação" para aparecerem na seleção.

## Ajustes v4
- Painel de Gerência com MENU de duas visões:
  • "Painel ao vivo" — cestos em tempo real por categoria;
  • "Histórico & análises" — filtros, KPIs, gráficos (inclui Cestos por turno),
    produção por turno, produção por operador e a tabela completa de histórico.
- Filtro de turno agora é MÚLTIPLA seleção (marque 1, 2 ou os 3 turnos).
- Linha do tempo já abre com a DATA DE HOJE marcada como padrão.

## Ajustes v3
- Turnos: 1º 06:01–15:30 | 2º 15:31–00:00 | 3º 00:01–06:00.
  Filtro por turno no Painel de Gerência e no Admin; coluna "Turno" nos
  relatórios Excel (pré-banho, banho e geral). O botão de Excel respeita o
  turno/período selecionados.
- Painel de Gerência: linha do tempo Ano/Mês/Dia agora com seleção por
  ARRASTAR (mouse e toque) com rolagem automática nas bordas; menu de
  categorias (Todos/Em banho/Fila/Preparando/Preenchendo) para entrar em cada
  etapa; todos os botões "Ver detalhes" na cor da logo; layout mais estruturado
  e 100% responsivo (celular/tablet).
- Painel Admin: identidade da marca (KPIs, seções e botões verde→azul).

## Ajustes desta versão
- Login continua ativo por 30 dias e em VÁRIOS dispositivos ao mesmo tempo
  (cada aparelho tem seu login; um não desloga o outro). A chave de sessão é
  guardada no banco, então redeploys no Railway não deslogam ninguém.
- Preparação: o cronômetro abre já em 00:00 ao iniciar.
- Banho: números e tempos na cor da logo (verde→azul); tempo "Esperando" grande;
  botões Iniciar/Sair em evidência e "Ver detalhes" menor.
- Painel de Gerência: 100% responsivo (celular/tablet), linha do tempo estilo
  Excel com níveis Ano / Mês / Dia (rolagem lateral, não quebra o layout) e
  gráficos com visual profissional na identidade da empresa.
- Painel Admin: mesma identidade visual da marca.

# Controle de Produtividade — Pré-Banho e Banho (Pintura Eletrostática)

Monitoramento em tempo real do tratamento de peças (pré-banho e banho):
grade de cestos, leitura da OP por coletora, pausa de tempo, múltiplas OPs
por cesto, 1 a 3 operadores, dashboards, painel da gerência e exportação Excel.
Roda 24h no Railway com PostgreSQL. Otimizado para tablet (Samsung Galaxy Tab A9).

==================================================================
NOVIDADES DESTA VERSÃO
==================================================================
1. PAINEL COM LOGIN: o painel da gerência agora exige usuário e senha
   (perfil "Gerência" — só visualização). Não há mais senha única.
2. CESTOS ATIVOS POR CATEGORIA: no painel, os cestos ativos aparecem
   agrupados por etapa (Em banho, Fila do banho, Preparando, Preenchendo).
3. FIM DA DUPLICAÇÃO: índice único impede dois cestos ativos com o mesmo
   número; duplicados antigos são reorganizados automaticamente no 1º deploy.
4. TELA DO BANHO MELHORADA: cada cesto mostra número, processo, Normal/
   Retrabalho e OP, com botão "Ver detalhes" (todas as infos do cesto).
5. JORNADA DE TRABALHO (trava o tempo): o tempo de espera na fila só conta
   dentro do expediente. Fora dele (noite, fim de semana, feriado) o relógio
   CONGELA. Configurável em Admin → Configurações (engrenagem).

==================================================================
IMPORTANTE: como subir sem erro
==================================================================
1. Os arquivos ficam na RAIZ do repositório. O app.py precisa aparecer
   DIRETO na página do repo, não dentro de uma pasta.
2. NÃO adicione nixpacks.toml. O Railway/Nixpacks instala as dependências
   sozinho pelo requirements.txt.
3. O comando de start usa 'python -m gunicorn' (Procfile e railway.json).

==================================================================
Deploy no Railway (como na sua imagem: Postgres + web)
==================================================================
1. Suba TODOS estes arquivos na RAIZ do repo no GitHub.
2. railway.app → New Project → Deploy from GitHub repo → escolha o repo.
3. New → Database → Add PostgreSQL (o serviço "Postgres" da sua imagem).
4. No serviço "web", aba Variables, adicione:
      DATABASE_URL = ${{Postgres.DATABASE_URL}}
      SECRET_KEY   = (uma frase longa qualquer)
   >>> A variável DATABASE_URL é OBRIGATÓRIA. Sem ela os dados se perdem a
       cada deploy (o app cai para SQLite temporário e avisa no log).
5. Settings → Networking → Generate Domain para a URL pública.

Os dados (cestos, usuários, configuração de jornada) ficam no PostgreSQL e
NÃO se perdem em reinício/redeploy/alteração do GitHub.

==================================================================
Usuários padrão (criados na 1ª execução — TROQUE as senhas)
==================================================================
  admin    / admin123    -> Administrador (dashboard, usuários, jornada, Excel)
  banho    / banho123    -> Operador de banho
  gerencia / painel123   -> Gerência (painel — só visualização)
  op1..op6 / op1234      -> Operadores de preparação

Crie/edite usuários em Admin → Usuários. Perfis disponíveis:
preparação, banho, gerência (painel) e administrador.

==================================================================
Fluxo de uso
==================================================================
1. Preparação: toca num cesto livre → escolhe 1 a 3 operadores → Iniciar
   (cronômetro começa). Pode Pausar (café/ginástica — não conta) e Retomar.
2. Parar tempo (cronômetro congela). Depois preenche: Adicionar OP (várias
   por cesto; cada uma puxa código/descrição/qtd da lista mestra), processo,
   tipo (Normal/Retrabalho) e conclui → vai para a fila do banho.
3. Banho: operador vê número, processo, tipo e OP; "Ver detalhes" abre tudo.
   Inicia e finaliza o banho. O cesto volta a ficar livre.
4. Qualquer card pode ser editado depois (tocar no cesto ocupado).

==================================================================
Jornada de trabalho (Admin → Configurações)
==================================================================
- Liga/desliga a jornada (se desligada, o tempo conta corrido 24/7).
- Define hora de início e fim do expediente.
- Marca se trabalha aos sábados e/ou domingos.
- "Dias extras": um sábado/domingo pontual que SERÁ trabalhado.
- "Feriados/folgas": dias úteis que NÃO serão trabalhados.
O tempo de espera na fila usa essa configuração em tempo real (no painel e
na tela do banho), congelando fora do expediente.

==================================================================
Lista mestra (Excel do SAP) e Área/Peso
==================================================================
Substitua lista_mestra.xlsx (ou .csv/.txt) e area_peso.xlsx na raiz do
projeto, faça commit e redeploy. Colunas da lista: Ordem (OP), Material
(código), Texto breve material, Quantidade da ordem. Área/Peso: Codigo Sap,
Area de Superficie, Peso. Em Admin → Lista mestra dá para conferir/recarregar.

==================================================================
Manutenção e reset (Admin → Manutenção de cestos · ícone de ferramenta)
==================================================================
- Lista TODOS os cestos (ativos e concluídos), com busca e filtro por estado.
- Editar qualquer cesto manualmente: número, estado, processo, tipo, tempos
  (prep/banho em minutos), operadores, observações, OPs e — no modo avançado —
  as datas/horas de início e fim (fuso local).
- Excluir um cesto específico (inclusive concluído).
- ZONA DE RESET (pede digitar APAGAR para confirmar):
    • Apagar concluídos  -> reseta o histórico, mantém os em andamento;
    • Apagar ativos      -> limpa os em andamento, mantém o histórico;
    • Apagar tudo        -> zera o sistema para começar do zero na fábrica.
  Dica: depois dos testes manuais, use "Apagar tudo" para entregar limpo.
  Faça um backup antes (Lista mestra -> Baixar backup) por segurança.

==================================================================
Relatórios / backup
==================================================================
Dashboard → Excel pré-banho, Excel banho e Excel geral (uma linha por OP).
Admin → Lista mestra → Baixar/Importar backup (JSON com cestos, usuários e
configuração). A importação só ADICIONA o que não existe — nunca apaga.

==================================================================
Rodar local
==================================================================
  pip install -r requirements.txt
  python app.py        (http://localhost:5000)
Sem DATABASE_URL usa SQLite local (dados_local.db) só para teste.
