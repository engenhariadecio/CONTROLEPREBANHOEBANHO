# Instalação da captura da monovia

Guia prático de campo: o que comprar, onde montar, como calibrar e como validar.

---

## Antes de tudo: você talvez não precise de câmera

Para **velocidade e odômetro**, um sensor de proximidade contando ganchos é
melhor que qualquer câmera. A monovia já tem ganchos igualmente espaçados — cada
gancho que passa é uma distância conhecida. Somando os pulsos você tem o odômetro
exato, sem calibração, sem iluminação, sem CPU e sem manutenção.

| | Sensor indutivo | Câmera |
|---|---|---|
| Custo | R$ 80–250 | R$ 400–3.000 |
| Calibração | nenhuma (passo do gancho) | px/metro, refazer se mexer |
| Sensível a luz/sujeira | não | sim |
| Erro típico | ~0 (é contagem) | 2–5% |
| Também mede área | não | sim |

**Recomendação:** sensor para velocidade/odômetro, câmera só se você quiser
também área processada e inspeção. As duas fontes convivem — mandam para o mesmo
endpoint com `fonte` diferente.

### Sensor: lista de compras e montagem

- Sensor indutivo NPN NA, 3 fios, distância de 8 mm (ex.: LJ12A3-4-Z/BX) — se os
  ganchos forem de aço. Se forem pintados ou de plástico, use fotoelétrico
  retro-reflexivo.
- Raspberry Pi 4 ou 5 (ou Pi Zero 2 W) + fonte.
- Optoacoplador (PC817) ou divisor resistivo se o sensor for de 24 V — **o GPIO
  do Pi é 3,3 V e queima com 24 V direto**.

Monte o sensor no trilho apontando para onde os ganchos passam mais perto, a
4–6 mm. Depois:

```bash
pip install requests gpiozero
python agente_monovia.py --fonte sensor --gpio 17 --metros-por-pulso 0.40 \
  --url https://SEU-APP --token SEU_TOKEN --mostrar
```

`--metros-por-pulso` é o **passo do gancho** medido com trena (distância entre
dois ganchos consecutivos). Meça 10 vãos e divida por 10 — reduz o erro.

Validação: marque um gancho com fita, cronometre uma volta completa e compare os
metros que o sistema acumulou com o perímetro real do trilho.

---

## Câmera: quando vale a pena

Vale se você quiser **área processada** e, mais adiante, inspeção (gancho vazio,
falha de camada). Para velocidade sozinha, o sensor ganha.

### Hardware

- **Câmera:** IP com RTSP (Intelbras/Hikvision, ~R$ 400) ou webcam USB decente
  (Logitech C920). Câmera industrial só se for fazer inspeção depois.
- **Computador:** Raspberry Pi 5 dá conta de fluxo óptico a 640×480. Se for medir
  área também, um mini-PC com Intel N100 é mais folgado.
- **Iluminação:** barra de LED branca, fixa. Luz constante importa mais que luz
  forte — evite janelas e lâmpadas que piscam.

### Onde montar

**Para velocidade:** perpendicular ao trilho, a 1–2 m, enquadrando um trecho onde
apareçam ganchos ou elos da corrente (a imagem precisa de textura para o fluxo
óptico "agarrar"). Fixe rígido — se a câmera vibrar ou for encostada, a
calibração vai embora.

**Para área:** perpendicular às peças, com um **fundo de contraste** atrás do
trilho (chapa branca ou azul, ~1×2 m). Sem fundo, a silhueta se confunde com o
que estiver na fábrica atrás e a medição vira loteria.

### O detalhe que quase estraga tudo

A monovia anda ~3,5 m/min = **5,8 cm/s**. A 180 px/m, isso é 10,5 px/s — ou
**0,42 px entre dois quadros a 25 fps**. Menos de um pixel: comparando quadros
consecutivos, o fluxo óptico mede ruído, não movimento.

Por isso o agente guarda um quadro de referência e só compara depois de
`--stride-seg` segundos (padrão 1 s → ~10 px, mensurável). Se a sua linha for
ainda mais lenta, aumente para 2 s. O cálculo usa o tempo **real** entre os
quadros, não o fps nominal, porque webcam USB entrega menos quadros do que promete
quando a CPU aperta.

### Calibração (uma vez, com a linha rodando)

1. Marque dois pontos no trilho a uma distância conhecida — quanto maior, melhor
   (2 m dá menos erro que 50 cm).
2. `python agente_monovia.py --fonte camera --calibrar`
3. Clique nos dois pontos na imagem e informe a distância real.
4. O script imprime `PX_POR_METRO`. Use no `--px-por-metro`.

Refaça a calibração toda vez que a câmera for movida, trocada de lente ou de
resolução.

### Rodando

```bash
pip install requests opencv-python
python agente_monovia.py --fonte camera \
  --camera "rtsp://usuario:senha@192.168.0.50/cam/realmonitor?channel=1&subtype=0" \
  --px-por-metro 182 --stride-seg 1.0 --medir-area \
  --url https://SEU-APP --token SEU_TOKEN --mostrar
```

`--mostrar` imprime cada medição no terminal — deixe rodando 10 minutos e compare
com um cronômetro e uma trena antes de confiar.

### Validação em campo

Marque dois pontos do trilho a 5 m. Cronometre um gancho indo de um ao outro.
`5 / (segundos/60)` é a velocidade real; compare com o que a tela `/monovia`
mostra. Erro aceitável: até 5%. Acima disso, revise a calibração ou aumente o
`--stride-seg`.

---

## Sobre a "IA básica"

Para velocidade e área, o que está implementado é visão computacional clássica
calibrada — fluxo óptico Lucas-Kanade e subtração de fundo. É mais precisa que
rede neural nessa tarefa, roda em hardware barato e não exige dados de treino.

Rede treinada só compensa para **classificar**: gancho vazio, peça com defeito,
tipo de peça. E note que "que peça é" você **não** precisa perguntar à IA — o
odômetro já responde com exatidão. Quando quiser treinar algo, o próprio sistema
já terá gerado um dataset rotulado de graça, porque toda detecção nasce vinculada
ao lote certo (`monovia_deteccoes.lote_id`).

Para gravar detecções da câmera:

```bash
curl -X POST https://SEU-APP/api/monovia/deteccao \
  -H 'X-Monovia-Token: SEU_TOKEN' -H 'Content-Type: application/json' \
  -d '{"linha":"MV1","estacao":"cam1","n_pecas":12,"area_m2":3.4,
       "confianca":0.91,"imagem_ref":"cam1/2026-08-26T10-31-02.jpg"}'
```

A resposta devolve `lote_id` e `cesto` — o vínculo já vem pronto.

---

## Ordem sugerida de implantação

1. **Sem hardware:** use o sistema já. Tempos de espera, carga e descarga são
   medidos só com os toques do operador; os metros vão na mão no fim da carga.
2. **CLP:** se a monovia tem inversor com saída de frequência ou encoder, esse é
   o caminho mais rápido. Peça o mapa Modbus ao fabricante.
3. **Sensor de gancho:** se não houver CLP acessível. Uma tarde de trabalho.
4. **Câmera:** quando quiser área processada.
5. **IA de classificação:** por último, com o histórico que o sistema já rotulou.
