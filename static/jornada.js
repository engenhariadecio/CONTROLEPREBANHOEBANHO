/* ════════════════════════════════════════════════════════════════
   Jornada de trabalho (espelho da lógica do servidor em app.py).
   Faz o cronômetro de ESPERA (fila) "congelar" fora do expediente.
   Lê a agenda em /api/config/agenda.
   ════════════════════════════════════════════════════════════════ */
const FUSO_LOCAL_HORAS = 3; // Brasil UTC-3
let _AGENDA = {
  usar_jornada: true, trabalha_sabado: false, trabalha_domingo: false,
  hora_inicio: '07:00', hora_fim: '18:00', dias_extra: [], dias_folga: []
};

async function carregarAgenda() {
  try {
    const r = await fetch('/api/config/agenda');
    if (r.ok) {
      const j = await r.json();
      if (j && typeof j === 'object' && Object.keys(j).length) _AGENDA = j;
    }
  } catch (_) {}
  return _AGENDA;
}

function _hhmmMin(s, padrao) {
  try { const [h, m] = String(s).split(':'); return (+h) * 60 + (+m); }
  catch (_) { const [h, m] = padrao.split(':'); return (+h) * 60 + (+m); }
}

function _diaTrabalhadoUTC(d, cfg) {
  const s = d.getUTCFullYear() + '-' + String(d.getUTCMonth() + 1).padStart(2, '0') +
            '-' + String(d.getUTCDate()).padStart(2, '0');
  if ((cfg.dias_folga || []).includes(s)) return false;
  if ((cfg.dias_extra || []).includes(s)) return true;
  const wd = (d.getUTCDay() + 6) % 7; // 0=seg ... 6=dom
  if (wd <= 4) return true;
  if (wd === 5) return !!cfg.trabalha_sabado;
  return !!cfg.trabalha_domingo;
}

/* Segundos úteis entre dois instantes (ms epoch UTC), respeitando a jornada. */
function tempoUtilSegundos(iniMs, fimMs, cfg) {
  cfg = cfg || _AGENDA;
  if (!iniMs || !fimMs || fimMs <= iniMs) return 0;
  if (!cfg.usar_jornada) return Math.floor((fimMs - iniMs) / 1000);
  const desloc = FUSO_LOCAL_HORAS * 3600 * 1000;
  let cur = new Date(iniMs - desloc);
  const fimL = fimMs - desloc;
  const hi = _hhmmMin(cfg.hora_inicio, '07:00');
  const hf = _hhmmMin(cfg.hora_fim, '18:00');
  let total = 0, lim = 0;
  while (cur.getTime() < fimL && lim < 4000) {
    lim++;
    const Y = cur.getUTCFullYear(), M = cur.getUTCMonth(), D = cur.getUTCDate();
    if (_diaTrabalhadoUTC(cur, cfg)) {
      const winIni = Date.UTC(Y, M, D, Math.floor(hi / 60), hi % 60);
      const winFim = Date.UTC(Y, M, D, Math.floor(hf / 60), hf % 60);
      const a = Math.max(cur.getTime(), winIni);
      const b = Math.min(fimL, winFim);
      if (b > a) total += (b - a) / 1000;
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
