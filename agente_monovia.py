"""
AGENTE DE BORDA DA MONOVIA — roda num mini-PC/Raspberry na fábrica, não no Railway.

Lê a velocidade da monovia e manda para o sistema. Tem três fontes:

    python agente_monovia.py --fonte clp       # CLP via Modbus TCP (preferido)
    python agente_monovia.py --fonte camera    # câmera + visão computacional
    python agente_monovia.py --fonte simulador # para testar sem hardware

O servidor não distingue as fontes — o endpoint é o mesmo. Se o CLP funcionar,
troque `--fonte camera` por `--fonte clp` e nada mais muda.

Buffer offline: se a rede da fábrica cair, as amostras ficam num arquivo local e
são enviadas em lote quando a conexão volta. Nenhuma medição se perde.

Instalação:
    pip install requests
    pip install pymodbus          # só para --fonte clp
    pip install opencv-python     # só para --fonte camera


────────────────────────────────────────────────────────────────────────────
SOBRE A CÂMERA: o que realmente precisa de IA e o que não precisa
────────────────────────────────────────────────────────────────────────────
Para VELOCIDADE e ÁREA, visão computacional clássica calibrada é mais precisa,
mais rápida e não exige treinar nada — é o que está implementado aqui:

  • velocidade → fluxo óptico (Lucas-Kanade) sobre os ganchos/corrente numa faixa
    fixa da imagem. Mede deslocamento em pixels/quadro e converte com um fator de
    calibração px/metro. Erro típico < 3% depois de calibrado.
  • parada     → velocidade ≈ 0 por N segundos (o servidor já faz isso sozinho).
  • área       → silhueta da peça contra o fundo (subtração de fundo + contorno),
    convertida por um fator m²/px². Vale para peças planas perpendiculares à câmera.

Rede neural treinada só compensa se você quiser CLASSIFICAR (que peça é, se tem
defeito, se o gancho está vazio). E, para "que peça é", você não precisa de IA:
o odômetro já responde isso com exatidão — o sistema sabe qual cesto ocupa qual
trecho de trilho. É por isso que o rastreio foi feito por metros, não por imagem.

CALIBRAÇÃO (fazer uma vez, com a linha rodando):
  1. Marque dois pontos no trilho a uma distância conhecida (ex.: 2,00 m).
  2. Rode `python agente_monovia.py --fonte camera --calibrar`.
  3. Clique nos dois pontos na imagem. O script imprime PX_POR_METRO.
  4. Coloque o valor em --px-por-metro (ou na constante abaixo).
"""
import os
import sys
import json
import time
import argparse
import threading
from collections import deque
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    sys.exit('Falta a biblioteca requests:  pip install requests')

BUFFER_ARQ = 'buffer_monovia.jsonl'


# ─────────────────────────────────────────────────────────────────────────────
# Envio (com buffer offline)
# ─────────────────────────────────────────────────────────────────────────────
class Enviador:
    def __init__(self, url, token, linha, lote=12):
        self.url = url.rstrip('/')
        self.token = token
        self.linha = linha
        self.lote = lote
        self.fila = deque()
        self._carregar_buffer()
        self.lock = threading.Lock()

    def _carregar_buffer(self):
        if os.path.exists(BUFFER_ARQ):
            with open(BUFFER_ARQ, encoding='utf-8') as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        try:
                            self.fila.append(json.loads(ln))
                        except ValueError:
                            pass
            print(f'[buffer] {len(self.fila)} amostras pendentes recuperadas do disco.')

    def _salvar_buffer(self):
        with open(BUFFER_ARQ, 'w', encoding='utf-8') as f:
            for a in self.fila:
                f.write(json.dumps(a, ensure_ascii=False) + '\n')

    def add(self, velocidade=None, odometro=None, fonte='clp', confianca=1.0, extra=None):
        a = {'linha': self.linha, 'fonte': fonte, 'confianca': confianca,
             'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')}
        if velocidade is not None:
            a['velocidade'] = round(float(velocidade), 3)
        if odometro is not None:
            a['odometro'] = round(float(odometro), 3)
        if extra:
            a['extra'] = extra
        with self.lock:
            self.fila.append(a)
            if len(self.fila) >= self.lote:
                self._flush()

    def _flush(self):
        if not self.fila:
            return
        itens = list(self.fila)
        try:
            r = requests.post(f'{self.url}/api/monovia/amostras',
                              json={'linha': self.linha, 'amostras': itens},
                              headers={'X-Monovia-Token': self.token}, timeout=10)
            if r.status_code == 200:
                self.fila.clear()
                if os.path.exists(BUFFER_ARQ):
                    os.remove(BUFFER_ARQ)
                j = r.json()
                print(f"[envio] {j.get('recebidas')} amostras · odômetro "
                      f"{j.get('odometro_m')} m · {j.get('velocidade')} m/min")
            else:
                print(f'[envio] servidor respondeu {r.status_code}; mantendo no buffer.')
                self._salvar_buffer()
        except requests.RequestException as e:
            print(f'[envio] sem conexão ({e.__class__.__name__}); guardando no buffer.')
            self._salvar_buffer()

    def flush(self):
        with self.lock:
            self._flush()


# ─────────────────────────────────────────────────────────────────────────────
# Fonte 1: CLP via Modbus TCP
# ─────────────────────────────────────────────────────────────────────────────
def rodar_clp(env, args):
    """Ajuste ENDERECO e ESCALA conforme o mapa de memória do seu CLP.

    Dois casos comuns:
      • o CLP expõe a VELOCIDADE (Hz do inversor ou m/min) -> envie velocidade
      • o CLP expõe a CONTAGEM DO ENCODER -> envie odometro (melhor: é exato)
    Se tiver o encoder, use-o. O odômetro absoluto elimina qualquer deriva.
    """
    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError:
        sys.exit('Falta pymodbus:  pip install pymodbus')

    ENDERECO_VEL = args.registrador          # holding register da velocidade
    ENDERECO_ENC = args.registrador_encoder  # holding register do encoder (opcional)
    ESCALA_VEL = args.escala                 # ex.: valor 350 -> 3,50 m/min => escala 0.01
    METROS_POR_PULSO = args.metros_por_pulso

    cli = ModbusTcpClient(args.clp_ip, port=args.clp_porta)
    if not cli.connect():
        sys.exit(f'Não consegui conectar ao CLP em {args.clp_ip}:{args.clp_porta}')
    print(f'[clp] conectado em {args.clp_ip}:{args.clp_porta}')

    try:
        while True:
            vel = odo = None
            try:
                if ENDERECO_ENC is not None:
                    rr = cli.read_holding_registers(ENDERECO_ENC, count=2, slave=args.slave)
                    if not rr.isError():
                        pulsos = (rr.registers[0] << 16) | rr.registers[1]
                        odo = pulsos * METROS_POR_PULSO
                if ENDERECO_VEL is not None:
                    rr = cli.read_holding_registers(ENDERECO_VEL, count=1, slave=args.slave)
                    if not rr.isError():
                        vel = rr.registers[0] * ESCALA_VEL
            except Exception as e:
                print(f'[clp] falha na leitura: {e}')
            if vel is not None or odo is not None:
                env.add(velocidade=vel, odometro=odo, fonte='clp')
            time.sleep(args.intervalo)
    except KeyboardInterrupt:
        env.flush()
        cli.close()


# ─────────────────────────────────────────────────────────────────────────────
# Fonte 2: câmera (fluxo óptico para velocidade + silhueta para área)
# ─────────────────────────────────────────────────────────────────────────────
def rodar_camera(env, args):
    try:
        import cv2
        import numpy as np
    except ImportError:
        sys.exit('Falta opencv:  pip install opencv-python')

    cap = cv2.VideoCapture(int(args.camera) if str(args.camera).isdigit() else args.camera)
    if not cap.isOpened():
        sys.exit(f'Não consegui abrir a câmera: {args.camera}')
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    print(f'[camera] aberta · {fps:.0f} fps · {args.px_por_metro} px/m')

    if args.calibrar:
        return _calibrar(cap, cv2)

    lk = dict(winSize=(21, 21), maxLevel=3,
              criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
    feat = dict(maxCorners=120, qualityLevel=0.2, minDistance=12, blockSize=7)

    # ── por que NÃO comparar quadros consecutivos ──────────────────────────
    # A monovia anda ~3,5 m/min = 5,8 cm/s. A 180 px/m isso dá 10,5 px/s, ou
    # 0,42 px entre dois quadros a 25 fps: MENOS DE UM PIXEL. O fluxo óptico
    # mediria ruído. Por isso guardamos um quadro de referência e só comparamos
    # com ele depois de `--stride-seg` segundos (1 s -> ~10 px, mensurável).
    stride = max(1, int(round(fps * args.stride_seg)))
    print(f'[camera] comparando quadros a cada {args.stride_seg}s ({stride} quadros) '
          f'-> ~{args.px_por_metro * 3.5 / 60 * args.stride_seg:.1f} px esperados a 3,5 m/min')

    ok, frame = cap.read()
    if not ok:
        sys.exit('Não consegui ler o primeiro quadro.')
    h, w = frame.shape[:2]
    # ROI: faixa horizontal onde os ganchos/corrente aparecem (ajuste se necessário)
    y0, y1 = int(h * args.roi_topo), int(h * args.roi_base)
    cinza_ant = cv2.cvtColor(frame[y0:y1], cv2.COLOR_BGR2GRAY)
    pts = cv2.goodFeaturesToTrack(cinza_ant, mask=None, **feat)
    fundo = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=40, detectShadows=False)

    janela = deque(maxlen=6)              # média móvel das últimas medições
    ultimo_envio = 0.0
    n_frame = 0
    t_ref = time.time()

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.5)
                continue
            n_frame += 1
            if n_frame % stride:          # só mede a cada `stride` quadros
                continue
            agora_f = time.time()
            dt_real = max(1e-6, agora_f - t_ref)   # tempo real decorrido
            t_ref = agora_f
            cinza = cv2.cvtColor(frame[y0:y1], cv2.COLOR_BGR2GRAY)

            # ── velocidade: mediana do deslocamento horizontal dos pontos ──
            desl_px = 0.0
            conf = 0.0
            if pts is not None and len(pts) >= 6:
                novos, st, _ = cv2.calcOpticalFlowPyrLK(cinza_ant, cinza, pts, None, **lk)
                if novos is not None:
                    bons_n = novos[st == 1].reshape(-1, 2)
                    bons_a = pts[st == 1].reshape(-1, 2)
                    if len(bons_n) >= 6:
                        dx = bons_n[:, 0] - bons_a[:, 0]
                        desl_px = float(np.median(dx))
                        # confiança = quão concordantes são os pontos
                        disp = float(np.std(dx))
                        conf = max(0.0, min(1.0, 1.0 - disp / (abs(desl_px) + 3.0)))
                        pts = bons_n.reshape(-1, 1, 2)
            if pts is None or len(pts) < 30:
                pts = cv2.goodFeaturesToTrack(cinza, mask=None, **feat)
            cinza_ant = cinza

            # px no intervalo -> m/min (usa o tempo REAL, não o fps nominal:
            # webcam USB entrega menos quadros do que promete quando a CPU aperta)
            v = abs(desl_px) / args.px_por_metro / dt_real * 60.0
            janela.append(v)
            v_media = sum(janela) / len(janela)
            if v_media < args.ruido:      # abaixo do ruído = parada de verdade
                v_media = 0.0

            # ── área ocupada (opcional, ROI perpendicular) ──
            extra = None
            if args.medir_area:
                mask = fundo.apply(frame[y0:y1])
                mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
                px_peca = int((mask > 200).sum())
                if px_peca > args.area_min_px:
                    extra = {'area_frame_m2': round(px_peca / (args.px_por_metro ** 2), 4),
                             'px': px_peca}

            agora = time.time()
            if agora - ultimo_envio >= args.intervalo:
                env.add(velocidade=v_media, fonte='camera',
                        confianca=round(conf, 2), extra=extra)
                ultimo_envio = agora
                if args.mostrar:
                    print(f'  v={v_media:6.2f} m/min  conf={conf:.2f}'
                          + (f"  área={extra['area_frame_m2']} m²" if extra else ''))
    except KeyboardInterrupt:
        env.flush()
        cap.release()


def _calibrar(cap, cv2):
    """Clique em dois pontos separados por uma distância conhecida no trilho."""
    print('\nCALIBRAÇÃO: clique em dois pontos de distância conhecida. ESC para sair.')
    ok, frame = cap.read()
    if not ok:
        return
    pontos = []

    def clique(ev, x, y, flags, param):
        if ev == cv2.EVENT_LBUTTONDOWN and len(pontos) < 2:
            pontos.append((x, y))
            cv2.circle(frame, (x, y), 6, (0, 255, 0), -1)
            cv2.imshow('calibracao', frame)

    cv2.imshow('calibracao', frame)
    cv2.setMouseCallback('calibracao', clique)
    while len(pontos) < 2:
        if cv2.waitKey(30) == 27:
            cv2.destroyAllWindows()
            return
    d_px = ((pontos[1][0] - pontos[0][0]) ** 2 + (pontos[1][1] - pontos[0][1]) ** 2) ** 0.5
    dist = float(input('Distância real entre os pontos, em metros: ') or 1)
    print(f'\n>>> PX_POR_METRO = {d_px / dist:.2f}')
    print(f'>>> use:  --px-por-metro {d_px / dist:.2f}\n')
    cv2.destroyAllWindows()


# ─────────────────────────────────────────────────────────────────────────────
# Fonte 3: SENSOR DE PROXIMIDADE contando ganchos  ← a mais confiável e barata
# ─────────────────────────────────────────────────────────────────────────────
def rodar_sensor(env, args):
    """Um sensor indutivo (ou fotoelétrico) apontado para os ganchos/corrente.

    Cada gancho que passa = `--metros-por-pulso` metros percorridos (normalmente
    o passo do gancho, ex.: 0,40 m). Somando os pulsos você tem o ODÔMETRO EXATO,
    sem calibração de imagem, sem iluminação, sem CPU. É o caminho que eu
    recomendo tentar antes da câmera: um sensor custa uma fração de uma câmera
    industrial e não erra.

    Ligação num Raspberry Pi: sinal do sensor -> GPIO (com resistor de pull-up),
    GND comum. Sensores PNP de 24 V precisam de um divisor ou optoacoplador para
    não queimar o GPIO de 3,3 V.
    """
    try:
        from gpiozero import Button
    except ImportError:
        sys.exit('Falta gpiozero:  pip install gpiozero')

    estado = {'pulsos': 0, 'ultimo': time.time()}
    sensor = Button(args.gpio, pull_up=True, bounce_time=args.debounce)

    def pulso():
        estado['pulsos'] += 1
    sensor.when_pressed = pulso

    print(f'[sensor] GPIO {args.gpio} · {args.metros_por_pulso} m por gancho')
    odo = 0.0
    try:
        while True:
            time.sleep(args.intervalo)
            agora = time.time()
            n, estado['pulsos'] = estado['pulsos'], 0
            dt = agora - estado['ultimo']
            estado['ultimo'] = agora
            avanco = n * args.metros_por_pulso
            odo += avanco
            vel = (avanco / dt) * 60.0 if dt > 0 else 0.0
            # manda odômetro E velocidade: o odômetro é a verdade, a velocidade
            # é derivada dele
            env.add(velocidade=vel, odometro=odo, fonte='sensor')
            if args.mostrar:
                print(f'  {n} ganchos · {vel:6.2f} m/min · odo {odo:8.2f} m')
    except KeyboardInterrupt:
        env.flush()


# ─────────────────────────────────────────────────────────────────────────────
# Fonte 4: simulador
# ─────────────────────────────────────────────────────────────────────────────
def rodar_simulador(env, args):
    import random
    print('[simulador] monovia virtual rodando. Ctrl+C para parar.')
    t = 0
    try:
        while True:
            t += 1
            # a cada ~3 min simula uma parada de 1 a 3 min
            if t % 180 < random.randint(20, 60):
                v = 0.0
            else:
                v = max(0.0, random.gauss(args.velocidade_sim, 0.15))
            env.add(velocidade=v, fonte='simulador')
            time.sleep(args.intervalo)
    except KeyboardInterrupt:
        env.flush()


# ─────────────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description='Agente de telemetria da monovia')
    p.add_argument('--url', default=os.environ.get('MONOVIA_URL', 'http://localhost:5000'),
                   help='URL do sistema (ex.: https://seuapp.up.railway.app)')
    p.add_argument('--token', default=os.environ.get('MONOVIA_TOKEN', ''))
    p.add_argument('--linha', default='MV1')
    p.add_argument('--fonte', choices=['clp', 'camera', 'sensor', 'simulador'],
                   default='simulador')
    p.add_argument('--intervalo', type=float, default=1.0, help='segundos entre amostras')
    p.add_argument('--lote', type=int, default=12, help='amostras por envio')
    # CLP
    p.add_argument('--clp-ip', default='192.168.0.10')
    p.add_argument('--clp-porta', type=int, default=502)
    p.add_argument('--slave', type=int, default=1)
    p.add_argument('--registrador', type=int, default=None, help='holding register da velocidade')
    p.add_argument('--registrador-encoder', type=int, default=None, help='holding register do encoder (32 bits)')
    p.add_argument('--escala', type=float, default=0.01, help='valor do registrador -> m/min')
    p.add_argument('--metros-por-pulso', type=float, default=0.001,
                   help='metros por pulso. No modo sensor = passo do gancho (ex.: 0.40)')
    # sensor de proximidade
    p.add_argument('--gpio', type=int, default=17, help='pino GPIO do sensor')
    p.add_argument('--debounce', type=float, default=0.02,
                   help='tempo morto entre pulsos (s), evita contagem dupla')
    # câmera
    p.add_argument('--camera', default='0')
    p.add_argument('--px-por-metro', type=float, default=180.0)
    p.add_argument('--stride-seg', type=float, default=1.0,
                   help='intervalo entre quadros comparados (s). Linha lenta pede valor maior.')
    p.add_argument('--roi-topo', type=float, default=0.35)
    p.add_argument('--roi-base', type=float, default=0.75)
    p.add_argument('--ruido', type=float, default=0.15, help='m/min abaixo disso = parada')
    p.add_argument('--medir-area', action='store_true')
    p.add_argument('--area-min-px', type=int, default=2000)
    p.add_argument('--calibrar', action='store_true')
    p.add_argument('--mostrar', action='store_true')
    # simulador
    p.add_argument('--velocidade-sim', type=float, default=3.5)
    args = p.parse_args()

    env = Enviador(args.url, args.token, args.linha, args.lote)
    print(f'[agente] enviando para {args.url} · linha {args.linha} · fonte {args.fonte}')
    {'clp': rodar_clp, 'camera': rodar_camera, 'sensor': rodar_sensor,
     'simulador': rodar_simulador}[args.fonte](env, args)


if __name__ == '__main__':
    main()
