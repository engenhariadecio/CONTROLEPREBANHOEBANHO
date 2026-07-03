/* ════════════════════════════════════════════════════════════════
   Jornada de trabalho (espelho da lógica do servidor em app.py).
   Faz o cronômetro de ESPERA (fila) "congelar" fora do expediente.
   Usa 3 turnos + exceções (TURNO EXTRA / PARADA). Lê /api/config/agenda.
   ════════════════════════════════════════════════════════════════ */
const FUSO_LOCAL_HORAS = 3; // Brasil UTC-3
let _AGENDA = {
  usar_jornada: true,
  turnos: [
    { nome: '1º turno', ini: '06:01', fim: '15:30', dias: [0,1,2,3,4], ativo: true },
    { nome: '2º turno', ini: '15:31', fim: '00:00', dias: [0,1,2,3,4], ativo: true },
    { nome: '3º turno', ini: '00:01', fim: '06:00', dias: [0,1,2,3,4,5], ativo: true }
  ],
  excecoes: [], dias_folga: []
};

async function carregarAgenda() {
  try {
    const r = await fetch('/api/config/agenda');
    if (r.ok) {
      const j = await r.json();
      if (j && typeof j === 'object' && Object.keys(j).length) _AGENDA = j;
      if (!_AGENDA.turnos || !_AGENDA.turnos.length) _AGENDA.turnos = [];
    }
  } catch (_) {}
  return _AGENDA;
}

function _minDia(s, padrao) {
  try { const [h, m] = String(s).split(':'); const v = (+h) * 60 + (+m); return isNaN(v) ? padrao : v; }
  catch (_) { return padrao; }
}
function _turnoJanela(t) {
  let i = _minDia(t.ini, 0), f = _minDia(t.fim, 1440);
  if (f === 0) f = 1440;
  if (f <= i) f = 1440;
  return [i, f];
}
function _merge(ivs) {
  ivs = ivs.slice().sort((a, b) => a[0] - b[0]);
  const out = [];
  for (const [a, b] of ivs) {
    if (out.length && a <= out[out.length - 1][1]) out[out.length - 1][1] = Math.max(out[out.length - 1][1], b);
    else out.push([a, b]);
  }
  return out;
}
function _subtrair(base, corte) {
  const [ca, cb] = corte, out = [];
  for (const [a, b] of base) {
    if (cb <= a || ca >= b) out.push([a, b]);
    else { if (a < ca) out.push([a, ca]); if (cb < b) out.push([cb, b]); }
  }
  return out;
}
/* d = objeto Date já em horário LOCAL (via getUTC*). Retorna janelas [minInicio,minFim]. */
function _janelasDoDia(d, cfg) {
  const wd = (d.getUTCDay() + 6) % 7; // 0=seg ... 6=dom
  const s = d.getUTCFullYear() + '-' + String(d.getUTCMonth() + 1).padStart(2, '0') +
            '-' + String(d.getUTCDate()).padStart(2, '0');
  let jan = [];
  for (const t of (cfg.turnos || [])) {
    if (t.ativo === false) continue;
    if ((t.dias || []).includes(wd)) jan.push(_turnoJanela(t));
  }
  if ((cfg.dias_folga || []).includes(s)) jan = [];
  jan = _merge(jan);
  for (const ex of (cfg.excecoes || [])) {
    const d0 = (ex.data || '').trim(), d1 = (ex.data_fim || '').trim() || d0;
    if (!d0 || !(d0 <= s && s <= d1)) continue;
    const temHora = !!(ex.ini || ex.fim);
    let a = _minDia(ex.ini, 0), b = _minDia(ex.fim, 1440);
    if (!ex.fim || ex.fim === '00:00') b = 1440;
    if (!temHora) { a = 0; b = 1440; }
    if (ex.tipo === 'PARADA') jan = _subtrair(jan, [a, b]);
    else jan = _merge(jan.concat([[a, b]]));
  }
  return _merge(jan);
}

/* Segundos úteis entre dois instantes (ms epoch UTC), respeitando a jornada. */
function tempoUtilSegundos(iniMs, fimMs, cfg) {
  cfg = cfg || _AGENDA;
  if (!iniMs || !fimMs || fimMs <= iniMs) return 0;
  if (!cfg.usar_jornada) return Math.floor((fimMs - iniMs) / 1000);
  if (!cfg.turnos || !cfg.turnos.length) return Math.floor((fimMs - iniMs) / 1000);
  const desloc = FUSO_LOCAL_HORAS * 3600 * 1000;
  const iniL = iniMs - desloc, fimL = fimMs - desloc;
  let cur = new Date(iniL);
  let total = 0, lim = 0;
  while (cur.getTime() < fimL && lim < 4000) {
    lim++;
    const Y = cur.getUTCFullYear(), M = cur.getUTCMonth(), D = cur.getUTCDate();
    const meiaNoite = Date.UTC(Y, M, D);
    const qs = Math.max(iniL, meiaNoite);
    const qe = Math.min(fimL, Date.UTC(Y, M, D + 1));
    if (qe > qs) {
      const qsMin = (qs - meiaNoite) / 60000, qeMin = (qe - meiaNoite) / 60000;
      for (const [a, b] of _janelasDoDia(cur, cfg)) {
        const ov = Math.min(qeMin, b) - Math.max(qsMin, a);
        if (ov > 0) total += ov * 60;
      }
    }
    cur = new Date(Date.UTC(Y, M, D + 1));
  }
  return Math.max(0, Math.floor(total));
}

function fmtHMS(s) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  const mm = String(m).padStart(2, '0'), xx = String(x).padStart(2, '0');
  return h > 0 ? `${h}:${mm}:${xx}` : `${mm}:${xx}`;
}
