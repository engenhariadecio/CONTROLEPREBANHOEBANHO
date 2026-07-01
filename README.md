# Controle de Produtividade — Pré-Banho e Banho

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
