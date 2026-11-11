"""Teste ponta a ponta do módulo de pintura (via HTTP, como o navegador faz).

    python teste_pintura.py

Cobre os seis lançamentos: monovia, estacionária, OP nova, preparação para
pintura (com destino armazenamento), armazenamento em palete/arara e retrabalho
(total com reposicionamento e parcial sem reposicionamento).
"""
import warnings
import io
warnings.filterwarnings('ignore')
import re
from datetime import datetime, timedelta

import app
import pintura

Card, Session = app.Card, app.Session
OK, FALHA = [], []


def checar(nome, cond, extra=''):
    (OK if cond else FALHA).append(nome)
    print(f"  {'OK  ' if cond else 'FALHA'} · {nome}{(' — ' + str(extra)) if extra else ''}")


def limpar():
    db = Session()
    db.query(pintura.MODELOS['atividade']).delete(synchronize_session=False)
    db.query(pintura.MODELOS['lote']).delete(synchronize_session=False)
    for m in ('amostra', 'parada', 'deteccao', 'estado'):
        db.query(pintura.MODELOS[m]).delete(synchronize_session=False)
    db.query(Card).filter(Card.observacao.like('T:%')).delete(synchronize_session=False)
    # registros de velocidade criados pelos testes (nomes "T: ...")
    V = pintura.MODELOS['vel_log']
    db.query(V).filter(V.nome.like('T:%')).delete(synchronize_session=False)
    db.commit()


def _avancar_ate_descarga(lote_id, volta_completa=False):
    """Simula o trajeto do lote ate a estacao de descarga.

    A chegada e por tempo (offset / velocidade), entao basta recuar o relogio
    da carga — e, no repasse, o instante em que o lote voltou para a linha.
    """
    cfg = pintura.get_linha()
    v = float(cfg.get('velocidade_nominal_m_min') or 1.4)
    dist = float(cfg.get('perimetro_m') or 120.0) if volta_completa else \
        next((e['offset_m'] for e in cfg['estacoes'] if e['id'] == 'descarga'), 90.0)
    atraso = timedelta(minutes=(dist / v) + 1)
    db = Session()
    lote = db.query(pintura.MODELOS['lote']).get(lote_id)
    if lote.na_linha_desde:
        lote.na_linha_desde -= atraso
    if lote.carga_ativ_id:
        a = db.query(pintura.MODELOS['atividade']).get(lote.carga_ativ_id)
        if a and a.fim:
            a.inicio -= atraso
            a.fim -= atraso
    db.commit()


def durar(ativ_id, minutos):
    """Recua o início da atividade para simular N minutos de carregamento.
    Os metros saem de velocidade × tempo, então o tempo é a régua."""
    db = Session()
    a = db.query(pintura.MODELOS['atividade']).get(ativ_id)
    a.inicio = datetime.utcnow() - timedelta(minutes=minutos)
    db.commit()


def usuario(login, nome, perfis):
    from werkzeug.security import generate_password_hash
    db = Session()
    u = db.query(app.Usuario).filter_by(login=login).first()
    if not u:
        db.add(app.Usuario(login=login, nome=nome, senha_hash=generate_password_hash('123'),
                           perfil=perfis.split(',')[0], perfis=perfis))
        db.commit()


def cesto_no_banho(numero, qtd, ordem):
    """Cria um cesto que já passou pela preparação e está em banho."""
    db = Session()
    agora = datetime.utcnow()
    c = Card(estado=app.ST_EM_BANHO, numero_cesto=numero, processo='AÇO SEM OXIDAÇÃO',
             tipo='Normal', ordem=ordem, material='300123', quantidade=qtd,
             texto_breve='PEÇA TESTE', observacao='T:teste',
             itens_json=f'[{{"ordem":"{ordem}","material":"300123",'
                        f'"texto_breve":"PEÇA TESTE","quantidade":{qtd},"tipo":"Normal","motivo":""}}]',
             prep_inicio=agora - timedelta(hours=3), prep_fim=agora - timedelta(hours=2, minutes=30),
             prep_minutos=30, banho_inicio=agora - timedelta(hours=2))
    db.add(c)
    db.commit()
    return c.id


def main():
    limpar()
    # os testes de repasse/forno precisam da descarga ligada; a 1ª fase (item 14)
    # testa o comportamento padrao, com ela desligada
    pintura.set_config(dict(pintura.get_config(), descarga_ativa=True))
    usuario('pint', 'Op Pintura', 'pintura,banho')
    usuario('prep1', 'Op Preparação', 'prep')
    cli = app.app.test_client()
    cli.post('/login', data={'usuario': 'pint', 'senha': '123'}, follow_redirects=True)

    print('\n1) SAÍDA DO BANHO → AGUARDANDO PINTURA')
    cid = cesto_no_banho(1, 120, '900001')
    r = cli.post('/api/banho/finalizar', json={'id': cid, 'obs_banho': 'ok'}).get_json()
    checar('cesto vai para FILA_PINTURA', r.get('proximo') == 'FILA_PINTURA')
    p = cli.get('/api/pintura/painel').get_json()
    checar('lote aparece na tela do operador', len(p['cestos']) == 1)
    lote1 = p['cestos'][0]['id']
    checar('cesto não volta ao pré-banho', not [g for g in cli.get('/api/cestos').get_json()
                                                if g['numero'] == 1 and not g['ocupado']])

    print('\n2) CARREGAR MONOVIA (com telemetria)')
    for _ in range(4):
        cli.post('/api/monovia/amostra', json={'linha': 'MV1', 'velocidade': 3.5, 'fonte': 'clp'})
    r = cli.post('/api/pintura/atividade/iniciar',
                 json={'lote_id': lote1, 'tipo': 'MONOVIA', 'n_operadores': 2,
                       'operadores': [{'nome': 'João', 'matricula': 'prep1'},
                                      {'nome': 'Maria', 'matricula': ''}]}).get_json()
    checar('atividade iniciada', r.get('sucesso'), r.get('erro'))
    aid = r['atividade']['id']
    checar('carga vinculada à linha da monovia',
           r['atividade']['monovia_linha'] == 'MV1', r['atividade']['monovia_linha'])
    checar('2 operadores gravados', len(r['atividade']['operadores']) == 2)
    r = cli.post('/api/pintura/atividade/pausar', json={'id': aid, 'motivo': 'Café'}).get_json()
    checar('parada lançada', r.get('pausado') is True)
    r = cli.post('/api/pintura/atividade/pausar', json={'id': aid}).get_json()
    checar('parada encerrada', r.get('pausado') is False)
    durar(aid, 5)                      # 5 min a 1,4 m/min = 7,0 m
    r = cli.post('/api/pintura/atividade/finalizar',
                 json={'id': aid, 'observacao': 'carga ok'}).get_json()
    checar('carga finalizada', r.get('sucesso'), r.get('erro'))
    checar('metros = velocidade x tempo', r['metricas']['metros'] == 7.0, r['metricas'])
    checar('empilhamento calculado', r['metricas']['ganchos'] == 17, r['metricas'])
    checar('peças/gancho', r['metricas']['pecas_por_gancho'] == 7.06, r['metricas'])
    g = [x for x in cli.get('/api/cestos').get_json() if x['numero'] == 1][0]
    checar('cesto liberado para o pré-banho', not g['ocupado'])

    print('\n3) PREPARAÇÃO PARA PINTURA → ARMAZENAMENTO (peças sem cesto)')
    cid2 = cesto_no_banho(2, 60, '900002')
    cli.post('/api/banho/finalizar', json={'id': cid2})
    lote2 = [l for l in cli.get('/api/pintura/painel').get_json()['cestos']][0]['id']
    r = cli.post('/api/pintura/atividade/iniciar',
                 json={'lote_id': lote2, 'tipo': 'PREPARACAO', 'subtipo': 'Isolamento',
                       'n_operadores': 1}).get_json()
    checar('preparação iniciada', r.get('sucesso'), r.get('erro'))
    r = cli.post('/api/pintura/atividade/finalizar',
                 json={'id': r['atividade']['id'], 'observacao': 'isolado',
                       'destino': 'ARM_PALETE'}).get_json()
    checar('preparação finalizada', r.get('sucesso'), r.get('erro'))
    p = cli.get('/api/pintura/painel').get_json()
    checar('peças voltam como SEM CESTO', len(p['sem_cesto']) == 1)
    checar('armazenamento em palete registrado sem texto obrigatorio',
           'Palete' in p['sem_cesto'][0]['local'], p['sem_cesto'][0]['local'])
    checar('mostra o cesto de origem', p['sem_cesto'][0]['numero_cesto'] == 2)
    g = [x for x in cli.get('/api/cestos').get_json() if x['numero'] == 2][0]
    checar('cesto 2 liberado', not g['ocupado'])

    print('\n4) LOTE SEM CESTO → CARREGAR ESTACIONÁRIA')
    lote3 = p['sem_cesto'][0]['id']
    r = cli.post('/api/pintura/atividade/iniciar',
                 json={'lote_id': lote3, 'tipo': 'ESTACIONARIA', 'n_operadores': 1}).get_json()
    checar('estacionária iniciada', r.get('sucesso'), r.get('erro'))
    r = cli.post('/api/pintura/atividade/finalizar',
                 json={'id': r['atividade']['id'], 'observacao': 'ok'}).get_json()
    checar('estacionária finalizada', r.get('sucesso'), r.get('erro'))
    checar('lote sai da tela', len(cli.get('/api/pintura/painel').get_json()['sem_cesto']) == 0)

    print('\n5) CARREGAR OP NOVA (sem vínculo com cesto)')
    r = cli.post('/api/pintura/op_nova',
                 json={'destino': 'monovia', 'processo': 'INOX', 'n_operadores': 1,
                       'itens': [{'ordem': '900555', 'material': '300123',
                                  'texto_breve': 'AVULSA', 'quantidade': 40}]}).get_json()
    checar('OP nova criada', r.get('sucesso'), r.get('erro'))
    r2 = cli.post('/api/pintura/atividade/finalizar',
                  json={'id': r['atividade']['id'], 'observacao': 'avulsa ok'}).get_json()
    checar('OP nova finalizada', r2.get('sucesso'), r2.get('erro'))

    print('\n6) ARMAZENAMENTO DIRETO EM PALETE/ARARA')
    cid3 = cesto_no_banho(3, 90, '900003')
    cli.post('/api/banho/finalizar', json={'id': cid3})
    lote4 = cli.get('/api/pintura/painel').get_json()['cestos'][0]['id']
    r = cli.post('/api/pintura/atividade/iniciar',
                 json={'lote_id': lote4, 'tipo': 'ARMAZENAMENTO', 'subtipo': 'Arara',
                       'n_operadores': 2}).get_json()
    checar('armazenamento iniciado', r.get('sucesso'), r.get('erro'))
    r = cli.post('/api/pintura/atividade/finalizar',
                 json={'id': r['atividade']['id'], 'observacao': 'arara 3'}).get_json()
    checar('armazenamento finalizado', r.get('sucesso'), r.get('erro'))
    p = cli.get('/api/pintura/painel').get_json()
    checar('peças ficam aguardando sem cesto', len(p['sem_cesto']) == 1)
    checar('cesto 3 liberado',
           not [x for x in cli.get('/api/cestos').get_json() if x['numero'] == 3][0]['ocupado'])

    print('\n7) RETRABALHO TOTAL COM REPOSICIONAMENTO')
    cid4 = cesto_no_banho(4, 50, '900004')
    cli.post('/api/banho/finalizar', json={'id': cid4})
    lote5 = [l for l in cli.get('/api/pintura/painel').get_json()['cestos']
             if l['numero_cesto'] == 4][0]['id']
    r = cli.post('/api/pintura/retrabalho',
                 json={'lote_id': lote5, 'todas': True, 'reposicionamento': True,
                       'motivo': 'Falha de camada'}).get_json()
    checar('retrabalho registrado', r.get('sucesso'), r.get('erro'))
    g = [x for x in cli.get('/api/cestos').get_json() if x['numero'] == 4][0]
    checar('cesto vai a AGUARDANDO_MONTAGEM',
           g['ocupado'] and g['card']['estado'] == 'AGUARDANDO_MONTAGEM',
           g['card']['estado'] if g['ocupado'] else 'livre')
    checar('itens travados como Retrabalho', g['card']['tipo'] == 'Retrabalho')
    cli2 = app.app.test_client()
    cli2.post('/login', data={'usuario': 'prep1', 'senha': '123'}, follow_redirects=True)
    rm = cli2.post('/api/prep/montagem/iniciar',
                   json={'id': r['card_id'], 'n_operadores': 2,
                         'operadores': [{'nome': 'Ana', 'matricula': ''}]}).get_json()
    checar('montagem iniciada no pré-banho', rm.get('sucesso'), rm.get('erro'))
    cli2.post('/api/prep/parar', json={'id': r['card_id']})
    rf = cli2.post('/api/prep/finalizar',
                   json={'id': r['card_id'], 'processo': 'AÇO COM OXIDAÇÃO',
                         'itens': g['card']['itens']}).get_json()
    checar('volta para a fila do banho', rf.get('sucesso'), rf.get('erro'))
    g = [x for x in cli.get('/api/cestos').get_json() if x['numero'] == 4][0]
    checar('estado final FILA_BANHO', g['card']['estado'] == 'FILA_BANHO', g['card']['estado'])

    print('\n8) RETRABALHO PARCIAL SEM REPOSICIONAMENTO')
    cid5 = cesto_no_banho(5, 100, '900005')
    cli.post('/api/banho/finalizar', json={'id': cid5})
    lote6 = [l for l in cli.get('/api/pintura/painel').get_json()['cestos']
             if l['numero_cesto'] == 5][0]['id']
    r = cli.post('/api/pintura/retrabalho',
                 json={'lote_id': lote6, 'todas': False, 'quantidade': 30,
                       'reposicionamento': False, 'motivo': 'Escorrimento'}).get_json()
    checar('exige local do restante', not r.get('sucesso'), r.get('erro'))
    r = cli.post('/api/pintura/retrabalho',
                 json={'lote_id': lote6, 'todas': False, 'quantidade': 30,
                       'reposicionamento': False, 'motivo': 'Escorrimento',
                       'local_restante': 'Arara 5'}).get_json()
    checar('retrabalho parcial aceito', r.get('sucesso'), r.get('erro'))
    checar('cesto com as 30 peças de retrabalho', r['card']['qtd_total'] == 30, r['card']['qtd_total'])
    p = cli.get('/api/pintura/painel').get_json()
    resto = [l for l in p['sem_cesto'] if l['quantidade'] == 70]
    checar('restante vira lote sem cesto (70 pç)', len(resto) == 1)
    rc = cli.post('/api/pintura/retrabalho/cadastrar',
                  json={'id': r['card_id'], 'itens': r['card']['itens'],
                        'processo': 'INOX'}).get_json()
    checar('cadastro confirmado', rc.get('sucesso'), rc.get('erro'))
    g = [x for x in cli.get('/api/cestos').get_json() if x['numero'] == 5][0]
    checar('vai direto para a fila do banho', g['card']['estado'] == 'FILA_BANHO', g['card']['estado'])

    print('\n9) CICLO NA LINHA E DESCARREGAMENTO')
    cid6 = cesto_no_banho(6, 40, '900006')
    cli.post('/api/banho/finalizar', json={'id': cid6})
    lote7 = [l for l in cli.get('/api/pintura/painel').get_json()['cestos']
             if l['numero_cesto'] == 6][0]['id']
    for _ in range(4):
        cli.post('/api/monovia/amostra', json={'linha': 'MV1', 'velocidade': 3.5, 'fonte': 'clp'})
    a = cli.post('/api/pintura/atividade/iniciar',
                 json={'lote_id': lote7, 'tipo': 'MONOVIA', 'n_operadores': 1}).get_json()
    durar(a['atividade']['id'], 4)
    cli.post('/api/pintura/atividade/finalizar', json={'id': a['atividade']['id']})
    p = cli.get('/api/pintura/painel').get_json()
    checar('lote fica NA LINHA apos carregar',
           any(l['id'] == lote7 for l in p['na_linha']))
    checar('cesto 6 liberado mesmo assim',
           not [x for x in cli.get('/api/cestos').get_json() if x['numero'] == 6][0]['ocupado'])
    nl = [l for l in p['na_linha'] if l['id'] == lote7][0]
    checar('ETA ate a descarga calculada', nl['linha_status']['eta_min'] > 0,
           nl['linha_status'])
    r = cli.post('/api/pintura/atividade/iniciar',
                 json={'lote_id': lote7, 'tipo': 'MONOVIA'}).get_json()
    checar('bloqueia recarregar lote que esta na linha', not r.get('sucesso'), r.get('erro'))
    # a peca leva offset/velocidade minutos ate a descarga: recuamos o relogio
    # da carga para simular esse percurso
    _avancar_ate_descarga(lote7)
    p = cli.get('/api/pintura/painel').get_json()
    checar('lote aparece pronto para descarregar',
           any(l['id'] == lote7 for l in p['prontos_descarga']),
           [l['id'] for l in p['prontos_descarga']])
    r = cli.post('/api/pintura/atividade/iniciar',
                 json={'lote_id': lote7, 'tipo': 'DESCARGA', 'n_operadores': 2}).get_json()
    checar('descarga iniciada', r.get('sucesso'), r.get('erro'))
    r = cli.post('/api/pintura/atividade/finalizar',
                 json={'id': r['atividade']['id'], 'observacao': 'ok'}).get_json()
    checar('descarga finalizada', r.get('sucesso'), r.get('erro'))
    p = cli.get('/api/pintura/painel').get_json()
    checar('lote sai da linha',
           not any(l['id'] == lote7 for l in p['prontos_descarga'] + p['na_linha']))
    d = cli.get('/api/pintura/dados').get_json()
    checar('KPI de descarga', d['descargas'] == 1, d['descargas'])

    print('\n10) REPASSE: PECAS QUE NAO DESCEM E DAO OUTRA VOLTA')
    SAP = '2291067'          # codigo real do area_peso.xlsx (0,005659 m²/peca)
    db = Session()
    c = Card(estado=app.ST_EM_BANHO, numero_cesto=9, processo='ALUMÍNIO', tipo='Normal',
             ordem='900909', material=SAP, quantidade=300, texto_breve='SUPORTE',
             observacao='T:teste',
             itens_json=('[{"ordem":"900909","material":"' + SAP +
                         '","texto_breve":"SUPORTE","quantidade":300,"tipo":"Normal","motivo":""}]'),
             prep_inicio=datetime.utcnow() - timedelta(hours=2),
             prep_fim=datetime.utcnow() - timedelta(minutes=90), prep_minutos=30,
             banho_inicio=datetime.utcnow() - timedelta(minutes=60))
    db.add(c); db.commit(); cid9 = c.id
    cli.post('/api/banho/finalizar', json={'id': cid9})
    lote9 = [l for l in cli.get('/api/pintura/painel').get_json()['cestos']
             if l['numero_cesto'] == 9][0]
    checar('area calculada do codigo SAP', abs(lote9['area_total'] - 1.698) < 0.01,
           lote9['area_total'])
    a = cli.post('/api/pintura/atividade/iniciar',
                 json={'lote_id': lote9['id'], 'tipo': 'MONOVIA', 'n_operadores': 3,
                       'operadores': [{'nome': 'Ana', 'matricula': ''},
                                      {'nome': 'Bruno', 'matricula': ''},
                                      {'nome': 'Carlos', 'matricula': ''}]}).get_json()
    checar('3 operadores no carregamento', a['atividade']['n_operadores'] == 3)
    durar(a['atividade']['id'], 10)        # 10 min a 1,4 m/min = 14,0 m
    r = cli.post('/api/pintura/atividade/finalizar',
                 json={'id': a['atividade']['id']}).get_json()
    m = r['metricas']
    checar('metros de 10 min a 1,4 m/min', m['metros'] == 14.0, m)
    checar('empilhamento: 35 ganchos em 14 m', m['ganchos'] == 35, m)
    checar('pecas por gancho', m['pecas_por_gancho'] == 8.57, m)
    checar('ocupacao do trilho calculada', m['ocupacao_pct'] == 4.0, m)

    _avancar_ate_descarga(lote9['id'])
    pr = [l for l in cli.get('/api/pintura/painel').get_json()['prontos_descarga']
          if l['id'] == lote9['id']][0]
    d1 = cli.post('/api/pintura/atividade/iniciar',
                  json={'lote_id': pr['id'], 'tipo': 'DESCARGA', 'n_operadores': 2}).get_json()
    checar('2 operadores na descarga', d1['atividade']['n_operadores'] == 2)
    r = cli.post('/api/pintura/atividade/finalizar',
                 json={'id': d1['atividade']['id'], 'qtd_repasse': 50,
                       'motivo_repasse': 'Falha de camada'}).get_json()
    checar('descarga parcial aceita', r.get('sucesso'), r.get('erro'))
    p = cli.get('/api/pintura/painel').get_json()
    volta = [l for l in p['na_linha'] + p['prontos_descarga'] if l['id'] == lote9['id']]
    checar('50 pecas voltam para outro passe',
           bool(volta) and volta[0]['quantidade'] == 50 and volta[0]['voltas'] == 2,
           f"{volta[0]['quantidade']} pc, {volta[0]['voltas']}o passe" if volta else 'sumiu')
    checar('motivo do repasse guardado',
           volta and volta[0]['motivo_repasse'] == 'Falha de camada')
    _avancar_ate_descarga(lote9['id'], volta_completa=True)
    pr2 = [l for l in cli.get('/api/pintura/painel').get_json()['prontos_descarga']
           if l['id'] == lote9['id']]
    checar('volta a aparecer na descarga apos uma volta do trilho', len(pr2) == 1)
    d2 = cli.post('/api/pintura/atividade/iniciar',
                  json={'lote_id': lote9['id'], 'tipo': 'DESCARGA', 'n_operadores': 1}).get_json()
    cli.post('/api/pintura/atividade/finalizar',
             json={'id': d2['atividade']['id'], 'qtd_repasse': 0})
    p = cli.get('/api/pintura/painel').get_json()
    checar('lote encerrado no 2o passe',
           not any(l['id'] == lote9['id'] for l in p['na_linha'] + p['prontos_descarga']))

    k = cli.get('/api/pintura/dados').get_json()
    checar('pecas carregadas nao sao reescritas pelo repasse', k['pecas'] >= 300, k['pecas'])
    checar('area processada > area carregada (conta o passe extra)',
           k['area_processada'] > k['area'], f"{k['area_processada']} vs {k['area']}")
    esperado = round(100 * k['pecas_ok'] / (k['pecas_ok'] + k['pecas_repasse']), 1)
    checar('aproveitamento coerente com OK/repasse',
           k['aproveitamento_pct'] == esperado,
           f"{k['pecas_ok']} OK / {k['pecas_repasse']} repasse = {k['aproveitamento_pct']}%")
    checar('aproveitamento + repasse = 100%',
           abs(k['aproveitamento_pct'] + k['taxa_repasse_pct'] - 100) < 0.2)
    checar('50 pecas contabilizadas em repasse', k['pecas_repasse'] == 50, k['pecas_repasse'])
    checar('ocupacao media preservada', k['ocupacao_media_pct'] > 0, k['ocupacao_media_pct'])

    print('\n11) DESTINO ESCOLHIDO PELO OPERADOR (monovia x estacionaria)')
    k = cli.get('/api/pintura/dados').get_json()
    mv, es = k.get('monovia', {}), k.get('estacionaria', {})
    checar('bloco monovia no painel', bool(mv), mv)
    checar('bloco estacionaria no painel', bool(es), es)
    checar('cargas separadas por destino',
           mv.get('cargas', 0) > 0 and es.get('cargas', 0) > 0,
           f"monovia {mv.get('cargas')} / estacionaria {es.get('cargas')}")
    checar('soma bate com o total de carregamentos',
           mv.get('cargas', 0) + es.get('cargas', 0) == k['carregamentos'],
           f"{mv.get('cargas')}+{es.get('cargas')} vs {k['carregamentos']}")
    checar('metros de trilho so na monovia',
           es.get('metros', 0) == 0 and mv.get('metros', 0) > 0,
           f"mv {mv.get('metros')} / est {es.get('metros')}")
    ger = app.app.test_client()
    ger.post('/login', data={'usuario': 'gerencia', 'senha': 'painel123'}, follow_redirects=True)
    dd = ger.get('/api/painel/dados').get_json()
    checar('painel da gerencia traz a pintura', bool(dd.get('pintura')))
    checar('painel traz os dois destinos',
           bool(dd['pintura'].get('monovia')) and bool(dd['pintura'].get('estacionaria')))

    print('\n12) ACESSO DOS USUARIOS')
    adm = app.app.test_client()
    adm.post('/login', data={'usuario': 'admin', 'senha': 'admin123'}, follow_redirects=True)
    html = adm.get('/selecionar').get_data(as_text=True)
    checar('Pintura aparece na tela de escolha do admin', 'Pintura' in html)
    checar('admin abre a tela de pintura', adm.get('/pintura').status_code == 200)
    checar('admin abre o painel da monovia', adm.get('/monovia').status_code == 200)
    # o perfil 'pintura' nao pode ser descartado ao salvar os acessos
    adm.post('/admin/usuarios', data={'acao': 'acessos', 'usuario_acessos': 'op2',
                                      'acessos': ['prep', 'pintura']}, follow_redirects=True)
    dbx = Session()
    op2 = dbx.query(app.Usuario).filter_by(login='op2').first()
    checar('perfil pintura e salvo no cadastro de usuarios',
           'pintura' in (op2.perfis or ''), op2.perfis)

    print('\n13) PLANILHAS E PAINEL')
    adm2 = app.app.test_client()
    adm2.post('/login', data={'usuario': 'admin', 'senha': 'admin123'}, follow_redirects=True)
    html = adm2.get('/dashboard').get_data(as_text=True)
    checar('botao de Excel da pintura no dashboard', "baixar('pintura')" in html)
    checar('Excel da pintura baixa', adm2.get('/api/download/pintura').status_code == 200)

    import io as _io
    from openpyxl import load_workbook
    r = adm2.get('/api/download/geral')
    checar('Excel geral baixa', r.status_code == 200)
    ws = load_workbook(_io.BytesIO(r.data)).active
    hdr = [x.value for x in ws[1]]
    for col in ('Aguardando pintura (min)', 'Tempo carga (min)', 'Tempo descarga (min)',
                'Tempo na linha/forno (min)', 'TOTAL do processo (min)',
                'TOTAL trabalhado (min)', 'TOTAL de espera (min)'):
        checar(f'coluna "{col}" na planilha geral', col in hdr)
    # a aritmetica dos totais tem que fechar em toda linha preenchida
    i_proc, i_trab, i_esp = (hdr.index('TOTAL do processo (min)'),
                             hdr.index('TOTAL trabalhado (min)'),
                             hdr.index('TOTAL de espera (min)'))
    ruim = []
    for linha in ws.iter_rows(min_row=2, values_only=True):
        if not isinstance(linha[i_proc], (int, float)):
            continue
        pr, tr, es = linha[i_proc], linha[i_trab] or 0, linha[i_esp] or 0
        if abs(pr - (tr + es)) > 0.2 and pr > 0:
            ruim.append((pr, tr, es))
    checar('processo = trabalhado + espera em todas as linhas', not ruim, ruim[:2])

    html = ger.get('/painel').get_data(as_text=True)
    checar('graficos da pintura no painel', 'graficosPintura' in html
           and 'chartDestino' in html and 'chartAprov' in html)

    print('\n14) 1a FASE: DESCARGA DESLIGADA')
    pintura.set_config(dict(pintura.get_config(), descarga_ativa=False))
    checar('descarga desligada por padrao na config nova',
           pintura.CONFIG_PADRAO['descarga_ativa'] is False)
    cid14 = cesto_no_banho(14, 90, '900014')
    cli.post('/api/banho/finalizar', json={'id': cid14})
    l14 = [l for l in cli.get('/api/pintura/painel').get_json()['cestos']
           if l['numero_cesto'] == 14][0]
    a = cli.post('/api/pintura/atividade/iniciar',
                 json={'lote_id': l14['id'], 'tipo': 'MONOVIA', 'n_operadores': 2}).get_json()
    cli.post('/api/pintura/atividade/finalizar', json={'id': a['atividade']['id']})
    pan = cli.get('/api/pintura/painel').get_json()
    checar('painel avisa que a descarga esta desligada', pan['descarga_ativa'] is False)
    checar('lote nao fica pendurado na linha',
           not any(l['id'] == l14['id'] for l in pan['na_linha'] + pan['prontos_descarga']))
    checar('cesto 14 volta livre para o pre-banho',
           not [x for x in cli.get('/api/cestos').get_json() if x['numero'] == 14][0]['ocupado'])

    print('\n15) PAINEL GERENCIAL AO VIVO')
    vivo = ger.get('/api/painel/ativos').get_json()
    checar('polling do painel traz a pintura', bool(vivo.get('pintura')))
    pt = vivo['pintura']
    for campo in ('fila', 'fila_qtd', 'fila_pecas', 'fila_area', 'espera_max_seg',
                  'carregando', 'carregando_qtd', 'hoje'):
        checar(f'campo ao vivo "{campo}"', campo in pt)
    checar('"pintado hoje" traz pecas e area',
           pt['hoje']['pecas'] > 0 and pt['hoje']['area'] > 0, pt['hoje'])
    checar('hoje separa monovia de estacionaria',
           pt['hoje']['monovia'] > 0 and pt['hoje']['estacionaria'] > 0, pt['hoje'])
    html = ger.get('/painel').get_data(as_text=True)
    checar('faixa "Pintado hoje" no painel', 'pintHoje' in html and 'hjPecas' in html)
    checar('abas de pintura no bloco ao vivo',
           "setCategoria('FILA_PINTURA')" in html and "setCategoria('CARREGANDO_PINT')" in html)
    checar('endpoint ao vivo dedicado',
           cli.get('/api/pintura/ao_vivo').status_code == 200)

    print('\n16) AJUSTES DA TELA DE PINTURA')
    html = cli.get('/pintura').get_data(as_text=True)
    abas = re.findall(r"setLayout\('(\w+)'\)", html)
    checar('6 abas na ordem pedida',
           abas[:6] == ['todos', 'fila', 'mv', 'est', 'prep', 'armaz'], abas)
    checar('"Carregar OP nova" acessivel (spec) e fora de "outras opcoes"',
           'abrirOpNova()' in html and 'Carregar OP nova' not in
           html.split('acao-grid')[1].split('</div>')[0])
    checar('campo de metros removido da finalizacao', 'Metros ocupados' not in html)
    outras = re.findall(r'acao-btn[^>]*><i[^>]*></i><span>([^<]+)</span>', html)
    checar('"outras opcoes" sem os carregamentos',
           not any('Carregar' in x for x in outras), outras)
    checar('armazenamento separado em palete e arara',
           'Armazenamento palete' in outras and 'Armazenamento arara' in outras, outras)
    dest = re.findall(r'data-dest="(\w+)"', html)
    checar('destinos da preparacao com palete e arara',
           'ARM_PALETE' in dest and 'ARM_ARARA' in dest, dest)
    checar('sem pergunta obrigatoria de como/onde armazenou',
           'Como e onde as peças foram armazenadas' not in html)

    # corrigir destino de uma carga em andamento
    cid16 = cesto_no_banho(16, 70, '900016')
    cli.post('/api/banho/finalizar', json={'id': cid16})
    l16 = [l for l in cli.get('/api/pintura/painel').get_json()['cestos']
           if l['numero_cesto'] == 16][0]
    a16 = cli.post('/api/pintura/atividade/iniciar',
                   json={'lote_id': l16['id'], 'tipo': 'MONOVIA', 'n_operadores': 2}).get_json()
    r = cli.post('/api/pintura/atividade/destino',
                 json={'id': a16['atividade']['id'], 'tipo': 'ESTACIONARIA'}).get_json()
    checar('corrige monovia -> estacionaria', r.get('sucesso'), r.get('erro'))
    checar('tipo trocado', r['atividade']['tipo'] == 'ESTACIONARIA', r.get('atividade', {}).get('tipo'))
    checar('operadores preservados na correcao', r['atividade']['n_operadores'] == 2)
    checar('correcao fica registrada na observacao',
           'destino corrigido' in (r['atividade']['observacao'] or ''), r['atividade']['observacao'])
    cli.post('/api/pintura/atividade/finalizar', json={'id': a16['atividade']['id']})

    print('\n17) CESTOS CONFIGURAVEIS E PAINEL')
    adm3 = app.app.test_client()
    adm3.post('/login', data={'usuario': 'admin', 'senha': 'admin123'}, follow_redirects=True)
    antes = len(cli.get('/api/cestos').get_json())
    adm3.post('/admin/config', data={
        'usar_jornada': 'on', 'total_cestos': '26',
        't1_nome': '1o', 't1_ini': '06:00', 't1_fim': '14:00', 't1_ativo': 'on',
        't1_dias': ['0', '1', '2', '3', '4'],
        't2_nome': '2o', 't2_ini': '14:00', 't2_fim': '22:00',
        't3_nome': '3o', 't3_ini': '22:00', 't3_fim': '06:00'}, follow_redirects=True)
    checar('quantidade de cestos configuravel',
           len(cli.get('/api/cestos').get_json()) == 26, f'{antes} -> 26')
    checar('campo na tela de configuracoes',
           'total_cestos' in adm3.get('/admin/config').get_data(as_text=True))

    d = ger.get('/api/painel/dados').get_json()
    g = d.get('geral') or {}
    for k in ('media_prep', 'media_ag_banho', 'media_banho', 'media_ag_pintura',
              'media_carga_monovia', 'media_carga_estacionaria', 'media_total'):
        checar(f'tempo medio "{k}" no bloco geral', k in g)
    checar('registros do processo trazem inicio/fim/etapa',
           bool(g.get('registros')) and 'ciclo_ini' in g['registros'][0]
           and 'etapa' in g['registros'][0])
    emcurso = [r for r in g['registros'] if not r['ciclo_fim']]
    checar('cesto em andamento fica sem tempo final',
           all(r['ciclo_total'] is None for r in emcurso), len(emcurso))

    html = ger.get('/painel').get_data(as_text=True)
    checar('aba "Todos" no seletor de setor', "setSetor('geral')" in html)
    checar('grafico Normal x Retrabalho removido', 'id="chartTipo"' not in html)
    checar('grafico de peso removido', 'id="chartPeso"' not in html)
    checar('KPI peso total removido', 'id="kPeso"' not in html)
    checar('KPI media pecas/cesto removido', 'id="kMediaPecas"' not in html)
    checar('periodo indicado na quantidade de cestos', 'periodoTag' in html)
    checar('coluna "Cadastrado por" fora da tabela', '<th>Cadastrado por</th>' not in html)
    checar('coluna "Operadores" fora da tabela', '<th>Operadores</th>' not in html)
    checar('colunas de inicio/fim do processo na tabela',
           'Início do processo' in html and 'Fim do processo' in html)
    checar('ver detalhes tambem nos lotes ao vivo', 'verDetalhesLote' in html)
    checar('detalhes mostram todas as etapas', '_etapasHTML' in html)

    print('\n18) VELOCIDADE DA MONOVIA (sem odometro)')
    cfg = pintura.get_config(forcar=True)
    checar('velocidade padrao de fabrica e 1,4 m/min',
           pintura.CONFIG_PADRAO['linhas'][0]['velocidade_nominal_m_min'] == 1.4)
    adm4 = app.app.test_client()
    adm4.post('/login', data={'usuario': 'admin', 'senha': 'admin123'}, follow_redirects=True)
    tela = adm4.get('/admin/config').get_data(as_text=True)
    checar('velocidade editavel na tela de configuracoes', 'name="vel_monovia"' in tela)
    base = dict(usar_jornada='on', total_cestos='26',
                t1_nome='1o', t1_ini='06:00', t1_fim='14:00', t1_ativo='on',
                t1_dias=['0', '1', '2', '3', '4'],
                t2_nome='2o', t2_ini='14:00', t2_fim='22:00',
                t3_nome='3o', t3_ini='22:00', t3_fim='06:00')
    adm4.post('/admin/config', data=dict(base, vel_monovia='2,5'), follow_redirects=True)
    checar('aceita virgula como separador decimal',
           pintura.get_config(True)['linhas'][0]['velocidade_nominal_m_min'] == 2.5)
    adm4.post('/admin/config', data=dict(base, vel_monovia='999'), follow_redirects=True)
    checar('rejeita velocidade fora da faixa',
           pintura.get_config(True)['linhas'][0]['velocidade_nominal_m_min'] == 2.5)
    adm4.post('/admin/config', data=dict(base, vel_monovia='1.4'), follow_redirects=True)
    checar('volta para 1,4',
           pintura.get_config(True)['linhas'][0]['velocidade_nominal_m_min'] == 1.4)

    # metros = velocidade x tempo
    cid18 = cesto_no_banho(18, 100, '900018')
    cli.post('/api/banho/finalizar', json={'id': cid18})
    l18 = [l for l in cli.get('/api/pintura/painel').get_json()['cestos']
           if l['numero_cesto'] == 18][0]
    a18 = cli.post('/api/pintura/atividade/iniciar',
                   json={'lote_id': l18['id'], 'tipo': 'MONOVIA', 'n_operadores': 1}).get_json()
    checar('carga nao grava odometro', 'odo_ini' not in a18['atividade'])
    durar(a18['atividade']['id'], 20)
    r18 = cli.post('/api/pintura/atividade/finalizar',
                   json={'id': a18['atividade']['id']}).get_json()
    checar('20 min a 1,4 m/min = 28 m', r18['metricas']['metros'] == 28.0, r18['metricas'])
    checar('metricas informam a velocidade usada', r18['metricas']['velocidade'] == 1.4)
    # estacionaria nao tem trilho correndo
    cid19 = cesto_no_banho(19, 100, '900019')
    cli.post('/api/banho/finalizar', json={'id': cid19})
    l19 = [l for l in cli.get('/api/pintura/painel').get_json()['cestos']
           if l['numero_cesto'] == 19][0]
    a19 = cli.post('/api/pintura/atividade/iniciar',
                   json={'lote_id': l19['id'], 'tipo': 'ESTACIONARIA', 'n_operadores': 1}).get_json()
    durar(a19['atividade']['id'], 20)
    r19 = cli.post('/api/pintura/atividade/finalizar',
                   json={'id': a19['atividade']['id']}).get_json()
    checar('estacionaria nao acumula metros de trilho',
           not r19.get('metricas'), r19.get('metricas'))

    # odometro fora das telas
    htmlp = cli.get('/pintura').get_data(as_text=True)
    mono = ger.get('/monovia').get_data(as_text=True)
    checar('odometro fora da tela de pintura',
           'odometro' not in htmlp.lower() and 'odômetro' not in htmlp.lower())
    checar('odometro fora do painel da monovia',
           'odômetro' not in mono.lower() and 'id="vOdo"' not in mono)
    checar('telas mostram a velocidade configurada',
           'Velocidade configurada' in htmlp and 'Velocidade configurada' in mono)
    checar('painel do operador devolve a velocidade',
           cli.get('/api/pintura/painel').get_json().get('velocidade_nominal') == 1.4)

    # migracao de quem ja tinha a config antiga salva (3.5 era o padrao velho)
    import json as _json
    dbm = Session()
    antiga = {'ativo': True, 'linha_padrao': 'MV1', 'descarga_ativa': False,
              'linhas': [{'id': 'MV1', 'nome': 'Monovia 1', 'passo_gancho_m': 0.40,
                          'area_max_gancho_m2': 1.20, 'velocidade_nominal_m_min': 3.5,
                          'perimetro_m': 120.0,
                          'estacoes': [{'id': 'descarga', 'nome': 'Descarga',
                                        'offset_m': 90.0}]}]}
    linha_cfg = dbm.query(app.Config).filter_by(chave='monovia').first()
    linha_cfg.valor = _json.dumps(antiga)
    dbm.commit()
    pintura._cfg_cache['dados'] = None
    pintura.migrar_config()
    checar('migra 3,5 (padrao antigo) para 1,4',
           pintura.get_config(True)['linhas'][0]['velocidade_nominal_m_min'] == 1.4)
    antiga['linhas'][0]['velocidade_nominal_m_min'] = 2.0
    linha_cfg = dbm.query(app.Config).filter_by(chave='monovia').first()
    linha_cfg.valor = _json.dumps(antiga)
    dbm.commit()
    pintura._cfg_cache['dados'] = None
    pintura.migrar_config()
    checar('nao sobrescreve velocidade escolhida pelo usuario',
           pintura.get_config(True)['linhas'][0]['velocidade_nominal_m_min'] == 2.0)
    # devolve a configuracao completa (a de teste acima tinha so uma estacao)
    dbm.query(app.Config).filter_by(chave='monovia').delete()
    dbm.commit()
    pintura._cfg_cache['dados'] = None
    checar('config volta ao padrao de fabrica apos limpar',
           pintura.get_config(True)['linhas'][0]['velocidade_nominal_m_min'] == 1.4)

    print('\n19) IDENTIDADE DO APP E ETAPA ATIVA')
    admN = app.app.test_client()
    admN.post('/login', data={'usuario': 'admin', 'senha': 'admin123'}, follow_redirects=True)

    def _etapas(rota):
        h = admN.get(rota).get_data(as_text=True)
        m = re.search(r'class="brand-sub">(.*?)</span>\s*</div>', h, re.S)
        if not m:
            return None, h
        ativa = re.search(r'<span class="etapa etapa-on">([^<]+)</span>', m.group(1))
        todas = re.findall(r'<span class="etapa(?: etapa-on)?">([^<]+)</span>', m.group(1))
        return (ativa.group(1).strip() if ativa else None,
                [x.strip() for x in todas])

    ativa, todas = _etapas('/preparacao')
    checar('as tres etapas no cabecalho',
           todas == ['PREPARAÇÃO BANHO', 'BANHO', 'PINTURA'], todas)
    checar('pre-banho acende PREPARAÇÃO BANHO', ativa == 'PREPARAÇÃO BANHO', ativa)
    checar('banho acende BANHO', _etapas('/banho')[0] == 'BANHO')
    checar('pintura acende PINTURA', _etapas('/pintura')[0] == 'PINTURA')
    checar('monovia acende PINTURA', _etapas('/monovia')[0] == 'PINTURA')
    checar('telas de admin nao acendem etapa', _etapas('/admin/config')[0] is None)
    checar('nome do app no cabecalho',
           'Controle de Produção' in admN.get('/banho').get_data(as_text=True))
    checar('titulo da aba com o nome novo',
           'Controle de Produção' in re.search(
               r'<title>([^<]+)</title>', admN.get('/pintura').get_data(as_text=True)).group(1))
    hl = app.app.test_client().get('/login').get_data(as_text=True)
    checar('login com o nome novo', 'Controle de Produção' in hl)
    checar('login lista as tres etapas',
           'PREPARAÇÃO BANHO' in hl and 'PINTURA' in hl)
    hp = ger.get('/painel').get_data(as_text=True)
    checar('painel da gerencia com as tres etapas',
           hp.count('data-etapa=') == 3 and 'Controle de Produção' in hp)
    checar('nome antigo fora das telas',
           'Controle de Produtividade' not in admN.get('/banho').get_data(as_text=True))

    print('\n20) RASTREIO NA MONOVIA E KPIs')
    est = cli.get('/api/monovia/estado').get_json()
    checar('estações respondem', len(est['estacoes']) == 4)
    d = cli.get('/api/pintura/dados').get_json()
    checar('KPIs consolidados', d['total'] >= 6, f"{d['total']} atividades")
    checar('tipos separados', len(d['por_tipo']) >= 5, list(d['por_tipo']))
    checar('export Excel', cli.get('/api/download/pintura').status_code in (200, 302))

    # ─────────────────────────────────────────────────────────────────────
    # A partir daqui: regressões da validação completa (cesto duplicado,
    # botões da pintura, painel da gerência, detalhes no pré-banho).
    # ─────────────────────────────────────────────────────────────────────
    L_, A_ = pintura.MODELOS['lote'], pintura.MODELOS['atividade']

    def livres(n=1):
        g = cli.get('/api/cestos').get_json()
        return [x['numero'] for x in g if not x['ocupado']][-n:]

    def fila_de(numero):
        return [l for l in cli.get('/api/pintura/painel').get_json()['cestos']
                if l['numero_cesto'] == numero]

    def cesto_ate_pintura(numero, qtd, ordem):
        cid_ = cesto_no_banho(numero, qtd, ordem)
        cli.post('/api/banho/finalizar', json={'id': cid_})
        return cid_, fila_de(numero)[0]['id']

    print('\n21) CESTO DUPLICADO: AS PORTAS FORAM FECHADAS')
    n1, n2, n3, n4 = livres(4)
    cidA = cesto_no_banho(n1, 50, '910001')
    r1 = cli.post('/api/banho/finalizar', json={'id': cidA}).get_json()
    r2 = cli.post('/api/banho/finalizar', json={'id': cidA})
    checar('2o "sair do banho" do mesmo cesto e recusado',
           r1.get('sucesso') and r2.status_code == 404)
    checar('o cesto vira UM lote so', len(fila_de(n1)) == 1, len(fila_de(n1)))
    loteA = fila_de(n1)[0]['id']
    dbx = Session()
    try:
        dbx.add(L_(card_id=cidA, numero_cesto=n1, origem='CESTO', estado='AGUARDANDO',
                   com_cesto=1, itens_json='[]', quantidade=1))
        dbx.commit()
        barrou = False
    except Exception:
        dbx.rollback()
        barrou = True
    checar('o banco recusa um 2o lote vivo para o mesmo cesto (indice unico)', barrou)

    a1 = cli.post('/api/pintura/atividade/iniciar',
                  json={'lote_id': loteA, 'tipo': 'MONOVIA'}).get_json()
    a2 = cli.post('/api/pintura/atividade/iniciar', json={'lote_id': loteA, 'tipo': 'ESTACIONARIA'})
    checar('2o "iniciar" no mesmo lote e recusado', a1.get('sucesso') and a2.status_code == 400)
    r = cli.post('/api/pintura/retrabalho', json={'lote_id': loteA, 'todas': True,
                                                  'reposicionamento': True})
    checar('retrabalho de lote em carga e recusado', r.status_code == 400, r.get_json())
    r = cli.post('/api/pintura/atividade/pausar', json={'id': a1['atividade']['id'], 'motivo': 'Café'}).get_json()
    checar('pausa devolve a atividade atualizada (o card nao precisa recarregar o lote)',
           r.get('sucesso') and r['atividade']['pausado'] is True and r['atividade']['pausa_motivo'] == 'Café')
    f1 = cli.post('/api/pintura/atividade/finalizar', json={'id': a1['atividade']['id']}).get_json()
    f2 = cli.post('/api/pintura/atividade/finalizar', json={'id': a1['atividade']['id']})
    checar('2o "finalizar" da mesma atividade e recusado', f1.get('sucesso') and f2.status_code in (404, 409))
    antes_hoje = cli.get('/api/pintura/ao_vivo').get_json()['hoje']['cargas']
    r = cli.post('/api/pintura/atividade/iniciar', json={'lote_id': loteA, 'tipo': 'MONOVIA'})
    checar('lote ja pintado NAO pode ser carregado de novo (tela velha em outro tablet)',
           r.status_code == 400, r.get_json())
    r = cli.post('/api/pintura/retrabalho', json={'lote_id': loteA, 'todas': True,
                                                  'reposicionamento': True})
    checar('lote ja pintado NAO gera cesto de retrabalho', r.status_code == 400)
    checar('"pintado hoje" nao conta em dobro',
           cli.get('/api/pintura/ao_vivo').get_json()['hoje']['cargas'] == antes_hoje)

    # cesto cancelado / excluido / alterado por fora do fluxo
    cidB, loteB = cesto_ate_pintura(n2, 60, '910002')
    prep_cli = app.app.test_client()
    prep_cli.post('/login', data={'usuario': 'prep1', 'senha': '123'}, follow_redirects=True)
    r = prep_cli.post('/api/cesto/cancelar', json={'id': cidB})
    checar('pre-banho nao cancela cesto que ja esta na pintura', r.status_code == 400)
    checar('e o lote continua na fila', len(fila_de(n2)) == 1)
    r = adm3.post('/api/admin/card/excluir', json={'id': cidB}).get_json()
    checar('admin exclui o cesto', r.get('sucesso'))
    checar('o lote do cesto excluido SAI da fila da pintura (antes ficava fantasma)',
           len(fila_de(n2)) == 0, len(fila_de(n2)))
    cidB2, loteB2 = cesto_ate_pintura(n2, 61, '910003')
    checar('o mesmo numero, preparado de novo, aparece UMA vez na fila',
           len(fila_de(n2)) == 1 and fila_de(n2)[0]['quantidade'] == 61, len(fila_de(n2)))
    r = adm3.post('/api/admin/card/salvar', json={'id': cidB2, 'estado': 'CONCLUIDO'}).get_json()
    checar('admin conclui o cesto na mao', r.get('sucesso'), r)
    checar('e o lote dele sai da fila', len(fila_de(n2)) == 0)
    lb2 = Session().query(L_).get(loteB2)
    checar('lote fica como CANCELADO (nada e apagado)', lb2.estado == 'FINALIZADO' and lb2.destino == 'CANCELADO')
    checar('lote cancelado nao entra no resumo do cesto', cidB2 not in pintura.resumo_por_card([cidB2]))

    # numero trocado
    cidC, loteC = cesto_ate_pintura(n3, 70, '910004')
    r = prep_cli.post('/api/cesto/mudar_numero', json={'id': cidC, 'numero_cesto': n2}).get_json()
    checar('troca o numero do cesto', r.get('sucesso'), r)
    checar('a fila da pintura mostra o numero novo',
           len(fila_de(n2)) == 1 and len(fila_de(n3)) == 0)

    # reconciliacao: estados desencontrados gravados direto no banco
    dbx = Session()
    cC = dbx.query(Card).get(cidC)
    cC.estado = app.ST_EM_PINTURA            # cesto "em pintura" com lote parado na fila
    lC = dbx.query(L_).get(loteC)
    dbx.commit()
    rel = pintura.reconciliar_lotes()
    checar('reconciliacao acerta cesto x lote', rel['ajustados'] >= 1
           and Session().query(Card).get(cidC).estado == app.ST_FILA_PINTURA, rel)
    dbx = Session()
    lC = dbx.query(L_).get(loteC)
    lC.estado = 'EM_ATIVIDADE'               # lote "em atividade" sem atividade aberta
    dbx.commit()
    pintura.reconciliar_lotes()
    checar('lote em atividade sem atividade volta para a fila',
           Session().query(L_).get(loteC).estado == 'AGUARDANDO' and len(fila_de(n2)) == 1)
    checar('reconciliacao e idempotente', not any(pintura.reconciliar_lotes().values()))

    # cadastro de retrabalho nao pode puxar cesto adiantado de volta para a fila
    r = cli.post('/api/pintura/retrabalho', json={'lote_id': loteC, 'todas': True,
                                                  'reposicionamento': False}).get_json()
    checar('retrabalho sem reposicionamento', r.get('sucesso'), r.get('erro'))
    cidR = r['card_id']
    itR = [{'ordem': '910004', 'material': '300123', 'texto_breve': 'X', 'quantidade': 70}]
    r1 = cli.post('/api/pintura/retrabalho/cadastrar', json={'id': cidR, 'itens': itR}).get_json()
    r2 = cli.post('/api/pintura/retrabalho/cadastrar', json={'id': cidR, 'itens': itR})
    checar('2o cadastro do mesmo retrabalho e recusado', r1.get('sucesso') and r2.status_code == 400)
    # lote sem cesto exige o numero do cesto do retrabalho, dentro da faixa
    r = cli.post('/api/pintura/op_nova', json={'destino': 'monovia', 'itens': itR}).get_json()
    cli.post('/api/pintura/atividade/finalizar', json={'id': r['atividade']['id']})
    cidD, loteD = cesto_ate_pintura(n4, 30, '910005')
    a = cli.post('/api/pintura/atividade/iniciar',
                 json={'lote_id': loteD, 'tipo': 'ARMAZENAMENTO', 'subtipo': 'Palete'}).get_json()
    cli.post('/api/pintura/atividade/finalizar', json={'id': a['atividade']['id']})
    r = cli.post('/api/pintura/retrabalho', json={'lote_id': loteD, 'todas': True, 'reposicionamento': True})
    checar('retrabalho de pecas fora do cesto exige o numero do cesto', r.status_code == 400, r.get_json())
    r = cli.post('/api/pintura/retrabalho', json={'lote_id': loteD, 'todas': True,
                                                  'reposicionamento': True, 'numero_cesto': 9999})
    checar('numero de cesto fora da faixa e recusado', r.status_code == 400, r.get_json())
    checar('e o lote continua na fila depois das recusas',
           Session().query(L_).get(loteD).estado == 'AGUARDANDO')

    # preparacao -> monovia: cesto volta a "aguardando pintura" enquanto ninguem carrega
    n5 = [x for x in livres(8) if x not in (n1, n2, n3, n4)][-1]
    cidE, loteE = cesto_ate_pintura(n5, 20, '910006')
    a = cli.post('/api/pintura/atividade/iniciar',
                 json={'lote_id': loteE, 'tipo': 'PREPARACAO', 'subtipo': 'Lixa'}).get_json()
    checar('cesto em preparacao fica EM_PINTURA', Session().query(Card).get(cidE).estado == app.ST_EM_PINTURA)
    cli.post('/api/pintura/atividade/finalizar', json={'id': a['atividade']['id'], 'destino': 'MONOVIA'})
    checar('fim da preparacao: cesto volta a FILA_PINTURA',
           Session().query(Card).get(cidE).estado == app.ST_FILA_PINTURA)

    print('\n22) PAINEL DA GERENCIA: CADA CESTO UMA VEZ')
    vivo = ger.get('/api/painel/ativos').get_json()
    ids_lote = {l['card_id'] for l in vivo['pintura']['fila'] + vivo['pintura']['carregando']
                if l.get('card_id') and l['com_cesto']}
    rep = [c['numero_cesto'] for c in vivo['ativos']
           if c['estado'] in ('FILA_PINTURA', 'EM_PINTURA', 'CARREGANDO') and c['id'] in ids_lote]
    checar('cesto que tem lote na pintura nao vem repetido em "ativos"', not rep, rep)
    checar('lotes ao vivo trazem o cesto de origem (card_id)',
           all('card_id' in l for l in vivo['pintura']['fila']))
    nums = [c['numero_cesto'] for c in vivo['ativos']] + \
           [l['numero_cesto'] for l in vivo['pintura']['fila'] + vivo['pintura']['carregando'] if l['com_cesto']]
    checar('nenhum numero de cesto repetido no painel ao vivo', len(nums) == len(set(nums)), sorted(nums))
    dd = ger.get('/api/painel/dados').get_json()
    checar('"em andamento" = cestos + lotes, sem contar em dobro',
           dd['em_andamento'] == len(dd['ativos']) + len(dd['ativos_pintura']),
           (dd['em_andamento'], len(dd['ativos']), len(dd['ativos_pintura'])))
    for bloco in ('prep', 'banho', 'pintura', 'geral'):
        b = dd[bloco]
        checar(f'bloco {bloco} tem dados para os graficos',
               isinstance(b.get('por_processo'), dict) and isinstance(b.get('por_dia'), dict)
               and isinstance(b.get('area_por_dia'), dict))
    for bloco in ('prep', 'banho', 'geral'):
        r0 = dd[bloco]['registros'][0]
        checar(f'tabela {bloco}: cada linha traz estado atual e ciclo',
               all(k in r0 for k in ('etapa', 'em_curso', 'ciclo_ini', 'ciclo_fim', 'ciclo_total')))
        crus = [r_['etapa'] for r_ in dd[bloco]['registros'] if '_' in (r_['etapa'] or '')]
        checar(f'tabela {bloco}: nenhum estado cru (FILA_PINTURA...)', not crus, crus[:3])
    rp = dd['pintura']['registros'][0]
    checar('tabela da pintura: lancamento com rotulo, lote e tempo',
           rp.get('tipo_lbl') and rp.get('lote') and 'minutos' in rp and rp.get('lote_id'))
    checar('pintura informa OPs carregadas e se a descarga esta ligada',
           dd['pintura']['ops'] > 0 and dd['pintura']['descarga_ativa'] is False)
    hp = ger.get('/painel').get_data(as_text=True)
    checar('painel: tabela unica com cabecalho por setor', 'CAB_SETOR' in hp and 'id="thIni"' in hp)
    checar('painel: historico nao e mais sobrescrito pela tabela antiga',
           'renderHistorico(d)' not in hp)
    checar('painel: periodo selecionado tem onde aparecer', 'id="periodoTag"' in hp)
    checar('painel: categorias de retrabalho e preparacao',
           "setCategoria('AGUARDANDO_MONTAGEM')" in hp and "setCategoria('PREP_PINT')" in hp)
    js = adm3.get('/static/dash.js').get_data(as_text=True)
    checar('graficos toleram bloco sem dados', "d.por_processo||{}" in js and "d.por_dia||{}" in js)

    print('\n23) PRE-BANHO: VER DETALHES EM CADA CESTO')
    hpre = prep_cli.get('/preparacao').get_data(as_text=True)
    checar('cada cesto da grade tem o olho de detalhes dentro do quadrado',
           'class="cesto-olho"' in hpre and 'verDetalhesCesto(' in hpre)
    checar('botao "Detalhes" de baixo e selo de operadores sairam da grade',
           'cesto-det' not in hpre and 'cesto-nop' not in hpre)
    d1 = prep_cli.get(f'/api/cesto/{n5}/detalhes').get_json()
    checar('detalhes de cesto ocupado', d1['ocupado'] and d1['card']['numero_cesto'] == n5
           and d1['card']['etapa'] == 'Aguardando pintura')
    checar('detalhes mostram a situacao na pintura',
           d1['card']['pintura_agora']['estado'] == 'AGUARDANDO'
           and any(h['tipo'] == 'PREPARACAO' for h in d1['card']['pintura_agora']['historico']))
    d2 = prep_cli.get(f'/api/cesto/{n1}/detalhes').get_json()
    checar('cesto livre mostra o ultimo uso', d2['ocupado'] is False and d2['card'] is None
           and d2['ultimo'] and d2['ultimo']['pintura_agora']['rotulo'] == 'Carregado na monovia')
    d3 = prep_cli.get(f'/api/cesto/{n4}/detalhes').get_json()
    checar('cesto liberado com pecas no palete: ultimo uso diz onde estao',
           d3['ocupado'] is False and d3['ultimo']['etapa'] == 'Aguardando pintura (fora do cesto)',
           d3['ultimo'] and d3['ultimo']['etapa'])
    checar('detalhes exigem login', app.app.test_client().get('/api/cesto/1/detalhes').status_code in (302, 401, 403))

    print('\n24) TELA DE PINTURA: BOTOES')
    hpi = cli.get('/pintura').get_data(as_text=True)
    checar('abas usam as colunas que existem na tela',
           "const COLS={fila:['lbF','colFila']" in hpi and 'colAtiv' not in hpi and "'lbA'" not in hpi)
    checar('OP nova abandonada e descartada ao fechar/abrir outro lote',
           hpi.count('limparOpNova()') >= 5)
    checar('toque repetido e ignorado enquanto grava', "window[n]=async function" in hpi)
    checar('parada pelo card nao abre o modal grande',
           "modal.classList.add('open');abrirCron();pedirPausa()" not in hpi)
    checar('OP nova na estacionaria oferece trocar para monovia', "(a.subtipo||'')==='estacionaria'" in hpi)
    checar('bloco morto removido', '_antigo' not in hpi)
    checar('arquivos estaticos com versao (tablet nao fica com JS velho)',
           re.search(r'/static/wakelock\.js\?v=\d+', hpi) is not None)

    print('\n25) BACKUP E CONFIGURACAO')
    bk = adm3.get('/api/admin/backup').get_json()
    checar('backup inclui lotes e atividades da pintura',
           len(bk.get('pintura_lotes') or []) > 0 and len(bk.get('pintura_atividades') or []) > 0)
    n_antes = Session().query(L_).count()
    dbx = Session()
    alvo = dbx.query(L_).filter(L_.estado == 'FINALIZADO').first()
    alvo_id = alvo.id
    dbx.query(A_).filter(A_.lote_id == alvo_id).delete(synchronize_session=False)
    dbx.delete(alvo)
    dbx.commit()
    import io as _io, json as _json
    r = adm3.post('/api/admin/restaurar', data={'arquivo': (_io.BytesIO(_json.dumps(bk).encode()), 'bk.json')},
                  content_type='multipart/form-data').get_json()
    checar('restaurar traz de volta o lote apagado', r.get('sucesso') and r.get('lotes_pintura') == 1
           and Session().query(L_).count() == n_antes, r)
    # config lida por conexao propria: nao pode derrubar o que o pedido ainda nao gravou
    dbx = Session()
    c9 = Card(estado=app.ST_PREENCHER, numero_cesto=livres(1)[0], observacao='T:teste')
    dbx.add(c9)
    app._agenda_cache['dados'] = None
    app.get_agenda()
    pintura._cfg_cache['dados'] = None
    pintura.get_config()
    app._cestos_cache['n'] = None
    app.total_cestos()
    checar('ler configuracao no meio do pedido nao descarta o que esta pendente', c9 in dbx.new)
    dbx.rollback()
    cache = app._CacheCurto(x=None)
    cache['x'] = 5
    cache._t -= 11
    checar('cache de configuracao vence em 10 s (o outro processo enxerga a mudanca)',
           cache['x'] is None and cache.velho('x') == 5)

    print('\n26) VELOCIDADE DA MONOVIA: PRODUCAO ALTERA, CADA CARGA GUARDA A SUA')
    from datetime import timedelta as _td
    V_ = pintura.MODELOS['vel_log']
    vel0 = pintura.velocidade_atual()
    n_log0 = Session().query(V_).count()
    v = cli.get('/api/pintura/velocidade').get_json()
    checar('producao consulta a velocidade configurada', v['velocidade'] == vel0 and 'alteracoes' in v)
    r = cli.post('/api/pintura/velocidade', json={'velocidade': 2.2})
    checar('alterar sem o nome de quem alterou e recusado', r.status_code == 400 and 'nome' in r.get_json()['erro'])
    r = cli.post('/api/pintura/velocidade', json={'velocidade': 99, 'nome': 'T: Carlos'})
    checar('velocidade fora da faixa e recusada', r.status_code == 400)
    r = cli.post('/api/pintura/velocidade', json={'velocidade': 'abc', 'nome': 'T: Carlos'})
    checar('velocidade que nao e numero e recusada', r.status_code == 400)
    checar('recusas nao criam registro', Session().query(V_).count() == n_log0)

    # carga A comeca na velocidade antiga
    nA, nB, nC = [x for x in livres(8)][-3:]
    cidVA, loteVA = cesto_ate_pintura(nA, 40, '920001')
    aA = cli.post('/api/pintura/atividade/iniciar', json={'lote_id': loteVA, 'tipo': 'MONOVIA'}).get_json()['atividade']
    checar('carga na monovia grava a velocidade do inicio', aA['vel_m_min'] == vel0, aA['vel_m_min'])
    nova = round(vel0 + 0.8, 2)
    r = cli.post('/api/pintura/velocidade', json={'velocidade': str(nova).replace('.', ','), 'nome': 'T: Carlos'}).get_json()
    checar('producao altera a velocidade (aceita virgula)', r.get('sucesso') and r['velocidade'] == nova, r)
    reg = Session().query(V_).order_by(V_.id.desc()).first()
    checar('registro: quem, de quanto, para quanto, onde',
           reg.nome == 'T: Carlos' and reg.de == vel0 and reg.para == nova
           and reg.origem == 'Pintura' and reg.data_hora is not None, reg.to_dict())
    checar('tela da pintura recebe a velocidade nova e quem alterou',
           cli.get('/api/pintura/painel').get_json()['velocidade_nominal'] == nova
           and cli.get('/api/pintura/painel').get_json()['velocidade_alteracao']['nome'] == 'T: Carlos')
    r = cli.post('/api/pintura/velocidade', json={'velocidade': nova, 'nome': 'T: Carlos'})
    checar('repetir a mesma velocidade nao cria registro', r.status_code == 400
           and Session().query(V_).count() == n_log0 + 1)
    # carga B comeca depois da troca
    cidVB, loteVB = cesto_ate_pintura(nB, 40, '920002')
    aB = cli.post('/api/pintura/atividade/iniciar', json={'lote_id': loteVB, 'tipo': 'MONOVIA'}).get_json()['atividade']
    checar('carga iniciada depois da troca usa a velocidade nova', aB['vel_m_min'] == nova, aB['vel_m_min'])
    checar('carga que ja estava em andamento continua com a antiga',
           Session().query(A_).get(aA['id']).vel_m_min == vel0)
    # 10 minutos de carga em cada uma
    dbx = Session()
    for i_ in (aA['id'], aB['id']):
        dbx.query(A_).get(i_).inicio = datetime.utcnow() - _td(minutes=10)
    dbx.commit()
    cli.post('/api/pintura/atividade/finalizar', json={'id': aA['id']})
    cli.post('/api/pintura/atividade/finalizar', json={'id': aB['id']})

    def _hist(lote_id, ativ_id):
        return next(h for h in cli.get(f'/api/pintura/lote/{lote_id}').get_json()['historico']
                    if h['id'] == ativ_id)
    fA, fB = _hist(loteVA, aA['id']), _hist(loteVB, aB['id'])
    checar('metros da carga antiga = tempo x velocidade antiga',
           abs(fA['metros'] - fA['minutos'] * vel0) < 0.05 and fA['minutos'] > 0, (fA['metros'], fA['minutos']))
    checar('metros da carga nova = tempo x velocidade nova',
           abs(fB['metros'] - fB['minutos'] * nova) < 0.05, (fB['metros'], fB['minutos']))
    checar('resumo do cesto (planilha geral) leva a velocidade da carga',
           pintura.resumo_por_card([cidVA])[cidVA]['velocidade'] == vel0
           and pintura.resumo_por_card([cidVB])[cidVB]['velocidade'] == nova)
    # estacionaria nao tem velocidade; trocar para monovia usa a do INICIO da carga
    cidVC, loteVC = cesto_ate_pintura(nC, 40, '920003')
    aC = cli.post('/api/pintura/atividade/iniciar', json={'lote_id': loteVC, 'tipo': 'ESTACIONARIA'}).get_json()['atividade']
    checar('carga na estacionaria nao tem velocidade', aC['vel_m_min'] is None)
    dbx = Session()
    dbx.query(A_).get(aC['id']).inicio = reg.data_hora - _td(minutes=5)    # comecou ANTES da troca
    dbx.commit()
    r = cli.post('/api/pintura/atividade/destino', json={'id': aC['id'], 'tipo': 'MONOVIA'}).get_json()
    checar('estacionaria -> monovia no meio: vale a velocidade de quando a carga comecou',
           r['atividade']['vel_m_min'] == vel0, r['atividade']['vel_m_min'])
    r = cli.post('/api/pintura/atividade/destino', json={'id': aC['id'], 'tipo': 'ESTACIONARIA'}).get_json()
    checar('monovia -> estacionaria: fica sem velocidade', r['atividade']['vel_m_min'] is None)
    cli.post('/api/pintura/atividade/finalizar', json={'id': aC['id']})
    # cargas antigas sem a velocidade gravada: a migracao congela pelo historico
    dbx = Session()
    dbx.query(A_).get(aA['id']).vel_m_min = None
    dbx.commit()
    pintura.congelar_velocidades()
    checar('carga antiga sem registro recebe a velocidade que valia no inicio dela',
           Session().query(A_).get(aA['id']).vel_m_min == vel0, Session().query(A_).get(aA['id']).vel_m_min)

    # administrador: configuracoes e planilhas
    r = adm4.post('/admin/config', data=dict(base, vel_monovia=str(nova)), follow_redirects=True)
    checar('salvar configuracoes sem mudar a velocidade nao cria registro',
           Session().query(V_).count() == n_log0 + 1)
    adm4.post('/admin/config', data=dict(base, vel_monovia=str(vel0)), follow_redirects=True)
    reg2 = Session().query(V_).order_by(V_.id.desc()).first()
    checar('alteracao pelo administrador tambem fica registrada',
           reg2.origem == 'Administrador' and reg2.de == nova and reg2.para == vel0, reg2.to_dict())
    dbx = Session()
    reg2 = dbx.query(V_).get(reg2.id)
    reg2.nome = 'T: Administrador'                  # para a limpeza do teste
    dbx.commit()
    from openpyxl import load_workbook
    r = adm4.get('/api/download/velocidade')
    checar('administrador baixa o registro de velocidade',
           r.status_code == 200 and 'spreadsheet' in r.headers['Content-Type']
           and 'velocidade_monovia_' in r.headers['Content-Disposition'])
    wbv = load_workbook(io.BytesIO(r.data))
    checar('planilha tem as duas abas', wbv.sheetnames == ['Alterações de velocidade', 'Velocidade por cesto'],
           wbv.sheetnames)
    alt = list(wbv['Alterações de velocidade'].iter_rows(values_only=True))
    checar('aba de alteracoes: data, hora, quem, de, para',
           alt[0][:3] == ('Data', 'Hora', 'Quem alterou') and alt[0][5:7] == ('De (m/min)', 'Para (m/min)')
           and any(l[2] == 'T: Carlos' and l[5] == vel0 and l[6] == nova and l[4] == 'Pintura'
                   and len(str(l[0])) == 10 and len(str(l[1])) == 8 for l in alt[1:]), alt[:3])
    pc = list(wbv['Velocidade por cesto'].iter_rows(values_only=True))
    iv = pc[0].index('Velocidade (m/min)')
    porcesto = {l[1]: l[iv] for l in pc[1:] if l and l[1] in (nA, nB)}
    checar('aba por cesto: cada cesto com a velocidade em que foi carregado',
           porcesto.get(nA) == vel0 and porcesto.get(nB) == nova, porcesto)
    wbg = load_workbook(io.BytesIO(adm4.get('/api/download/geral').data))
    cab = [c.value for c in wbg['Geral'][1]]
    checar('planilha geral: coluna de velocidade e as abas do registro',
           'Velocidade monovia (m/min)' in cab and 'Alterações de velocidade' in wbg.sheetnames
           and 'Velocidade por cesto' in wbg.sheetnames, wbg.sheetnames)
    ic, iv2 = cab.index('Cesto'), cab.index('Velocidade monovia (m/min)')
    lg = {l[ic]: l[iv2] for l in wbg['Geral'].iter_rows(min_row=2, values_only=True) if l[ic] in (nA, nB)}
    checar('planilha geral: velocidade certa em cada cesto', lg.get(nA) == vel0 and lg.get(nB) == nova, lg)
    checar('planilha geral: cabecalho e linhas com o mesmo numero de colunas',
           all(len([x for x in l]) == len(cab) for l in wbg['Geral'].iter_rows(min_row=2, max_row=3, values_only=True)))
    wbp = load_workbook(io.BytesIO(adm4.get('/api/download/pintura').data))
    cabp = [c.value for c in wbp['Pintura'][1]]
    checar('planilha da pintura: coluna de velocidade', 'Velocidade monovia (m/min)' in cabp
           and cabp.index('Velocidade monovia (m/min)') + 1 == cabp.index('Metros monovia'))
    for nome_, c_ in (('gerencia', ger), ('operador de pintura', cli)):
        r = c_.get('/api/download/velocidade')
        checar(f'{nome_} nao baixa o registro de velocidade',
               r.status_code in (302, 401, 403) or 'spreadsheet' not in r.headers.get('Content-Type', ''))
    # nada disso no painel da gerencia
    hp = ger.get('/painel').get_data(as_text=True).lower()
    checar('painel da gerencia nao tem nada de velocidade', 'velocidade' not in hp and 'm/min' not in hp)
    vivo = ger.get('/api/painel/ativos').get_json()['pintura']
    checar('nem os dados enviados ao painel da gerencia',
           'velocidade_nominal' not in vivo and 'velocidade_alteracao' not in vivo)
    # telas
    hpi = cli.get('/pintura').get_data(as_text=True)
    checar('pintura: campo "sem leitura" saiu; velocidade configurada com botao de alterar',
           'sem leitura' not in hpi and 'id="mvVel"' not in hpi and 'abrirVelocidade()' in hpi)
    checar('pintura: cestos em quadrados (fila e atividades), janela ao tocar',
           'function tileLote' in hpi and 'function tileAtividade' in hpi
           and 'function cardLote' not in hpi and hpi.count('class="cestos-grid pint"') == 5
           and 'onclick="abrirLote(${l.id})"' in hpi and 'onclick="abrirAtividade(${l.id})"' in hpi)
    hmo = adm4.get('/monovia').get_data(as_text=True)
    checar('administrador: registro de velocidade na pagina da monovia e no dashboard',
           '/api/download/velocidade' in hmo
           and "baixar('velocidade')" in adm4.get('/dashboard').get_data(as_text=True))
    bk = adm4.get('/api/admin/backup').get_json()
    checar('backup inclui o registro de velocidade', len(bk.get('monovia_velocidade_log') or []) >= 2)
    checar('velocidade volta ao valor inicial ao fim do teste', pintura.velocidade_atual() == vel0)

    print('\n' + '=' * 62)
    print(f'  {len(OK)} verificações OK · {len(FALHA)} falhas')
    if FALHA:
        for f in FALHA:
            print('   ! ' + f)
    print('=' * 62 + '\n')
    limpar()


if __name__ == '__main__':
    main()
