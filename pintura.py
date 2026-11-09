"""
Módulo PINTURA / MONOVIA — extensão do Controle de Produtividade.

FLUXO
─────
    Preparação → Fila do banho → Banho → AGUARDANDO PINTURA → (atividade) → cesto liberado

Ao sair do banho o cesto NÃO volta para o pré-banho: ele fica "AGUARDANDO PINTURA"
e vira um LOTE. O operador de pintura escolhe uma das seis ações:

    Carregar monovia · Carregar estacionária · Carregar OP nova
    Preparação para pintura · Armazenamento em palete/arara · Retrabalho

MODELO
──────
Duas entidades novas, porque as peças passaram a ter vida própria fora do cesto:

  LotePintura      — um conjunto de peças (OPs + quantidades) aguardando pintura.
                     Nasce do cesto que saiu do banho ou de uma OP nova.
                     `com_cesto = 0` quando as peças foram para palete/arara:
                     o cesto é liberado e o lote continua na tela do operador.

  AtividadePintura — um lançamento de trabalho sobre um lote: tipo, operadores,
                     início/fim, pausas e observação. Mesma mecânica de tempo do
                     pré-banho (pausa não conta no total).

Um lote pode ter VÁRIAS atividades em sequência (ex.: preparação → armazenamento
→ carregar monovia), e é isso que dá o custo real de cada peça na pintura.

RASTREABILIDADE NA MONOVIA (odômetro)
─────────────────────────────────────
Tempo puro não serve, porque a monovia para. O que resolve é o odômetro, a
integral da velocidade: S(t) = ∫ v dt.

  • no início da carga grava-se S_ini; no fim, S_fim
  • o lote ocupa o trecho [S_ini, S_fim] do trilho
  • cada estação tem um offset_m (distância até o ponto de carregamento)
  • o lote está na estação D quando:  S_ini + D ≤ S_agora ≤ S_fim + D

Funciona com a linha parando ou mudando de ritmo, porque tudo é medido em metros.
Consequência: a câmera não precisa reconhecer a peça — o sistema já sabe qual é.
"""
import os
import io
import json
import threading
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, jsonify, session, send_file
from sqlalchemy import (Column, Integer, String, DateTime, Float, Text,
                        inspect, text, Index)

bp = Blueprint('pintura', __name__)

CTX = {}
MODELOS = {}
_lock_odo = threading.Lock()

# ── Estados do cesto (Card) ──────────────────────────────────────────────────
ST_FILA_PINTURA = 'FILA_PINTURA'          # saiu do banho, aguardando pintura
ST_EM_PINTURA = 'EM_PINTURA'              # atividade de pintura em curso
ST_AG_MONTAGEM = 'AGUARDANDO_MONTAGEM'    # retrabalho: volta ao pré-banho remontar
ESTADOS_PINTURA = (ST_FILA_PINTURA, ST_EM_PINTURA, 'CARREGANDO')

# ── Tipos de atividade ───────────────────────────────────────────────────────
TIPO_MONOVIA = 'MONOVIA'
TIPO_ESTACIONARIA = 'ESTACIONARIA'
TIPO_OP_NOVA = 'OP_NOVA'
TIPO_PREPARACAO = 'PREPARACAO'
TIPO_ARMAZENAMENTO = 'ARMAZENAMENTO'
TIPO_RETRABALHO = 'RETRABALHO'
TIPO_DESCARGA = 'DESCARGA'

TIPOS_LBL = {
    TIPO_MONOVIA: 'Carregar monovia',
    TIPO_ESTACIONARIA: 'Carregar estacionária',
    TIPO_OP_NOVA: 'Carregar OP nova',
    TIPO_PREPARACAO: 'Preparação para pintura',
    TIPO_ARMAZENAMENTO: 'Armazenamento em palete/arara',
    TIPO_RETRABALHO: 'Retrabalho',
    TIPO_DESCARGA: 'Descarregar',
}
ATIV_PREPARACAO = ['Isolamento', 'Lixa', 'Limpeza', 'Outros']
LOCAIS_ARMAZ = ['Palete', 'Arara']

CONFIG_PADRAO = {
    'ativo': True,
    'linha_padrao': 'MV1',
    'linhas': [
        {
            'id': 'MV1',
            'nome': 'Monovia 1',
            'passo_gancho_m': 0.40,
            'area_max_gancho_m2': 1.20,
            'velocidade_nominal_m_min': 3.5,
            # volta completa do trilho: usado quando a peça NÃO é descarregada e
            # segue para outro passe (falha de pintura)
            'perimetro_m': 120.0,
            'estacoes': [
                {'id': 'cam1', 'nome': 'Câmera', 'offset_m': 12.0},
                {'id': 'cabine', 'nome': 'Cabine', 'offset_m': 25.0},
                {'id': 'estufa', 'nome': 'Estufa', 'offset_m': 45.0},
                {'id': 'descarga', 'nome': 'Descarga', 'offset_m': 90.0},
            ],
        }
    ],
    'parada_limiar_m_min': 0.10,
    'parada_min_seg': 20,
    'guardar_amostra_seg': 5,
    'token_ingestao': '',
    # 1ª fase: interessa saber O QUE ENTROU NA LINHA no dia. O descarregamento
    # ainda não é apontado, então o lote encerra no fim do carregamento e nada
    # fica pendurado. Ligue quando for apontar a descarga — o código já existe
    # inteiro (repasse, tempo de forno, rastreio por odômetro).
    'descarga_ativa': False,
}

_cfg_cache = {'dados': None}


# ─────────────────────────────────────────────────────────────────────────────
# Modelos
# ─────────────────────────────────────────────────────────────────────────────
def _definir_modelos(Base):
    class LotePintura(Base):
        """Peças aguardando/passando pela pintura. Podem estar num cesto ou não."""
        __tablename__ = 'lotes_pintura'
        id = Column(Integer, primary_key=True)
        card_id = Column(Integer, index=True)         # cesto de origem (None se OP nova)
        numero_cesto = Column(Integer, default=0)     # snapshot do nº de origem
        origem = Column(String(20), default='CESTO')  # CESTO | OP_NOVA | RESTO_RETRABALHO
        estado = Column(String(20), default='AGUARDANDO', index=True)
        com_cesto = Column(Integer, default=1)        # 0 = peças fora do cesto
        itens_json = Column(Text, default='')
        quantidade = Column(Integer, default=0)
        processo = Column(String(60), default='')
        tipo = Column(String(20), default='Normal')
        observacao = Column(Text, default='')
        local = Column(Text, default='')              # onde/como está armazenado
        destino = Column(String(30), default='')      # MONOVIA | ESTACIONARIA | RETRABALHO
        atividade_atual_id = Column(Integer)
        # ── ciclo na linha (entre carregar e descarregar) ──
        carga_ativ_id = Column(Integer)      # atividade que carregou este lote
        na_linha_desde = Column(DateTime)    # instante em que entrou na linha
        voltas = Column(Integer, default=1)  # nº de passes pela cabine
        odo_repasse = Column(Float)          # odômetro quando voltou para outro passe
        motivo_repasse = Column(String(120), default='')
        criado_em = Column(DateTime, default=datetime.utcnow)
        finalizado_em = Column(DateTime)

        def itens(self):
            try:
                v = json.loads(self.itens_json) if self.itens_json else []
                return v if isinstance(v, list) else []
            except (ValueError, TypeError):
                return []

        def to_dict(self):
            itens = self.itens()
            area = peso = 0.0
            for it in itens:
                a, p = CTX['_area_peso_do_codigo'](it.get('material', ''))
                q = int(it.get('quantidade') or 0)
                area += a * q
                peso += p * q
            esperando = 0
            if self.estado == 'AGUARDANDO' and self.criado_em:
                esperando = CTX['tempo_util_segundos'](self.criado_em, datetime.utcnow())
            return {
                'id': self.id, 'card_id': self.card_id, 'numero_cesto': self.numero_cesto or 0,
                'origem': self.origem, 'estado': self.estado,
                'com_cesto': bool(self.com_cesto),
                'itens': itens, 'n_itens': len(itens),
                'ordem': itens[0].get('ordem') if itens else '',
                'material': itens[0].get('material') if itens else '',
                'texto_breve': itens[0].get('texto_breve') if itens else '',
                'quantidade': self.quantidade or 0,
                'processo': self.processo or '', 'tipo': self.tipo or 'Normal',
                'observacao': self.observacao or '', 'local': self.local or '',
                'destino': self.destino or '',
                'area_total': round(area, 3), 'peso_total': round(peso, 2),
                'criado_iso': self.criado_em.isoformat() + 'Z' if self.criado_em else '',
                'esperando_seg': esperando,
                'atividade_atual_id': self.atividade_atual_id,
                'carga_ativ_id': self.carga_ativ_id,
                'na_linha_iso': (self.na_linha_desde.isoformat() + 'Z'
                                 if self.na_linha_desde else ''),
                'na_linha_seg': (int((datetime.utcnow() - self.na_linha_desde).total_seconds())
                                 if self.estado == 'NA_LINHA' and self.na_linha_desde else 0),
                'voltas': self.voltas or 1,
                'motivo_repasse': self.motivo_repasse or '',
                'em_repasse': bool(self.odo_repasse is not None),
            }

    class AtividadePintura(Base):
        """Um lançamento de trabalho sobre um lote (com tempo e paradas)."""
        __tablename__ = 'atividades_pintura'
        id = Column(Integer, primary_key=True)
        lote_id = Column(Integer, index=True)
        card_id = Column(Integer, index=True)
        numero_cesto = Column(Integer, default=0)
        tipo = Column(String(20), index=True)
        subtipo = Column(String(60), default='')
        subtipo_outro = Column(String(120), default='')
        justificativa = Column(Text, default='')
        destino = Column(String(30), default='')

        operadores_json = Column(Text, default='')
        n_operadores = Column(Integer, default=1)
        usuario_ini = Column(String(120), default='')
        usuario_fim = Column(String(120), default='')

        inicio = Column(DateTime)
        fim = Column(DateTime)
        minutos = Column(Float, default=0)
        pausado = Column(Integer, default=0)
        pausa_inicio = Column(DateTime)
        pausa_motivo = Column(String(60), default='')
        pausa_acumulada_seg = Column(Integer, default=0)
        pausas_json = Column(Text, default='')

        observacao = Column(Text, default='')
        estado = Column(String(20), default='EM_ANDAMENTO', index=True)

        monovia_linha = Column(String(20), default='')
        odo_ini = Column(Float)
        odo_fim = Column(Float)
        n_ganchos = Column(Integer, default=0)
        # descarga: quantas peças desceram boas e quantas voltaram para outro passe
        qtd_ok = Column(Integer, default=0)
        qtd_repasse = Column(Integer, default=0)
        motivo_repasse = Column(String(120), default='')
        # FOTO do lote no momento desta atividade. Sem isso, um repasse (que
        # reduz o lote) reescreveria retroativamente as métricas do carregamento.
        qtd_lote = Column(Integer, default=0)
        area_lote = Column(Float, default=0)
        criado_em = Column(DateTime, default=datetime.utcnow)

        def operadores(self):
            try:
                v = json.loads(self.operadores_json) if self.operadores_json else []
                return v if isinstance(v, list) else []
            except (ValueError, TypeError):
                return []

        def to_dict(self):
            def iso(dt):
                return dt.isoformat() + 'Z' if dt else ''

            def fmt(dt):
                return (dt - timedelta(hours=CTX['FUSO_LOCAL_HORAS'])).strftime(
                    '%d/%m/%Y %H:%M:%S') if dt else ''
            ops = self.operadores()
            pausas = CTX['_pausas_resumo'](self.pausas_json)
            det = self.subtipo or ''
            if det.lower().startswith('outro') and self.subtipo_outro:
                det = self.subtipo_outro
            return {
                'id': self.id, 'lote_id': self.lote_id, 'card_id': self.card_id,
                'numero_cesto': self.numero_cesto or 0,
                'tipo': self.tipo, 'tipo_lbl': TIPOS_LBL.get(self.tipo, self.tipo),
                'detalhe': det, 'subtipo': self.subtipo or '',
                'subtipo_outro': self.subtipo_outro or '',
                'justificativa': self.justificativa or '', 'destino': self.destino or '',
                'operadores': ops, 'operadores_txt': CTX['_op_txt'](ops),
                'n_operadores': self.n_operadores or 1,
                'inicio': fmt(self.inicio), 'fim': fmt(self.fim),
                'inicio_iso': iso(self.inicio), 'fim_iso': iso(self.fim),
                'inicio_data': fmt(self.inicio)[:10], 'inicio_hora': fmt(self.inicio)[11:],
                'fim_data': fmt(self.fim)[:10], 'fim_hora': fmt(self.fim)[11:],
                'minutos': round(self.minutos or 0, 1),
                'pausado': bool(self.pausado),
                'pausa_inicio_iso': iso(self.pausa_inicio),
                'pausa_motivo': self.pausa_motivo or '',
                'pausa_acumulada_seg': self.pausa_acumulada_seg or 0,
                'pausas': pausas, 'total_pausa_min': round(pausas['total_seg'] / 60, 1),
                'observacao': self.observacao or '', 'estado': self.estado,
                'monovia_linha': self.monovia_linha or '',
                'odo_ini': self.odo_ini, 'odo_fim': self.odo_fim,
                'metros': (round(self.odo_fim - self.odo_ini, 2)
                           if self.odo_ini is not None and self.odo_fim is not None else 0),
                'n_ganchos': self.n_ganchos or 0,
                'qtd_ok': self.qtd_ok or 0, 'qtd_repasse': self.qtd_repasse or 0,
                'motivo_repasse': self.motivo_repasse or '',
                'qtd_lote': self.qtd_lote or 0, 'area_lote': round(self.area_lote or 0, 3),
                'turno_lbl': CTX['turno_label'](
                    CTX['turno_num'](self.fim or self.inicio or datetime.utcnow())),
            }

    class MonoviaEstado(Base):
        __tablename__ = 'monovia_estado'
        id = Column(Integer, primary_key=True)
        linha = Column(String(20), unique=True, nullable=False)
        ts = Column(DateTime)
        velocidade = Column(Float, default=0)
        odometro_m = Column(Float, default=0)
        odometro_bruto = Column(Float)
        parada_desde = Column(DateTime)
        fonte = Column(String(20), default='')
        ultima_gravacao = Column(DateTime)

    class MonoviaAmostra(Base):
        __tablename__ = 'monovia_amostras'
        id = Column(Integer, primary_key=True)
        linha = Column(String(20), index=True)
        ts = Column(DateTime, index=True)
        velocidade = Column(Float, default=0)
        odometro_m = Column(Float, default=0)
        parada = Column(Integer, default=0)
        fonte = Column(String(20), default='')
        confianca = Column(Float, default=1.0)
        extra_json = Column(Text, default='')

    class MonoviaParada(Base):
        __tablename__ = 'monovia_paradas'
        id = Column(Integer, primary_key=True)
        linha = Column(String(20), index=True)
        inicio = Column(DateTime, index=True)
        fim = Column(DateTime)
        duracao_seg = Column(Integer, default=0)
        odometro_m = Column(Float, default=0)
        aberta = Column(Integer, default=1)
        motivo = Column(String(60), default='')
        justificativa = Column(Text, default='')
        usuario = Column(String(120), default='')
        fonte = Column(String(20), default='')

    class MonoviaDeteccao(Base):
        __tablename__ = 'monovia_deteccoes'
        id = Column(Integer, primary_key=True)
        linha = Column(String(20), index=True)
        estacao = Column(String(30), index=True)
        ts = Column(DateTime, index=True)
        odometro_m = Column(Float, default=0)
        atividade_id = Column(Integer, index=True)
        lote_id = Column(Integer, index=True)
        n_pecas = Column(Integer, default=0)
        area_m2 = Column(Float, default=0)
        confianca = Column(Float, default=0)
        imagem_ref = Column(String(255), default='')
        extra_json = Column(Text, default='')

    Index('ix_mv_amostra_linha_ts', MonoviaAmostra.linha, MonoviaAmostra.ts)
    MODELOS.update(lote=LotePintura, atividade=AtividadePintura,
                   estado=MonoviaEstado, amostra=MonoviaAmostra,
                   parada=MonoviaParada, deteccao=MonoviaDeteccao)
    return MODELOS


def init_pintura(**kw):
    CTX.update(kw)
    _definir_modelos(kw['Base'])
    return MODELOS


def _migrar_tabelas_modulo(engine, insp):
    """Adiciona colunas que faltam nas tabelas do módulo.

    `Base.metadata.create_all` cria tabelas que não existem, mas NÃO altera
    tabelas já criadas. Sem isso, toda coluna nova (foi o caso das de repasse)
    quebra em produção: no Postgres a transação aborta e o usuário vê erro 500
    ao tirar o cesto do banho. Aqui a lista sai dos próprios modelos, então
    colunas futuras passam a ser migradas sozinhas.
    """
    for modelo in MODELOS.values():
        tabela = modelo.__tablename__
        if tabela not in insp.get_table_names():
            continue                      # create_all cuida das que não existem
        existentes = {c['name'] for c in insp.get_columns(tabela)}
        for col in modelo.__table__.columns:
            if col.name in existentes or col.primary_key:
                continue
            try:
                tipo = col.type.compile(engine.dialect)
            except Exception:
                tipo = 'TEXT'
            try:
                with engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE {tabela} ADD COLUMN {col.name} {tipo}'))
                print(f'[pintura] coluna {tabela}.{col.name} criada.')
            except Exception as e:
                print(f'[pintura] aviso ao criar {tabela}.{col.name}: {e}')


def reparar_lotes_orfaos():
    """Cesto que saiu do banho mas ficou sem lote (falha antiga de migração)
    não apareceria na fila da pintura e ficaria preso, travando o número do
    cesto. Aqui ele é recuperado no boot."""
    db = _db()
    L, Card = MODELOS['lote'], CTX['Card']
    try:
        presos = db.query(Card).filter(
            Card.estado.in_([ST_FILA_PINTURA, ST_EM_PINTURA])).all()
        n = 0
        for card in presos:
            tem = db.query(L).filter(L.card_id == card.id, L.estado != 'FINALIZADO').first()
            if not tem:
                criar_lote_do_cesto(card, db)
                n += 1
        if n:
            db.commit()
            print(f'[pintura] {n} cesto(s) sem lote recuperados para a fila da pintura.')
    except Exception as e:
        db.rollback()
        print(f'[pintura] aviso ao recuperar lotes: {e}')


def criar_tabelas():
    engine = CTX['engine']
    insp = inspect(engine)
    novas = {
        'pintura_inicio': 'TIMESTAMP NULL', 'pintura_fim': 'TIMESTAMP NULL',
        'pintura_minutos': 'FLOAT DEFAULT 0',
        'operador_pintura_inicio': "VARCHAR(120) DEFAULT ''",
        'oper_pint_ini_mat': "VARCHAR(50) DEFAULT ''",
        'operador_pintura_fim': "VARCHAR(120) DEFAULT ''",
        'oper_pint_fim_mat': "VARCHAR(50) DEFAULT ''",
        'obs_pintura': 'TEXT', 'monovia_linha': "VARCHAR(20) DEFAULT ''",
        'odo_carga_ini': 'FLOAT', 'odo_carga_fim': 'FLOAT',
        'n_ganchos': 'INTEGER DEFAULT 0', 'descarregado_em': 'TIMESTAMP NULL',
        'card_origem_id': 'INTEGER', 'lote_origem_id': 'INTEGER',
    }
    if 'cards' in insp.get_table_names():
        existentes = {c['name'] for c in insp.get_columns('cards')}
        with engine.begin() as conn:
            for col, tipo in novas.items():
                if col not in existentes:
                    try:
                        conn.execute(text(f'ALTER TABLE cards ADD COLUMN {col} {tipo}'))
                    except Exception:
                        pass
    _migrar_tabelas_modulo(engine, insp)
    print('[pintura] tabelas/colunas OK.')


# ─────────────────────────────────────────────────────────────────────────────
# Sessão e configuração
# ─────────────────────────────────────────────────────────────────────────────
def _db(db=None):
    """Session é scoped_session: `Session()` devolve a MESMA sessão da thread.
    Por isso nenhum helper aqui chama db.close() — isso fecharia a sessão de quem
    chamou e as alterações pendentes se perderiam. Quem fecha é o teardown."""
    return db if db is not None else CTX['Session']()


def get_config(forcar=False):
    if _cfg_cache['dados'] is not None and not forcar:
        return _cfg_cache['dados']
    cfg = json.loads(json.dumps(CONFIG_PADRAO))
    try:
        row = _db().query(CTX['Config']).filter_by(chave='monovia').first()
        if row and row.valor:
            salvo = json.loads(row.valor)
            if isinstance(salvo, dict):
                cfg.update(salvo)
    except Exception:
        pass
    if not cfg.get('linhas'):
        cfg['linhas'] = json.loads(json.dumps(CONFIG_PADRAO['linhas']))
    _cfg_cache['dados'] = cfg
    return cfg


def set_config(novo):
    cfg = json.loads(json.dumps(CONFIG_PADRAO))
    cfg.update(novo or {})
    db = _db()
    row = db.query(CTX['Config']).filter_by(chave='monovia').first()
    if not row:
        row = CTX['Config'](chave='monovia')
        db.add(row)
    row.valor = json.dumps(cfg, ensure_ascii=False)
    db.commit()
    _cfg_cache['dados'] = cfg
    return cfg


def pintura_ativa():
    return bool(get_config().get('ativo', True))


def descarga_ativa():
    return bool(get_config().get('descarga_ativa', False))


def get_linha(linha_id=None):
    cfg = get_config()
    lid = linha_id or cfg.get('linha_padrao') or 'MV1'
    for l in cfg['linhas']:
        if l.get('id') == lid:
            return l
    return cfg['linhas'][0]


# ─────────────────────────────────────────────────────────────────────────────
# Telemetria da monovia
# ─────────────────────────────────────────────────────────────────────────────
def _estado_linha(db, linha_id):
    M = MODELOS['estado']
    st = db.query(M).filter_by(linha=linha_id).first()
    if not st:
        st = M(linha=linha_id, ts=datetime.utcnow(), velocidade=0, odometro_m=0.0)
        db.add(st)
        db.flush()
    return st


def odometro_agora(linha_id=None, db=None):
    linha = get_linha(linha_id)
    db = _db(db)
    st = db.query(MODELOS['estado']).filter_by(linha=linha['id']).first()
    if not st:
        return {'odometro_m': 0.0, 'velocidade': 0.0, 'ts': None, 'parada': False,
                'parada_desde': None, 'idade_seg': None, 'modo': 'sem_dados', 'fonte': ''}
    agora = datetime.utcnow()
    idade = (agora - (st.ts or agora)).total_seconds()
    odo = st.odometro_m or 0.0
    modo = 'medido'
    if 0 < idade <= 120 and (st.velocidade or 0) > 0:
        odo += (st.velocidade / 60.0) * idade      # projeção curta entre amostras
        modo = 'projetado'
    elif idade > 120:
        modo = 'desatualizado'
    cfg = get_config()
    return {'odometro_m': round(odo, 3), 'velocidade': round(st.velocidade or 0, 2),
            'ts': st.ts.isoformat() + 'Z' if st.ts else None,
            'parada': bool((st.velocidade or 0) <= cfg['parada_limiar_m_min']),
            'parada_desde': st.parada_desde.isoformat() + 'Z' if st.parada_desde else None,
            'idade_seg': round(idade, 1), 'modo': modo, 'fonte': st.fonte or ''}


def _odometro_estimado(linha_id=None):
    """Sem telemetria: estima metros pela velocidade nominal, para o rastreio já
    funcionar no dia 1."""
    v = float(get_linha(linha_id).get('velocidade_nominal_m_min') or 3.5)
    return (v / 60.0) * (datetime.utcnow() - datetime(2020, 1, 1)).total_seconds()


def _odo_atual(linha_id, db=None):
    est = odometro_agora(linha_id, db)
    return est['odometro_m'] if est['modo'] != 'sem_dados' else _odometro_estimado(linha_id)


def ingerir_amostra(linha_id, velocidade=None, odometro=None, ts=None,
                    fonte='clp', confianca=1.0, extra=None):
    cfg = get_config()
    lid = get_linha(linha_id)['id']
    ts = ts or datetime.utcnow()
    db = _db()
    with _lock_odo:
        st = _estado_linha(db, lid)
        dt = max(0.0, min((ts - (st.ts or ts)).total_seconds(), 300.0))

        if odometro is not None:
            odo_bruto = float(odometro)
            if st.odometro_bruto is None:
                delta = 0.0
            elif odo_bruto >= st.odometro_bruto:
                delta = odo_bruto - st.odometro_bruto
            else:
                delta = odo_bruto                     # encoder resetou
            st.odometro_bruto = odo_bruto
            st.odometro_m = (st.odometro_m or 0.0) + delta
            if velocidade is None and dt > 0:
                velocidade = (delta / dt) * 60.0
        elif velocidade is not None and dt > 0:
            st.odometro_m = (st.odometro_m or 0.0) + (float(velocidade) / 60.0) * dt

        vel = float(velocidade or 0.0)
        st.velocidade, st.ts, st.fonte = vel, ts, fonte

        parado = vel <= float(cfg['parada_limiar_m_min'])
        if parado and not st.parada_desde:
            st.parada_desde = ts
        elif not parado and st.parada_desde:
            dur = (ts - st.parada_desde).total_seconds()
            if dur >= float(cfg['parada_min_seg']):
                db.add(MODELOS['parada'](linha=lid, inicio=st.parada_desde, fim=ts,
                                         duracao_seg=int(dur), odometro_m=st.odometro_m,
                                         aberta=0, fonte=fonte))
            st.parada_desde = None

        passo = float(cfg.get('guardar_amostra_seg') or 5)
        if (not st.ultima_gravacao) or (ts - st.ultima_gravacao).total_seconds() >= passo:
            db.add(MODELOS['amostra'](
                linha=lid, ts=ts, velocidade=vel, odometro_m=st.odometro_m,
                parada=1 if parado else 0, fonte=fonte, confianca=confianca,
                extra_json=json.dumps(extra, ensure_ascii=False) if extra else ''))
            st.ultima_gravacao = ts
        db.commit()
        return {'odometro_m': round(st.odometro_m or 0, 3),
                'velocidade': round(vel, 2), 'parado': parado}


# ─────────────────────────────────────────────────────────────────────────────
# Rastreabilidade
# ─────────────────────────────────────────────────────────────────────────────
def posicao_da_atividade(ativ, odo_atual=None, db=None):
    linha = get_linha(ativ.monovia_linha)
    est = odometro_agora(linha['id'], db)
    if odo_atual is None:
        odo_atual = est['odometro_m']
    ini = ativ.odo_ini
    if ini is None:
        return {'ok': False, 'motivo': 'carga sem odômetro', 'estacoes': []}
    fim = ativ.odo_fim if ativ.odo_fim is not None else odo_atual
    v = est['velocidade'] or linha.get('velocidade_nominal_m_min') or 3.5
    out = []
    for e in linha.get('estacoes', []):
        d = float(e.get('offset_m') or 0)
        a, b = ini + d, fim + d
        if odo_atual < a:
            faltam = a - odo_atual
            sit, eta = 'a_caminho', (round(faltam / (v / 60.0) / 60.0, 1) if v > 0 else None)
        elif odo_atual <= b:
            sit, eta, faltam = 'na_estacao', 0, 0
        else:
            sit, eta, faltam = 'passou', None, 0
        out.append({'estacao': e.get('id'), 'nome': e.get('nome'), 'offset_m': d,
                    'situacao': sit, 'faltam_m': round(faltam, 1), 'eta_min': eta,
                    'entra_em_m': round(a, 1), 'sai_em_m': round(b, 1)})
    return {'ok': True, 'odometro_m': round(odo_atual, 1),
            'trecho': [round(ini, 1), round(fim, 1)],
            'metros_lote': round(fim - ini, 2), 'estacoes': out}


def atividades_na_estacao(estacao_id, linha_id=None, odo_atual=None, db=None):
    """Quais cargas estão passando nesta estação agora — é o que a câmera pergunta."""
    linha = get_linha(linha_id)
    est = next((e for e in linha.get('estacoes', []) if e.get('id') == estacao_id), None)
    if not est:
        return []
    db = _db(db)
    d = float(est.get('offset_m') or 0)
    if odo_atual is None:
        odo_atual = odometro_agora(linha['id'], db)['odometro_m']
    alvo = odo_atual - d
    A, L = MODELOS['atividade'], MODELOS['lote']
    q = db.query(A).filter(A.odo_ini.isnot(None), A.odo_ini <= alvo
                           ).order_by(A.odo_ini.desc()).limit(30).all()
    out = []
    for a in q:
        fim = a.odo_fim if a.odo_fim is not None else alvo
        if fim >= alvo:
            lote = db.query(L).get(a.lote_id) if a.lote_id else None
            d_ = a.to_dict()
            d_['lote'] = lote.to_dict() if lote else None
            d_['posicao_no_lote_pct'] = round(
                100 * (alvo - a.odo_ini) / max(0.001, fim - a.odo_ini), 1)
            out.append(d_)
    return out


def _offset_descarga(linha):
    est = next((e for e in linha.get('estacoes', []) if e.get('id') == 'descarga'), None)
    return float(est.get('offset_m')) if est else None


def situacao_na_linha(lote, ativ_carga, odo_atual=None, db=None):
    """Onde o lote está entre a carga e a descarga.

    O lote chega na descarga quando a ÚLTIMA peça dele passou pela estação, ou
    seja quando:  odo_atual >= odo_fim_da_carga + offset_descarga.
    Sem monovia (estacionária) ou sem estação configurada, fica pronto na hora —
    quem manda é o operador.
    """
    if not ativ_carga or ativ_carga.tipo == TIPO_ESTACIONARIA:
        return {'pronto': True, 'modo': 'manual', 'faltam_m': 0, 'eta_min': 0}
    linha = get_linha(ativ_carga.monovia_linha)
    off = _offset_descarga(linha)
    if off is None or ativ_carga.odo_fim is None:
        return {'pronto': True, 'modo': 'manual', 'faltam_m': 0, 'eta_min': 0}
    est = odometro_agora(linha['id'], db)
    if odo_atual is None:
        odo_atual = est['odometro_m']
    if lote is not None and lote.odo_repasse is not None:
        # peça não descarregada: dá uma volta inteira no trilho antes de voltar
        # à estação de descarga
        alvo = lote.odo_repasse + float(linha.get('perimetro_m') or 120.0)
        modo = 'repasse'
    else:
        alvo = ativ_carga.odo_fim + off
        modo = 'odometro'
    faltam = alvo - odo_atual
    if faltam <= 0:
        return {'pronto': True, 'modo': modo, 'faltam_m': 0, 'eta_min': 0}
    v = est['velocidade'] or linha.get('velocidade_nominal_m_min') or 3.5
    return {'pronto': False, 'modo': modo, 'faltam_m': round(faltam, 1),
            'eta_min': round(faltam / v, 1) if v > 0 else None,
            'primeira_peca_chegou': odo_atual >= (ativ_carga.odo_ini or 0) + off}


def metricas_carga(ativ, lote=None, linha=None):
    """Empilhamento e ocupação — só com odômetro + quantidade/área já existentes."""
    linha = linha or get_linha(ativ.monovia_linha)
    metros = 0.0
    if ativ.odo_ini is not None and ativ.odo_fim is not None:
        metros = max(0.0, ativ.odo_fim - ativ.odo_ini)
    passo = float(linha.get('passo_gancho_m') or 0.4)
    ganchos = int(metros / passo) if metros > 0 else 0
    # prioriza a foto gravada na atividade; o lote pode ter mudado desde então
    if ativ.qtd_lote:
        pecas, area = ativ.qtd_lote, (ativ.area_lote or 0)
    else:
        d = lote.to_dict() if lote else {}
        area = d.get('area_total') or 0
        pecas = d.get('quantidade') or 0
    area_max = ganchos * float(linha.get('area_max_gancho_m2') or 1.2)
    return {
        'metros': round(metros, 2), 'ganchos': ganchos,
        'pecas_por_gancho': round(pecas / ganchos, 2) if ganchos else 0,
        'area_por_metro': round(area / metros, 3) if metros else 0,
        'area_m2': round(area, 3), 'pecas': pecas,
        'ocupacao_pct': round(100 * area / area_max, 1) if area_max else 0,
        'min_por_metro': round((ativ.minutos or 0) / metros, 2) if metros else 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de domínio
# ─────────────────────────────────────────────────────────────────────────────
def _normalizar_operadores(db, dados, n_op):
    """Mesma regra do pré-banho: aceita matrícula ou {nome, matricula}."""
    Usuario = CTX['Usuario']
    ops = []
    for item in (dados or [])[:4]:
        if isinstance(item, dict):
            nome = (item.get('nome') or '').strip()
            mat = (item.get('matricula') or '').strip()
            if mat and not nome:
                u = db.query(Usuario).filter_by(login=mat).first()
                if u:
                    nome = u.nome
            if nome or mat:
                ops.append({'nome': nome or mat, 'matricula': mat})
        else:
            mat = str(item).strip()
            if mat:
                u = db.query(Usuario).filter_by(login=mat).first()
                ops.append({'nome': u.nome if u else mat, 'matricula': mat})
    if not ops:
        ops = [{'nome': session.get('nome', ''), 'matricula': session.get('usuario', '')}]
    return ops[:max(1, n_op)]


def _norm_itens(itens):
    out = []
    for it in itens or []:
        try:
            q = int(it.get('quantidade') or 0)
        except (ValueError, TypeError):
            q = 0
        ordem = (it.get('ordem') or '').strip()
        mat = (it.get('material') or '').strip()
        if not ordem and not mat:
            continue
        out.append({'ordem': ordem, 'material': mat,
                    'texto_breve': (it.get('texto_breve') or '').strip(),
                    'quantidade': q, 'tipo': it.get('tipo') or 'Normal',
                    'motivo': (it.get('motivo') or '').strip()})
    return out


def criar_lote_do_cesto(card, db=None):
    """Chamado quando o cesto sai do banho."""
    db = _db(db)
    L = MODELOS['lote']
    existente = db.query(L).filter(L.card_id == card.id, L.estado != 'FINALIZADO').first()
    if existente:
        return existente
    d = card.to_dict()
    lote = L(card_id=card.id, numero_cesto=card.numero_cesto, origem='CESTO',
             estado='AGUARDANDO', com_cesto=1,
             itens_json=json.dumps(d['itens'], ensure_ascii=False),
             quantidade=d['qtd_total'], processo=card.processo or '',
             tipo=card.tipo or 'Normal', observacao=card.observacao or '',
             criado_em=card.banho_fim or datetime.utcnow())
    db.add(lote)
    db.flush()
    return lote


def _fechar_pausa(ativ, agora):
    if ativ.pausado and ativ.pausa_inicio:
        dur = int((agora - ativ.pausa_inicio).total_seconds())
        ativ.pausa_acumulada_seg = (ativ.pausa_acumulada_seg or 0) + dur
        try:
            mapa = json.loads(ativ.pausas_json) if ativ.pausas_json else {}
        except (ValueError, TypeError):
            mapa = {}
        motivo = ativ.pausa_motivo or 'Outros'
        mapa[motivo] = mapa.get(motivo, 0) + dur
        ativ.pausas_json = json.dumps(mapa, ensure_ascii=False)
    ativ.pausado = 0
    ativ.pausa_inicio = None
    ativ.pausa_motivo = ''


def _liberar_cesto(db, lote, motivo=''):
    """O cesto ficou vazio: volta a ficar disponível para o pré-banho."""
    if lote.card_id:
        card = db.query(CTX['Card']).get(lote.card_id)
        if card and card.estado in ESTADOS_PINTURA:
            card.estado = CTX['ST_CONCLUIDO']
            if motivo and not card.obs_pintura:
                card.obs_pintura = motivo
    lote.com_cesto = 0


def _token_valido():
    if session.get('usuario') and ({'admin', 'pintura'} & set(session.get('perfis') or [])):
        return True
    esperado = get_config().get('token_ingestao') or os.environ.get('MONOVIA_TOKEN', '')
    if not esperado:
        return True
    return (request.headers.get('X-Monovia-Token') or request.args.get('token', '')) == esperado


def _lr(*perfis):
    def deco(f):
        def wrapper(*a, **kw):
            return CTX['login_required'](*perfis)(f)(*a, **kw)
        wrapper.__name__ = f.__name__
        return wrapper
    return deco


# ═════════════════════════════════════════════════════════════════════════════
# TELAS
# ═════════════════════════════════════════════════════════════════════════════
@bp.route('/pintura')
@_lr('pintura', 'banho')
def tela_pintura():
    return render_template('pintura.html', nome=session.get('nome'),
                           perfil=session.get('perfil'), processos=CTX['PROCESSOS'],
                           ativ_prep=ATIV_PREPARACAO, locais=LOCAIS_ARMAZ)


@bp.route('/monovia')
@_lr('pintura', 'painel')
def tela_monovia():
    return render_template('monovia.html', nome=session.get('nome'),
                           perfil=session.get('perfil'))


# ═════════════════════════════════════════════════════════════════════════════
# PAINEL DO OPERADOR DE PINTURA
# ═════════════════════════════════════════════════════════════════════════════
@bp.route('/api/pintura/painel')
@_lr('pintura', 'banho')
def api_painel():
    db = _db()
    L, A = MODELOS['lote'], MODELOS['atividade']
    lotes = db.query(L).filter(L.estado.in_(['AGUARDANDO', 'EM_ATIVIDADE', 'NA_LINHA'])
                               ).order_by(L.criado_em).all()
    ativs = {a.lote_id: a.to_dict() for a in
             db.query(A).filter_by(estado='EM_ANDAMENTO').order_by(A.inicio).all()}
    mv = odometro_agora(db=db)
    com_cesto, sem_cesto, em_ativ, prontos, na_linha = [], [], [], [], []
    for l in lotes:
        d = l.to_dict()
        if l.estado == 'EM_ATIVIDADE':
            d['atividade'] = ativs.get(l.id)
            em_ativ.append(d)
        elif l.estado == 'NA_LINHA':
            carga = db.query(A).get(l.carga_ativ_id) if l.carga_ativ_id else None
            d['carga'] = carga.to_dict() if carga else None
            d['linha_status'] = situacao_na_linha(l, carga, mv['odometro_m'], db)
            (prontos if d['linha_status']['pronto'] else na_linha).append(d)
        elif l.com_cesto:
            com_cesto.append(d)
        else:
            sem_cesto.append(d)
    return jsonify({'cestos': com_cesto, 'sem_cesto': sem_cesto,
                    'em_atividade': em_ativ, 'prontos_descarga': prontos,
                    'na_linha': na_linha, 'monovia': mv,
                    'descarga_ativa': descarga_ativa()})


def estado_ao_vivo():
    """Foto instantânea da pintura, para o painel da gerência (polling de 5 s).

    Além da fila e do que está carregando, devolve o que JÁ ENTROU NA LINHA HOJE
    — que é a pergunta principal nesta primeira fase: o que foi pintado no dia.
    """
    db = _db()
    L, A = MODELOS['lote'], MODELOS['atividade']
    fuso = timedelta(hours=CTX['FUSO_LOCAL_HORAS'])
    hoje = (datetime.utcnow() - fuso).date()
    ini_hoje = datetime.combine(hoje, datetime.min.time()) + fuso

    fila = db.query(L).filter(L.estado == 'AGUARDANDO').order_by(L.criado_em).all()
    ativs = db.query(A).filter_by(estado='EM_ANDAMENTO').order_by(A.inicio).all()
    lotes_ativ = {l.id: l for l in db.query(L).filter(
        L.id.in_([a.lote_id for a in ativs]))} if ativs else {}

    CARGAS = (TIPO_MONOVIA, TIPO_ESTACIONARIA, TIPO_OP_NOVA)
    hoje_q = db.query(A).filter(A.estado == 'FINALIZADA', A.tipo.in_(CARGAS),
                                A.fim >= ini_hoje).all()
    pint_hoje = {'cargas': len(hoje_q),
                 'pecas': sum(a.qtd_lote or 0 for a in hoje_q),
                 'area': round(sum(a.area_lote or 0 for a in hoje_q), 2),
                 'monovia': sum(1 for a in hoje_q if a.tipo == TIPO_MONOVIA
                                or (a.subtipo or '').lower() == 'monovia'),
                 'estacionaria': sum(1 for a in hoje_q if a.tipo == TIPO_ESTACIONARIA
                                     or (a.subtipo or '').lower() == 'estacionaria'),
                 'metros': round(sum(max(0, (a.odo_fim or 0) - (a.odo_ini or 0))
                                     for a in hoje_q if a.odo_ini is not None), 1)}

    def _res(l):
        d = l.to_dict()
        return {k: d[k] for k in ('id', 'numero_cesto', 'com_cesto', 'ordem', 'itens',
                                  'n_itens', 'quantidade', 'area_total', 'peso_total',
                                  'processo', 'tipo', 'criado_iso', 'esperando_seg', 'local')}

    carregando = []
    for a in ativs:
        lote = lotes_ativ.get(a.lote_id)
        if not lote:
            continue
        d = _res(lote)
        ad = a.to_dict()
        d.update(atividade_id=a.id, tipo_lbl=ad['tipo_lbl'], detalhe=ad['detalhe'],
                 inicio_iso=ad['inicio_iso'], pausado=ad['pausado'],
                 pausa_motivo=ad['pausa_motivo'],
                 pausa_acumulada_seg=ad['pausa_acumulada_seg'],
                 pausa_inicio_iso=ad['pausa_inicio_iso'],
                 operadores_txt=ad['operadores_txt'], n_operadores=ad['n_operadores'],
                 eh_carga=a.tipo in CARGAS)
        carregando.append(d)

    return {
        'fila': [_res(l) for l in fila],
        'fila_qtd': len(fila),
        'fila_pecas': sum(l.quantidade or 0 for l in fila),
        'fila_area': round(sum(_res(l)['area_total'] for l in fila), 2),
        'espera_max_seg': max([_res(l)['esperando_seg'] for l in fila], default=0),
        'carregando': carregando,
        'carregando_qtd': len(carregando),
        'hoje': pint_hoje,
        'na_linha': (db.query(L).filter(L.estado == 'NA_LINHA').count()
                     if descarga_ativa() else 0),
        'descarga_ativa': descarga_ativa(),
        'monovia': odometro_agora(db=db),
    }


@bp.route('/api/pintura/ao_vivo')
@_lr('pintura', 'painel', 'admin', 'banho')
def api_pintura_ao_vivo():
    return jsonify(estado_ao_vivo())


@bp.route('/api/pintura/lote/<int:lote_id>')
@_lr('pintura', 'banho', 'painel')
def api_lote(lote_id):
    db = _db()
    L, A = MODELOS['lote'], MODELOS['atividade']
    lote = db.query(L).get(lote_id)
    if not lote:
        return jsonify({'erro': 'Lote não encontrado.'}), 404
    d = lote.to_dict()
    d['historico'] = [a.to_dict() for a in
                      db.query(A).filter_by(lote_id=lote_id).order_by(A.id).all()]
    if lote.card_id:
        card = db.query(CTX['Card']).get(lote.card_id)
        if card:
            cd = card.to_dict()
            d['cesto_origem'] = {
                'numero_cesto': cd['numero_cesto'], 'prep_minutos': cd['prep_minutos'],
                'banho_minutos': cd['banho_minutos'], 'banho_fim': cd['banho_fim'],
                'operadores_prep_txt': cd['operadores_prep_txt'],
                'processo': cd['processo'],
                'observacao': cd['observacao'], 'obs_banho': cd['obs_banho'],
                'estado_atual': cd['estado'],
            }
    return jsonify(d)


# ═════════════════════════════════════════════════════════════════════════════
# ATIVIDADES: iniciar / pausar / finalizar
# ═════════════════════════════════════════════════════════════════════════════
@bp.route('/api/pintura/atividade/iniciar', methods=['POST'])
@_lr('pintura', 'banho')
def api_ativ_iniciar():
    d = request.json or {}
    tipo = (d.get('tipo') or '').upper()
    if tipo not in TIPOS_LBL or tipo in (TIPO_RETRABALHO, TIPO_OP_NOVA):
        return jsonify({'sucesso': False, 'erro': 'Tipo de atividade inválido.'}), 400
    db = _db()
    L, A = MODELOS['lote'], MODELOS['atividade']
    lote = db.query(L).get(int(d.get('lote_id') or 0))
    if not lote:
        return jsonify({'sucesso': False, 'erro': 'Lote não encontrado.'}), 404
    if lote.estado == 'EM_ATIVIDADE':
        return jsonify({'sucesso': False, 'erro': 'Este lote já tem atividade em andamento.'}), 400
    if tipo == TIPO_DESCARGA and lote.estado != 'NA_LINHA':
        return jsonify({'sucesso': False, 'erro': 'Este lote não está na linha.'}), 400
    if tipo != TIPO_DESCARGA and lote.estado == 'NA_LINHA':
        return jsonify({'sucesso': False,
                        'erro': 'Lote na linha — só é possível descarregar.'}), 400

    try:
        n_op = int(d.get('n_operadores', 1))
        n_op = n_op if n_op in (1, 2, 3, 4) else 1
    except (ValueError, TypeError):
        n_op = 1
    ops = _normalizar_operadores(db, d.get('operadores'), n_op)

    ativ = A(lote_id=lote.id, card_id=lote.card_id, numero_cesto=lote.numero_cesto or 0,
             tipo=tipo, subtipo=(d.get('subtipo') or '')[:60],
             subtipo_outro=(d.get('subtipo_outro') or '')[:120],
             justificativa=(d.get('justificativa') or '').strip(),
             operadores_json=json.dumps(ops, ensure_ascii=False), n_operadores=n_op,
             usuario_ini=session.get('nome', ''), inicio=datetime.utcnow(),
             estado='EM_ANDAMENTO')
    if tipo == TIPO_MONOVIA:
        linha = get_linha(d.get('linha'))
        ativ.monovia_linha = linha['id']
        ativ.odo_ini = _odo_atual(linha['id'], db)
    db.add(ativ)
    db.flush()

    lote.estado = 'EM_ATIVIDADE'
    lote.atividade_atual_id = ativ.id
    if lote.card_id:
        card = db.query(CTX['Card']).get(lote.card_id)
        if card and card.estado in ESTADOS_PINTURA:
            card.estado = ST_EM_PINTURA
    db.commit()
    return jsonify({'sucesso': True, 'atividade': ativ.to_dict()})


@bp.route('/api/pintura/atividade/destino', methods=['POST'])
@_lr('pintura', 'banho')
def api_ativ_destino():
    """Troca monovia <-> estacionária numa carga JÁ INICIADA (erro do operador).

    O cronômetro e os operadores continuam os mesmos: só muda para onde as peças
    estão indo. Ao virar monovia, o odômetro é marcado agora — o trecho medido
    cobre o que ainda falta carregar, então os metros ficam subestimados. É o
    preço de corrigir no meio; o tempo, que é o que interessa, fica intacto.
    """
    d = request.json or {}
    novo = (d.get('tipo') or '').upper()
    if novo not in (TIPO_MONOVIA, TIPO_ESTACIONARIA):
        return jsonify({'sucesso': False, 'erro': 'Destino inválido.'}), 400
    db = _db()
    ativ = db.query(MODELOS['atividade']).get(int(d.get('id', 0)))
    if not ativ or ativ.estado != 'EM_ANDAMENTO':
        return jsonify({'sucesso': False, 'erro': 'Atividade não está em andamento.'}), 404
    if ativ.tipo not in (TIPO_MONOVIA, TIPO_ESTACIONARIA, TIPO_OP_NOVA):
        return jsonify({'sucesso': False,
                        'erro': 'Só é possível trocar o destino de um carregamento.'}), 400
    if ativ.tipo == TIPO_OP_NOVA:
        ativ.subtipo = 'monovia' if novo == TIPO_MONOVIA else 'estacionaria'
    else:
        ativ.tipo = novo
    if novo == TIPO_MONOVIA:
        linha = get_linha(d.get('linha'))
        ativ.monovia_linha = linha['id']
        if ativ.odo_ini is None:
            ativ.odo_ini = _odo_atual(linha['id'], db)
    else:
        ativ.monovia_linha = ''
        ativ.odo_ini = None
        ativ.odo_fim = None
    obs = (d.get('motivo') or '').strip()
    marca = f'[destino corrigido para {TIPOS_LBL.get(novo, novo)}'
    marca += f': {obs}]' if obs else ']'
    ativ.observacao = ((ativ.observacao or '') + ' ' + marca).strip()
    db.commit()
    return jsonify({'sucesso': True, 'atividade': ativ.to_dict()})


@bp.route('/api/pintura/atividade/pausar', methods=['POST'])
@_lr('pintura', 'banho')
def api_ativ_pausar():
    d = request.json or {}
    db = _db()
    ativ = db.query(MODELOS['atividade']).get(int(d.get('id', 0)))
    if not ativ or ativ.estado != 'EM_ANDAMENTO':
        return jsonify({'sucesso': False, 'erro': 'Atividade não está em andamento.'}), 404
    agora = datetime.utcnow()
    if ativ.pausado:
        _fechar_pausa(ativ, agora)
    else:
        motivo = (d.get('motivo') or '').strip()
        if not motivo:
            return jsonify({'sucesso': False, 'erro': 'Informe o motivo da parada.'}), 400
        ativ.pausado = 1
        ativ.pausa_inicio = agora
        ativ.pausa_motivo = motivo[:60]
    db.commit()
    return jsonify({'sucesso': True, 'pausado': bool(ativ.pausado)})


@bp.route('/api/pintura/atividade/finalizar', methods=['POST'])
@_lr('pintura', 'banho')
def api_ativ_finalizar():
    d = request.json or {}
    db = _db()
    L, A = MODELOS['lote'], MODELOS['atividade']
    ativ = db.query(A).get(int(d.get('id', 0)))
    if not ativ or ativ.estado != 'EM_ANDAMENTO':
        return jsonify({'sucesso': False, 'erro': 'Atividade não está em andamento.'}), 404
    lote = db.query(L).get(ativ.lote_id)
    agora = datetime.utcnow()
    _fechar_pausa(ativ, agora)
    ativ.fim = agora
    bruto = (ativ.fim - ativ.inicio).total_seconds()
    ativ.minutos = round(max(0, bruto - (ativ.pausa_acumulada_seg or 0)) / 60, 1)
    ativ.observacao = (d.get('observacao') or '').strip()
    ativ.usuario_fim = session.get('nome', '')
    ativ.estado = 'FINALIZADA'
    if lote:                       # foto do lote AGORA (antes de qualquer repasse)
        _ld = lote.to_dict()
        ativ.qtd_lote = _ld['quantidade']
        ativ.area_lote = _ld['area_total']

    metricas = {}
    if ativ.tipo == TIPO_MONOVIA or (ativ.tipo == TIPO_OP_NOVA and ativ.monovia_linha):
        linha = get_linha(ativ.monovia_linha)
        ativ.odo_fim = _odo_atual(linha['id'], db)
        if d.get('metros_manual'):
            try:
                ativ.odo_fim = (ativ.odo_ini or 0) + float(d['metros_manual'])
            except (ValueError, TypeError):
                pass
        metricas = metricas_carga(ativ, lote, linha)
        ativ.n_ganchos = metricas['ganchos']

    proxima = None
    if ativ.tipo == TIPO_DESCARGA:
        itens = lote.itens()
        total = sum(int(i.get('quantidade') or 0) for i in itens)
        try:
            repasse = max(0, min(int(d.get('qtd_repasse') or 0), total))
        except (ValueError, TypeError):
            repasse = 0
        ativ.qtd_repasse = repasse
        ativ.qtd_ok = total - repasse
        ativ.motivo_repasse = (d.get('motivo_repasse') or '')[:120]
        lote.atividade_atual_id = None
        if repasse > 0:
            # Peças com falha de pintura NÃO descem: seguem penduradas para outro
            # passe pela cabine. O lote continua na linha, com o odômetro marcado
            # para esperar uma volta completa do trilho.
            fica, _desceu = _separar_retrabalho(itens, repasse)
            for it in fica:
                it['tipo'] = lote.tipo or 'Normal'   # repasse não é retrabalho de banho
            linha_r = get_linha(ativ.monovia_linha or
                                (db.query(A).get(lote.carga_ativ_id).monovia_linha
                                 if lote.carga_ativ_id else None))
            lote.itens_json = json.dumps(fica, ensure_ascii=False)
            lote.quantidade = repasse
            lote.voltas = (lote.voltas or 1) + 1
            lote.odo_repasse = _odo_atual(linha_r['id'], db)
            lote.motivo_repasse = ativ.motivo_repasse
            lote.estado = 'NA_LINHA'
        else:
            lote.estado = 'FINALIZADO'
            lote.finalizado_em = agora

    elif ativ.tipo in (TIPO_MONOVIA, TIPO_ESTACIONARIA, TIPO_OP_NOVA):
        # As peças entraram na linha e o CESTO volta ao pré-banho (está vazio).
        # Com a descarga ligada, o LOTE continua vivo até ser descarregado — é o
        # que permite medir tempo de forno e repasse. Com ela desligada (1ª fase),
        # o lote encerra aqui: sem apontamento de descarga, ele ficaria pendurado
        # para sempre na tela.
        lote.na_linha_desde = agora
        lote.carga_ativ_id = ativ.id
        lote.atividade_atual_id = None
        if descarga_ativa():
            lote.estado = 'NA_LINHA'
        else:
            lote.estado = 'FINALIZADO'
            lote.finalizado_em = agora
        lote.destino = (TIPO_MONOVIA if ativ.tipo == TIPO_MONOVIA else
                        TIPO_ESTACIONARIA if ativ.tipo == TIPO_ESTACIONARIA else
                        (ativ.subtipo or '').upper())
        _liberar_cesto(db, lote)
        _resumo_no_card(db, lote, ativ)

    elif ativ.tipo == TIPO_ARMAZENAMENTO:
        # peças saem do cesto e continuam aguardando pintura
        local = ativ.subtipo or 'Armazenado'
        lote.local = ' · '.join(x for x in [local, ativ.justificativa, ativ.observacao] if x)
        lote.estado = 'AGUARDANDO'
        lote.criado_em = agora            # recomeça a contagem de espera
        lote.atividade_atual_id = None
        _liberar_cesto(db, lote, 'Peças armazenadas: ' + (local or ''))

    elif ativ.tipo == TIPO_PREPARACAO:
        destino = (d.get('destino') or '').upper()
        ativ.destino = destino
        lote.estado = 'AGUARDANDO'
        lote.atividade_atual_id = None
        if destino in ('ARM_PALETE', 'ARM_ARARA', TIPO_ARMAZENAMENTO):
            onde = {'ARM_PALETE': 'Palete', 'ARM_ARARA': 'Arara'}.get(destino, 'Armazenado')
            lote.local = ' · '.join(x for x in [onde, ativ.observacao] if x)
            lote.criado_em = agora
            _liberar_cesto(db, lote, 'Peças armazenadas: ' + onde)
        elif destino in (TIPO_MONOVIA, TIPO_ESTACIONARIA):
            proxima = destino          # a tela abre o modal do carregamento em seguida

    db.commit()
    return jsonify({'sucesso': True, 'minutos': ativ.minutos, 'metricas': metricas,
                    'proxima': proxima, 'lote_id': lote.id})


def _resumo_no_card(db, lote, ativ):
    """Espelha o carregamento no cesto de origem, para os relatórios do cesto."""
    if not lote.card_id:
        return
    card = db.query(CTX['Card']).get(lote.card_id)
    if not card:
        return
    card.pintura_inicio = ativ.inicio
    card.pintura_fim = ativ.fim
    card.pintura_minutos = ativ.minutos
    card.operador_pintura_inicio = ativ.usuario_ini
    card.operador_pintura_fim = ativ.usuario_fim
    card.obs_pintura = ativ.observacao
    card.monovia_linha = ativ.monovia_linha or ''
    card.odo_carga_ini = ativ.odo_ini
    card.odo_carga_fim = ativ.odo_fim
    card.n_ganchos = ativ.n_ganchos or 0


# ═════════════════════════════════════════════════════════════════════════════
# CARREGAR OP NOVA (sem vínculo com cesto)
# ═════════════════════════════════════════════════════════════════════════════
@bp.route('/api/pintura/op_nova', methods=['POST'])
@_lr('pintura', 'banho')
def api_op_nova():
    d = request.json or {}
    destino = (d.get('destino') or '').lower()
    if destino not in ('monovia', 'estacionaria'):
        return jsonify({'sucesso': False, 'erro': 'Escolha monovia ou estacionária.'}), 400
    itens = _norm_itens(d.get('itens'))
    if not itens:
        return jsonify({'sucesso': False, 'erro': 'Cadastre ao menos uma OP.'}), 400
    db = _db()
    L, A = MODELOS['lote'], MODELOS['atividade']
    lote = L(card_id=None, numero_cesto=0, origem='OP_NOVA', estado='EM_ATIVIDADE',
             com_cesto=0, itens_json=json.dumps(itens, ensure_ascii=False),
             quantidade=sum(i['quantidade'] for i in itens),
             processo=(d.get('processo') or '').strip(),
             tipo='Retrabalho' if any(i['tipo'] == 'Retrabalho' for i in itens) else 'Normal',
             observacao=(d.get('observacao') or '').strip(),
             local='OP avulsa (sem cesto)')
    db.add(lote)
    db.flush()

    try:
        n_op = int(d.get('n_operadores', 1))
        n_op = n_op if n_op in (1, 2, 3, 4) else 1
    except (ValueError, TypeError):
        n_op = 1
    ops = _normalizar_operadores(db, d.get('operadores'), n_op)
    ativ = A(lote_id=lote.id, card_id=None, numero_cesto=0, tipo=TIPO_OP_NOVA,
             subtipo=destino, operadores_json=json.dumps(ops, ensure_ascii=False),
             n_operadores=n_op, usuario_ini=session.get('nome', ''),
             inicio=datetime.utcnow(), estado='EM_ANDAMENTO')
    if destino == 'monovia':
        linha = get_linha(d.get('linha'))
        ativ.monovia_linha = linha['id']
        ativ.odo_ini = _odo_atual(linha['id'], db)
    db.add(ativ)
    db.flush()
    lote.atividade_atual_id = ativ.id
    db.commit()
    return jsonify({'sucesso': True, 'lote_id': lote.id, 'atividade': ativ.to_dict()})


# ═════════════════════════════════════════════════════════════════════════════
# RETRABALHO
# ═════════════════════════════════════════════════════════════════════════════
def _separar_retrabalho(itens, quantidade):
    """Tira `quantidade` peças do lote (do primeiro item em diante) e devolve
    (itens_retrabalho, itens_restantes)."""
    falta = int(quantidade)
    retrab, resto = [], []
    for it in itens:
        q = int(it.get('quantidade') or 0)
        usar = min(q, falta) if falta > 0 else 0
        falta -= usar
        if usar > 0:
            r = dict(it)
            r['quantidade'] = usar
            r['tipo'] = 'Retrabalho'
            retrab.append(r)
        if q - usar > 0:
            s = dict(it)
            s['quantidade'] = q - usar
            resto.append(s)
    return retrab, resto


@bp.route('/api/pintura/retrabalho', methods=['POST'])
@_lr('pintura', 'banho')
def api_retrabalho():
    """Manda peças de volta ao pré-banho.

    reposicionamento=True  -> cesto em AGUARDANDO_MONTAGEM (pré-banho remonta)
    reposicionamento=False -> cesto em PREENCHER (cadastro na hora → fila do banho)

    Retrabalho parcial: as peças de retrabalho ficam no cesto e o restante vira um
    lote SEM cesto — por isso é obrigatório informar onde o restante foi colocado.
    """
    d = request.json or {}
    db = _db()
    L, A, Card = MODELOS['lote'], MODELOS['atividade'], CTX['Card']
    lote = db.query(L).get(int(d.get('lote_id') or 0))
    if not lote:
        return jsonify({'sucesso': False, 'erro': 'Lote não encontrado.'}), 404
    itens = lote.itens()
    total = sum(int(i.get('quantidade') or 0) for i in itens)
    todas = bool(d.get('todas'))
    try:
        qtd = total if todas else int(d.get('quantidade') or 0)
    except (ValueError, TypeError):
        qtd = 0
    if qtd <= 0 or qtd > total:
        return jsonify({'sucesso': False, 'erro': f'Quantidade inválida (1 a {total}).'}), 400
    parcial = qtd < total
    if parcial and not (d.get('local_restante') or '').strip():
        return jsonify({'sucesso': False,
                        'erro': 'Informe onde ficaram as peças que não vão para retrabalho.'}), 400

    retrab, resto = _separar_retrabalho(itens, qtd)
    motivo = (d.get('motivo') or 'Retrabalho da pintura').strip()
    for it in retrab:
        it['tipo'] = 'Retrabalho'
        it['motivo'] = motivo
    repos = bool(d.get('reposicionamento'))

    # nº do cesto: o mesmo de origem (as peças estão nele). Se o lote já estava sem
    # cesto (palete/arara), o operador precisa informar um número livre.
    numero = lote.numero_cesto or 0
    if d.get('numero_cesto'):
        try:
            numero = int(d['numero_cesto'])
        except (ValueError, TypeError):
            pass
    if not numero:
        return jsonify({'sucesso': False,
                        'erro': 'Informe o número do cesto para o retrabalho.'}), 400

    # o cesto antigo sai dos ativos antes de o novo entrar (índice único por número)
    antigo = db.query(Card).get(lote.card_id) if lote.card_id else None
    if antigo and antigo.estado in ESTADOS_PINTURA:
        antigo.estado = CTX['ST_CONCLUIDO']
        db.flush()
    if db.query(Card).filter(Card.numero_cesto == numero,
                             Card.estado.in_(CTX['ESTADOS_ATIVOS'])).first():
        return jsonify({'sucesso': False, 'erro': f'O cesto {numero} está em uso.'}), 400

    agora = datetime.utcnow()
    novo = Card(estado=(ST_AG_MONTAGEM if repos else CTX['ST_PREENCHER']),
                numero_cesto=numero, processo=lote.processo or '',
                tipo='Retrabalho', motivo_retrabalho=motivo,
                itens_json=json.dumps(retrab, ensure_ascii=False),
                ordem=retrab[0]['ordem'] if retrab else '',
                material=retrab[0]['material'] if retrab else '',
                texto_breve=retrab[0]['texto_breve'] if retrab else '',
                quantidade=sum(i['quantidade'] for i in retrab),
                observacao=f'Retrabalho vindo da pintura (lote #{lote.id}).',
                card_origem_id=lote.card_id, lote_origem_id=lote.id,
                cadastrado_por=session.get('nome', ''), criado_em=agora)
    if not repos:
        # sem remontagem não há tempo de preparação: o cadastro é confirmado na hora
        novo.prep_inicio = agora
        novo.prep_fim = agora
        novo.prep_minutos = 0
    db.add(novo)
    db.flush()

    # registro da decisão (sem cronômetro)
    db.add(A(lote_id=lote.id, card_id=lote.card_id, numero_cesto=lote.numero_cesto or 0,
             tipo=TIPO_RETRABALHO,
             subtipo=('Todas as peças' if todas else f'{qtd} de {total} peças'),
             justificativa=motivo,
             observacao=('Necessita reposicionamento pelo pré-banho.' if repos
                         else 'Sem reposicionamento — volta direto para o banho.'),
             operadores_json=json.dumps(
                 [{'nome': session.get('nome', ''), 'matricula': session.get('usuario', '')}],
                 ensure_ascii=False),
             n_operadores=1, usuario_ini=session.get('nome', ''),
             usuario_fim=session.get('nome', ''),
             inicio=agora, fim=agora, minutos=0, estado='FINALIZADA'))

    if parcial:
        db.add(L(card_id=None, numero_cesto=lote.numero_cesto, origem='RESTO_RETRABALHO',
                 estado='AGUARDANDO', com_cesto=0,
                 itens_json=json.dumps(resto, ensure_ascii=False),
                 quantidade=sum(int(i.get('quantidade') or 0) for i in resto),
                 processo=lote.processo or '', tipo=lote.tipo or 'Normal',
                 observacao=lote.observacao or '',
                 local=(d.get('local_restante') or '').strip(), criado_em=agora))
    lote.estado = 'FINALIZADO'
    lote.finalizado_em = agora
    lote.destino = 'RETRABALHO'
    lote.atividade_atual_id = None
    db.commit()
    return jsonify({'sucesso': True, 'card_id': novo.id, 'numero_cesto': numero,
                    'reposicionamento': repos, 'parcial': parcial,
                    'card': novo.to_dict()})


@bp.route('/api/pintura/retrabalho/cadastrar', methods=['POST'])
@_lr('pintura', 'banho')
def api_retrabalho_cadastrar():
    """Confirma o cadastro do cesto de retrabalho (caso SEM reposicionamento) e
    manda direto para a fila do banho."""
    d = request.json or {}
    db = _db()
    Card = CTX['Card']
    card = db.query(Card).get(int(d.get('id', 0)))
    if not card:
        return jsonify({'sucesso': False, 'erro': 'Cesto não encontrado.'}), 404
    itens = _norm_itens(d.get('itens'))
    if not itens:
        return jsonify({'sucesso': False, 'erro': 'Cadastre ao menos uma OP.'}), 400
    for it in itens:                       # item travado como retrabalho
        it['tipo'] = 'Retrabalho'
        if not it.get('motivo'):
            it['motivo'] = card.motivo_retrabalho or 'Retrabalho da pintura'
    card.itens_json = json.dumps(itens, ensure_ascii=False)
    card.ordem = itens[0]['ordem']
    card.material = itens[0]['material']
    card.texto_breve = itens[0]['texto_breve']
    card.quantidade = sum(i['quantidade'] for i in itens)
    card.tipo = 'Retrabalho'
    if d.get('processo'):
        card.processo = (d.get('processo') or '').strip()
    if d.get('observacao') is not None:
        card.observacao = (d.get('observacao') or '').strip()
    if not card.prep_fim:
        card.prep_inicio = card.prep_inicio or datetime.utcnow()
        card.prep_fim = datetime.utcnow()
        card.prep_minutos = card.prep_minutos or 0
    card.estado = CTX['ST_FILA_BANHO']
    db.commit()
    return jsonify({'sucesso': True})


# ═════════════════════════════════════════════════════════════════════════════
# TELEMETRIA — ingestão e consulta
# ═════════════════════════════════════════════════════════════════════════════
@bp.route('/api/monovia/amostra', methods=['POST'])
def api_monovia_amostra():
    if not _token_valido():
        return jsonify({'sucesso': False, 'erro': 'Token inválido.'}), 401
    d = request.json or {}
    v, o = d.get('velocidade'), d.get('odometro')
    if v is None and o is None:
        return jsonify({'sucesso': False, 'erro': 'Envie velocidade e/ou odometro.'}), 400
    ts = None
    if d.get('ts'):
        try:
            ts = datetime.fromisoformat(str(d['ts']).replace('Z', ''))
        except ValueError:
            ts = None
    r = ingerir_amostra(d.get('linha'), velocidade=(float(v) if v is not None else None),
                        odometro=(float(o) if o is not None else None), ts=ts,
                        fonte=d.get('fonte', 'clp'),
                        confianca=float(d.get('confianca', 1.0)), extra=d.get('extra'))
    return jsonify({'sucesso': True, **r})


@bp.route('/api/monovia/amostras', methods=['POST'])
def api_monovia_amostras():
    if not _token_valido():
        return jsonify({'sucesso': False, 'erro': 'Token inválido.'}), 401
    d = request.json or {}
    itens = d.get('amostras') or []
    if not isinstance(itens, list):
        return jsonify({'sucesso': False, 'erro': 'amostras deve ser uma lista.'}), 400
    n, ultimo = 0, {}
    for it in sorted(itens, key=lambda x: str(x.get('ts', ''))):
        try:
            ts = datetime.fromisoformat(str(it['ts']).replace('Z', '')) if it.get('ts') else None
            ultimo = ingerir_amostra(it.get('linha') or d.get('linha'),
                                     velocidade=it.get('velocidade'), odometro=it.get('odometro'),
                                     ts=ts, fonte=it.get('fonte', d.get('fonte', 'clp')),
                                     confianca=float(it.get('confianca', 1.0)),
                                     extra=it.get('extra'))
            n += 1
        except Exception as e:
            print(f'[monovia] amostra ignorada: {e}')
    return jsonify({'sucesso': True, 'recebidas': n, **ultimo})


@bp.route('/api/monovia/deteccao', methods=['POST'])
def api_monovia_deteccao():
    """Evento da câmera. O lote é inferido pelo odômetro — a IA não precisa
    reconhecer a peça."""
    if not _token_valido():
        return jsonify({'sucesso': False, 'erro': 'Token inválido.'}), 401
    d = request.json or {}
    db = _db()
    linha = get_linha(d.get('linha'))
    estacao = d.get('estacao', 'cam1')
    odo = odometro_agora(linha['id'], db)['odometro_m']
    cand = atividades_na_estacao(estacao, linha['id'], odo, db)
    ativ_id = cand[0]['id'] if cand else None
    lote_id = cand[0]['lote_id'] if cand else None
    db.add(MODELOS['deteccao'](
        linha=linha['id'], estacao=estacao, ts=datetime.utcnow(), odometro_m=odo,
        atividade_id=ativ_id, lote_id=lote_id, n_pecas=int(d.get('n_pecas') or 0),
        area_m2=float(d.get('area_m2') or 0), confianca=float(d.get('confianca') or 0),
        imagem_ref=(d.get('imagem_ref') or '')[:255],
        extra_json=json.dumps(d.get('extra'), ensure_ascii=False) if d.get('extra') else ''))
    db.commit()
    return jsonify({'sucesso': True, 'atividade_id': ativ_id, 'lote_id': lote_id,
                    'cesto': cand[0]['numero_cesto'] if cand else None, 'odometro_m': odo})


@bp.route('/api/monovia/estado')
def api_monovia_estado():
    if not (session.get('usuario') or _token_valido()):
        return jsonify({}), 403
    db = _db()
    linha = get_linha(request.args.get('linha'))
    est = odometro_agora(linha['id'], db)
    estacoes = []
    for e in linha.get('estacoes', []):
        cs = atividades_na_estacao(e['id'], linha['id'], est['odometro_m'], db)
        estacoes.append({
            'id': e['id'], 'nome': e['nome'], 'offset_m': e['offset_m'],
            'cargas': [{'atividade_id': a['id'], 'lote_id': a['lote_id'],
                        'numero_cesto': a['numero_cesto'],
                        'ordem': (a['lote'] or {}).get('ordem', ''),
                        'quantidade': (a['lote'] or {}).get('quantidade', 0),
                        'area_total': (a['lote'] or {}).get('area_total', 0),
                        'posicao_pct': a['posicao_no_lote_pct']} for a in cs],
        })
    parada_atual = None
    if est.get('parada_desde'):
        ini = datetime.fromisoformat(est['parada_desde'].replace('Z', ''))
        parada_atual = {'desde': est['parada_desde'],
                        'segundos': int((datetime.utcnow() - ini).total_seconds())}
    return jsonify({'linha': linha['id'], 'nome': linha['nome'], **est,
                    'velocidade_nominal': linha.get('velocidade_nominal_m_min'),
                    'parada_atual': parada_atual, 'estacoes': estacoes})


@bp.route('/api/monovia/paradas')
@_lr('pintura', 'painel', 'admin')
def api_monovia_paradas():
    de, ate = _parse_datas()
    db = _db()
    M = MODELOS['parada']
    q = _filtro_periodo(db.query(M).filter_by(linha=get_linha(request.args.get('linha'))['id']),
                        M.inicio, de, ate)
    rows = q.order_by(M.inicio.desc()).limit(500).all()
    fuso = timedelta(hours=CTX['FUSO_LOCAL_HORAS'])
    return jsonify([{
        'id': r.id, 'inicio': (r.inicio - fuso).strftime('%d/%m/%Y %H:%M:%S') if r.inicio else '',
        'fim': (r.fim - fuso).strftime('%d/%m/%Y %H:%M:%S') if r.fim else '',
        'duracao_seg': r.duracao_seg, 'duracao_min': round((r.duracao_seg or 0) / 60, 1),
        'motivo': r.motivo or '', 'justificativa': r.justificativa or '',
        'turno': CTX['turno_label'](CTX['turno_num'](r.inicio)) if r.inicio else '',
        'odometro_m': round(r.odometro_m or 0, 1), 'fonte': r.fonte or '',
    } for r in rows])


@bp.route('/api/monovia/parada/justificar', methods=['POST'])
@_lr('pintura', 'admin')
def api_parada_justificar():
    d = request.json or {}
    db = _db()
    r = db.query(MODELOS['parada']).get(int(d.get('id', 0)))
    if not r:
        return jsonify({'sucesso': False, 'erro': 'Parada não encontrada.'}), 404
    r.motivo = (d.get('motivo') or '')[:60]
    r.justificativa = (d.get('justificativa') or '').strip()
    r.usuario = session.get('nome', '')
    db.commit()
    return jsonify({'sucesso': True})


@bp.route('/api/monovia/config', methods=['GET', 'POST'])
@_lr('admin')
def api_monovia_config():
    if request.method == 'POST':
        return jsonify({'sucesso': True, 'config': set_config(request.json or {})})
    return jsonify(get_config())


# ═════════════════════════════════════════════════════════════════════════════
# KPIs e export
# ═════════════════════════════════════════════════════════════════════════════
def _parse_datas():
    def pd(s):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return None
    return pd(request.args.get('de')), pd(request.args.get('ate'))


def _parse_turnos():
    raw = (request.args.get('turno', '') or '').strip()
    s = {int(x) for x in raw.replace(';', ',').split(',') if x.strip() in ('1', '2', '3')}
    return s or None


def _filtro_periodo(q, coluna, de, ate):
    fuso = timedelta(hours=CTX['FUSO_LOCAL_HORAS'])
    if de:
        q = q.filter(coluna >= datetime.combine(de, datetime.min.time()) + fuso)
    if ate:
        q = q.filter(coluna < datetime.combine(ate, datetime.min.time()) + fuso + timedelta(days=1))
    return q


def coletar_dados_pintura(de=None, ate=None, turnos=None):
    db = _db()
    A, L = MODELOS['atividade'], MODELOS['lote']
    fuso = timedelta(hours=CTX['FUSO_LOCAL_HORAS'])

    def no_periodo(dt):
        if not dt:
            return False
        d = (dt - fuso).date()
        return not ((de and d < de) or (ate and d > ate))

    todas = db.query(A).filter(A.estado == 'FINALIZADA', A.fim.isnot(None)).all()
    lista = [a for a in todas if no_periodo(a.fim)
             and (not turnos or CTX['turno_num'](a.fim) in turnos)]
    lotes = {l.id: l for l in db.query(L).all()} if lista else {}

    por_tipo, por_dia, area_dia = {}, {}, {}
    pecas = area = 0.0
    metros = 0.0
    ganchos = 0
    tempos, esperas, pausa_seg = [], [], 0
    t_descarga, t_linha, ocupacoes = [], [], []
    pecas_ok = pecas_repasse = 0
    area_processada = 0.0
    # o operador decide o destino ao pegar o cesto: cada um tem os seus números
    dest = {TIPO_MONOVIA: {'cargas': 0, 'pecas': 0, 'area': 0.0, 'tempos': [], 'metros': 0.0},
            TIPO_ESTACIONARIA: {'cargas': 0, 'pecas': 0, 'area': 0.0, 'tempos': [], 'metros': 0.0}}
    registros = []
    CARGAS = (TIPO_MONOVIA, TIPO_ESTACIONARIA, TIPO_OP_NOVA)
    for a in sorted(lista, key=lambda x: x.fim, reverse=True):
        lote = lotes.get(a.lote_id)
        d = a.to_dict()
        d['lote'] = lote.to_dict() if lote else None
        if a.odo_ini is not None and lote:
            d['metricas'] = metricas_carga(a, lote)
        registros.append(d)
        t = TIPOS_LBL.get(a.tipo, a.tipo)
        por_tipo[t] = por_tipo.get(t, 0) + 1
        if a.tipo != TIPO_RETRABALHO:
            tempos.append(a.minutos or 0)
        pausa_seg += (a.pausa_acumulada_seg or 0)
        if a.tipo in CARGAS:
            # o que ENTROU na linha (foto do momento da carga)
            q_carga = a.qtd_lote or 0
            ar_carga = a.area_lote or 0
            pecas += q_carga
            area += ar_carga
            # OP nova cai no destino escolhido no cadastro (subtipo)
            k = (a.tipo if a.tipo in dest
                 else (TIPO_MONOVIA if (a.subtipo or '').lower() == 'monovia'
                       else TIPO_ESTACIONARIA))
            dest[k]['cargas'] += 1
            dest[k]['pecas'] += q_carga
            dest[k]['area'] = round(dest[k]['area'] + ar_carga, 3)
            dest[k]['tempos'].append(a.minutos or 0)
            if a.odo_ini is not None and a.odo_fim is not None:
                dest[k]['metros'] = round(dest[k]['metros'] + max(0, a.odo_fim - a.odo_ini), 2)
            if d.get('metricas', {}).get('ocupacao_pct'):
                ocupacoes.append(d['metricas']['ocupacao_pct'])
            dia = (a.fim - fuso).date()
            por_dia[dia] = por_dia.get(dia, 0) + 1
            area_dia[dia] = round(area_dia.get(dia, 0) + ar_carga, 3)
            if lote and lote.criado_em and a.inicio and a.inicio >= lote.criado_em:
                esperas.append(CTX['tempo_util_segundos'](lote.criado_em, a.inicio) / 60)
        if a.tipo == TIPO_DESCARGA:
            t_descarga.append(a.minutos or 0)
            pecas_ok += a.qtd_ok or 0
            pecas_repasse += a.qtd_repasse or 0
            # cada descarga fecha UM passe pela cabine: é essa a área efetivamente
            # pintada naquela volta (no repasse o lote é menor)
            area_processada += a.area_lote or 0
            # tempo na linha = da saída do carregamento até o início da descarga
            if lote and lote.na_linha_desde and a.inicio and a.inicio >= lote.na_linha_desde:
                t_linha.append((a.inicio - lote.na_linha_desde).total_seconds() / 60)
        if a.odo_ini is not None and a.odo_fim is not None:
            metros += max(0, a.odo_fim - a.odo_ini)
            ganchos += a.n_ganchos or 0

    Mp = MODELOS['parada']
    paradas = [p for p in _filtro_periodo(db.query(Mp), Mp.inicio, de, ate).all()
               if not turnos or CTX['turno_num'](p.inicio) in turnos]
    parada_seg = sum(p.duracao_seg or 0 for p in paradas)
    horas = sum(tempos) / 60 if tempos else 0
    dias = sorted(por_dia)

    def _bloco(k):
        b = dest[k]
        t = b.pop('tempos')
        b['media_tempo'] = round(sum(t) / len(t), 1) if t else 0
        b['media_pecas'] = round(b['pecas'] / b['cargas'], 1) if b['cargas'] else 0
        return b

    return {
        'total': len(lista),
        'carregamentos': sum(1 for a in lista if a.tipo in CARGAS),
        'monovia': _bloco(TIPO_MONOVIA),
        'estacionaria': _bloco(TIPO_ESTACIONARIA),
        'pecas': int(pecas), 'area': round(area, 2),
        'media_tempo': round(sum(tempos) / len(tempos), 1) if tempos else 0,
        'media_espera': round(sum(esperas) / len(esperas), 1) if esperas else 0,
        'total_pausa_min': round(pausa_seg / 60, 1),
        'metros': round(metros, 1), 'ganchos': ganchos,
        'area_por_metro': round(area / metros, 3) if metros else 0,
        'pecas_por_gancho': round(pecas / ganchos, 2) if ganchos else 0,
        'area_por_hora': round(area / horas, 2) if horas else 0,
        # ── aproveitamento ──
        # produção: peças que desceram boas / peças que passaram pela descarga
        'pecas_ok': pecas_ok, 'pecas_repasse': pecas_repasse,
        'aproveitamento_pct': (round(100 * pecas_ok / (pecas_ok + pecas_repasse), 1)
                               if (pecas_ok + pecas_repasse) else 0),
        'taxa_repasse_pct': (round(100 * pecas_repasse / (pecas_ok + pecas_repasse), 1)
                             if (pecas_ok + pecas_repasse) else 0),
        # ocupação: quanto da capacidade do trilho foi usada
        'ocupacao_media_pct': round(sum(ocupacoes) / len(ocupacoes), 1) if ocupacoes else 0,
        'area_processada': round(area_processada, 2),
        'descargas': len(t_descarga),
        'media_descarga': round(sum(t_descarga) / len(t_descarga), 1) if t_descarga else 0,
        'media_linha': round(sum(t_linha) / len(t_linha), 1) if t_linha else 0,
        'aguardando_agora': db.query(L).filter(L.estado == 'AGUARDANDO').count(),
        'na_linha_agora': db.query(L).filter(L.estado == 'NA_LINHA').count(),
        'por_tipo': por_tipo,
        'n_paradas': len(paradas), 'parada_min': round(parada_seg / 60, 1),
        'parada_media_min': round(parada_seg / 60 / len(paradas), 1) if paradas else 0,
        'por_dia': {d.strftime('%d/%m'): por_dia[d] for d in dias},
        'area_por_dia': {d.strftime('%d/%m'): area_dia[d] for d in dias},
        'registros': registros[:300],
    }


def resumo_por_card(card_ids=None):
    """Consolida a pintura POR CESTO, para a planilha geral.

    Um cesto pode gerar várias atividades (preparação, armazenamento, carga) e,
    depois do carregamento, o lote se desprende do cesto. Aqui juntamos tudo o
    que aconteceu com as peças daquele cesto, seguindo a cadeia de lotes.
    """
    db = _db()
    L, A = MODELOS['lote'], MODELOS['atividade']
    q = db.query(L).filter(L.card_id.isnot(None))
    if card_ids:
        q = q.filter(L.card_id.in_(list(card_ids)))
    lotes = q.all()
    if not lotes:
        return {}
    ids = [l.id for l in lotes]
    ativs = db.query(A).filter(A.lote_id.in_(ids), A.estado == 'FINALIZADA'
                               ).order_by(A.inicio).all()
    por_lote = {}
    for a in ativs:
        por_lote.setdefault(a.lote_id, []).append(a)

    out = {}
    for lote in lotes:
        la = por_lote.get(lote.id, [])
        carga = next((a for a in la if a.tipo in (TIPO_MONOVIA, TIPO_ESTACIONARIA,
                                                  TIPO_OP_NOVA)), None)
        desc = [a for a in la if a.tipo == TIPO_DESCARGA]
        prev = out.get(lote.card_id)
        d = {
            'destino': (TIPOS_LBL.get(carga.tipo) if carga else ''),
            'linha': (carga.monovia_linha if carga else ''),
            'carga_ini': carga.inicio if carga else None,
            'carga_fim': carga.fim if carga else None,
            'carga_min': round(carga.minutos or 0, 1) if carga else 0,
            'carga_operadores': (CTX['_op_txt'](carga.operadores()) if carga else ''),
            'n_oper_carga': (carga.n_operadores or 0) if carga else 0,
            'metros': (round((carga.odo_fim or 0) - (carga.odo_ini or 0), 2)
                       if carga and carga.odo_ini is not None and carga.odo_fim is not None else 0),
            'ganchos': (carga.n_ganchos or 0) if carga else 0,
            'pecas_carregadas': (carga.qtd_lote or 0) if carga else 0,
            'area_carregada': round((carga.area_lote or 0), 3) if carga else 0,
            # outras atividades sobre as mesmas peças
            'outras_min': round(sum(a.minutos or 0 for a in la
                                    if a.tipo in (TIPO_PREPARACAO, TIPO_ARMAZENAMENTO)), 1),
            'outras': ' + '.join(f'{TIPOS_LBL.get(a.tipo, a.tipo)} {a.minutos}min' for a in la
                                 if a.tipo in (TIPO_PREPARACAO, TIPO_ARMAZENAMENTO)),
            'descarga_ini': desc[0].inicio if desc else None,
            'descarga_fim': desc[-1].fim if desc else None,
            'descarga_min': round(sum(a.minutos or 0 for a in desc), 1),
            'descarga_operadores': (CTX['_op_txt'](desc[0].operadores()) if desc else ''),
            'n_oper_desc': (desc[0].n_operadores or 0) if desc else 0,
            'pecas_ok': sum(a.qtd_ok or 0 for a in desc),
            'pecas_repasse': sum(a.qtd_repasse or 0 for a in desc),
            'motivo_repasse': next((a.motivo_repasse for a in desc if a.motivo_repasse), ''),
            'voltas': lote.voltas or 1,
            'local': lote.local or '',
            'lote_criado': lote.criado_em,
        }
        # se o cesto gerou mais de um lote, fica o mais completo (com carga)
        if prev and not d['carga_ini'] and prev.get('carga_ini'):
            continue
        out[lote.card_id] = d
    return out


@bp.route('/api/pintura/dados')
@_lr('pintura', 'painel', 'admin')
def api_pintura_dados():
    de, ate = _parse_datas()
    return jsonify(coletar_dados_pintura(de, ate, _parse_turnos()))


@bp.route('/api/download/pintura')
@_lr('admin')
def download_pintura():
    from openpyxl import Workbook
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    de, ate = _parse_datas()
    dados = coletar_dados_pintura(de, ate, _parse_turnos())

    wb = Workbook()
    ws = wb.active
    ws.title = 'Pintura'
    headers = ['ID', 'Lote', 'Cesto', 'Atividade', 'Detalhe', 'OP (Ordem)', 'Código',
               'Texto breve', 'Qtd', 'Área do lote (m²)', 'Processo', 'Tipo',
               'Operadores', 'Nº oper.', 'Início - Data', 'Início - Hora',
               'Fim - Data', 'Fim - Hora', 'Tempo (min)', 'Parada (min)',
               'Paradas (por motivo)', 'Metros monovia', 'Ganchos', 'Peças/gancho',
               'm²/metro', 'Ocupação (%)', 'Passe nº', 'Desceram OK', 'Repasse',
               'Motivo do repasse', 'Destino', 'Local/armazenagem', 'Observação', 'Turno']
    fill = PatternFill("solid", fgColor="0F3D5C")
    font = Font(bold=True, color="FFFFFF", size=11)
    thin = Side(style='thin', color='D0D7DE')
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill, cell.font = fill, font
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(bottom=thin)
    ws.row_dimensions[1].height = 28

    for d in dados['registros']:
        lote = d.get('lote') or {}
        m = d.get('metricas') or {}
        itens = lote.get('itens') or [{}]
        for it in itens:
            ws.append([
                d['id'], d['lote_id'], d['numero_cesto'] or '', d['tipo_lbl'], d['detalhe'],
                it.get('ordem'), it.get('material'), it.get('texto_breve'),
                it.get('quantidade'), lote.get('area_total'), lote.get('processo'),
                it.get('tipo', lote.get('tipo')), d['operadores_txt'], d['n_operadores'],
                d['inicio_data'], d['inicio_hora'], d['fim_data'], d['fim_hora'],
                d['minutos'], d['total_pausa_min'], d['pausas']['texto'],
                m.get('metros'), m.get('ganchos'), m.get('pecas_por_gancho'),
                m.get('area_por_metro'), m.get('ocupacao_pct'),
                lote.get('voltas'), d['qtd_ok'] or '', d['qtd_repasse'] or '',
                d['motivo_repasse'], d['destino'], lote.get('local'),
                d['observacao'], d['turno_lbl'],
            ])
    ws.append([])
    lin = ws.max_row + 1
    for rot, val in [('TOTAL DE ATIVIDADES', dados['total']),
                     ('CARREGAMENTOS', dados['carregamentos']),
                     ('TOTAL DE PEÇAS CARREGADAS', dados['pecas']),
                     ('ÁREA CARREGADA (m²)', dados['area']),
                     ('ÁREA PROCESSADA (m²)', dados['area_processada']),
                     ('APROVEITAMENTO (%)', dados['aproveitamento_pct']),
                     ('PEÇAS EM REPASSE', dados['pecas_repasse']),
                     ('OCUPAÇÃO MÉDIA DO TRILHO (%)', dados['ocupacao_media_pct']),
                     ('DESCARREGAMENTOS', dados['descargas']),
                     ('TEMPO MÉDIO DE DESCARGA (min)', dados['media_descarga']),
                     ('TEMPO MÉDIO NA LINHA (min)', dados['media_linha']),
                     ('METROS DE MONOVIA', dados['metros']),
                     ('PARADAS DA MONOVIA (nº / min)',
                      f"{dados['n_paradas']} / {dados['parada_min']}")]:
        ws.append([rot, val])
    for r in range(lin, lin + 14):
        ws.cell(row=r, column=1).font = Font(bold=True)
        ws.cell(row=r, column=2).font = Font(bold=True)
    larg = [6, 7, 7, 22, 20, 14, 14, 28, 7, 13, 20, 12, 26, 9, 14, 12, 14, 12,
            12, 12, 26, 14, 9, 12, 11, 12, 9, 12, 10, 22, 14, 26, 26, 10]
    for i, w in enumerate(larg, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    ws.freeze_panes = 'A2'
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    return send_file(buf, as_attachment=True, download_name=f'pintura_{stamp}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
