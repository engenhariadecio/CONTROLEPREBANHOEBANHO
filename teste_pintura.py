"""Teste ponta a ponta do módulo de pintura (via HTTP, como o navegador faz).

    python teste_pintura.py

Cobre os seis lançamentos: monovia, estacionária, OP nova, preparação para
pintura (com destino armazenamento), armazenamento em palete/arara e retrabalho
(total com reposicionamento e parcial sem reposicionamento).
"""
import warnings
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

    print('\n' + '=' * 62)
    print(f'  {len(OK)} verificações OK · {len(FALHA)} falhas')
    if FALHA:
        for f in FALHA:
            print('   ! ' + f)
    print('=' * 62 + '\n')
    limpar()


if __name__ == '__main__':
    main()
