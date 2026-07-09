// Lógica compartilhada do dashboard admin e do painel público.
// Define window.DASH_ENDPOINT antes de carregar este arquivo.
let chartTipo, chartProc, chartDia, chartPeso, chartArea, chartTurno;
const ESTADOS={
  PREPARANDO:{t:'Preparando cesto',c:'#F59E0B',i:'hourglass-split'},
  PREENCHER:{t:'Aguardando cadastro',c:'#64748b',i:'pencil-square'},
  FILA_BANHO:{t:'Aguardando banho',c:'#7C5CFC',i:'list-ol'},
  EM_BANHO:{t:'Em banho',c:'#2563EB',i:'droplet-fill'},
};
const BRAND_GREEN='#2BA45C', BRAND_BLUE='#1668C0';

// Estilo profissional global dos gráficos (roda uma vez)
function _setupChartDefaults(){
  if(window._chartReady || typeof Chart==='undefined')return;
  Chart.defaults.font.family="Manrope, system-ui, sans-serif";
  Chart.defaults.font.size=12;
  Chart.defaults.color='#56616F';
  Chart.defaults.plugins.legend.labels.usePointStyle=true;
  Chart.defaults.plugins.legend.labels.boxWidth=8;
  Chart.defaults.plugins.legend.labels.padding=14;
  Chart.defaults.plugins.tooltip.backgroundColor='#16202E';
  Chart.defaults.plugins.tooltip.padding=11;
  Chart.defaults.plugins.tooltip.cornerRadius=9;
  Chart.defaults.plugins.tooltip.titleFont={weight:'700',size:12};
  Chart.defaults.plugins.tooltip.bodyFont={weight:'600',size:12};
  Chart.defaults.plugins.tooltip.displayColors=false;
  window._chartReady=true;
}
// gradiente vertical p/ dar profundidade às barras/áreas
function _grad(ctx, area, c1, c2){
  if(!area)return c1;
  const g=ctx.createLinearGradient(0,area.top,0,area.bottom);
  g.addColorStop(0,c1); g.addColorStop(1,c2);
  return g;
}
const _gridCfg={color:'rgba(86,97,111,.10)',drawBorder:false};

function getRange(){
  const de=document.getElementById('fDe')?.value||'';
  const ate=document.getElementById('fAte')?.value||'';
  const turno=document.getElementById('fTurno')?.value||'';
  const p=new URLSearchParams();
  if(de)p.set('de',de); if(ate)p.set('ate',ate); if(turno)p.set('turno',turno);
  return p.toString()?('?'+p.toString()):'';
}

async function atualizar(){
  const r=await fetch(window.DASH_ENDPOINT+getRange());
  const d=await r.json();
  set('kTotal',d.total); set('kAnd',d.em_andamento);
  set('kBanhoNormal',d.banho_normal!==undefined?d.banho_normal:d.normais);
  set('kBanhoRetrab',d.banho_retrabalho!==undefined?d.banho_retrabalho:d.retrabalhos);
  set('kNormais',d.normais); set('kRetrab',d.retrabalhos);
  set('kPrep',d.media_prep); set('kBanho',d.media_banho);
  set('kEspera',d.media_espera!==undefined?d.media_espera:0);
  set('kPecas',d.pecas_total_geral!==undefined?d.pecas_total_geral:0);
  set('kPeso',d.peso_total_geral!==undefined?d.peso_total_geral:0);
  set('kArea',d.area_total_geral!==undefined?d.area_total_geral:0);
  set('kTotalOps',d.total_ops!==undefined?d.total_ops:0);
  set('kMediaPecas',d.media_pecas_cesto!==undefined?d.media_pecas_cesto:0);
  set('kTaxaRetrab',(d.taxa_retrab!==undefined?d.taxa_retrab:0)+'%');
  renderAtivos(d.ativos);
  renderCharts(d);
  if(document.getElementById('turnoBodyPrep')||document.getElementById('turnoBody')) renderTurnoTables(d);
  if(document.getElementById('operadoresBody')) renderOperadores(d.operadores);
  if(document.getElementById('tbody')) renderTabela(d.registros);
  if(document.getElementById('histBody')) renderHistorico(d.registros);
}
function _renderTurnoTbl(id,t){
  const tb=document.getElementById(id); if(!tb)return;
  if(!t||!t.labels){tb.innerHTML='<tr><td colspan="7" class="empty">Sem dados.</td></tr>';return;}
  const horas=['06:01–15:30','15:31–00:00','00:01–06:00'];
  const sum=a=>a.reduce((x,y)=>x+(+y||0),0);
  let html='';
  for(let i=0;i<3;i++){
    html+=`<tr><td><span class="pill pill-turno">${t.labels[i]}</span></td><td><span class="small">${horas[i]}</span></td>
      <td class="mono"><strong>${t.cestos[i]}</strong></td><td class="mono">${t.pecas[i]}</td>
      <td class="mono">${t.peso[i]}</td><td class="mono">${t.area[i]}</td><td class="mono">${t.retrabalho[i]}</td></tr>`;
  }
  html+=`<tr class="tbl-total"><td colspan="2"><strong>Total</strong></td>
    <td class="mono"><strong>${sum(t.cestos)}</strong></td><td class="mono"><strong>${sum(t.pecas)}</strong></td>
    <td class="mono"><strong>${sum(t.peso).toFixed(1)}</strong></td><td class="mono"><strong>${sum(t.area).toFixed(2)}</strong></td>
    <td class="mono"><strong>${sum(t.retrabalho)}</strong></td></tr>`;
  tb.innerHTML=html;
}
function renderTurnoTables(d){
  _renderTurnoTbl('turnoBodyPrep', d.turnos_prep||d.turnos);
  _renderTurnoTbl('turnoBodyBanho', d.turnos_banho||d.turnos);
  _renderTurnoTbl('turnoBody', d.turnos); // compat (se existir a tabela antiga)
  _renderDiaTurno('diaTurnoPrep', d.dia_turno_prep);
  _renderDiaTurno('diaTurnoBanho', d.dia_turno_banho);
  renderOee(d);
}
function _oeeCor(v){ return v>=85?'#2BA45C' : v>=60?'#F59E0B' : '#D6473F'; }
function _oeeUm(pref, o){
  const val=document.getElementById(pref+'Val'); if(!val) return;
  if(!o){ val.textContent='—'; return; }
  val.textContent=o.oee.toFixed(1)+'%';
  val.style.color=_oeeCor(o.oee);
  const fill=document.getElementById(pref+'Fill');
  if(fill){ fill.style.width=Math.max(0,Math.min(100,o.oee))+'%'; fill.style.background=_oeeCor(o.oee); }
  const set=(id,v)=>{const e=document.getElementById(id); if(e)e.textContent=v.toFixed(1)+'%';};
  set(pref+'D',o.disponibilidade); set(pref+'P',o.performance); set(pref+'Q',o.qualidade);
  const det=document.getElementById(pref+'Det');
  if(det) det.innerHTML=`${o.cestos} cestos · rodou ${o.tempo_rodando_min} min de ${o.tempo_planejado_min} min planejados`
    +` · padrão ${o.padrao_min} min/cesto`+(o.recursos>1?` × ${o.recursos} postos`:'')
    +(o.retrabalhos?` · ${o.retrabalhos} retrabalho(s)`:'');
}
function renderOee(d){
  const grid=document.getElementById('oeeGrid'); if(!grid) return;
  if(!d.oee_prep && !d.oee_banho){ grid.style.display='none'; return; }
  grid.style.display='';
  _oeeUm('oeePrep', d.oee_prep);
  _oeeUm('oeeBanho', d.oee_banho);
}
function _renderDiaTurno(id, linhas){
  const tb=document.getElementById(id); if(!tb)return;
  if(!linhas||!linhas.length){tb.innerHTML='<tr><td colspan="5" class="empty">Sem dados no período.</td></tr>';return;}
  const tot=[0,0,0];
  let html=linhas.map(l=>{
    tot[0]+=l.turnos[0]; tot[1]+=l.turnos[1]; tot[2]+=l.turnos[2];
    return `<tr><td><strong>${l.dia}</strong></td>
      <td class="mono">${l.turnos[0]}</td><td class="mono">${l.turnos[1]}</td><td class="mono">${l.turnos[2]}</td>
      <td class="mono"><strong>${l.total}</strong></td></tr>`;
  }).join('');
  html+=`<tr class="tbl-total"><td><strong>Total</strong></td>
    <td class="mono"><strong>${tot[0]}</strong></td><td class="mono"><strong>${tot[1]}</strong></td>
    <td class="mono"><strong>${tot[2]}</strong></td><td class="mono"><strong>${tot[0]+tot[1]+tot[2]}</strong></td></tr>`;
  tb.innerHTML=html;
}
function renderOperadores(ops){
  const tb=document.getElementById('operadoresBody'); if(!tb)return;
  if(!ops||!ops.length){tb.innerHTML='<tr><td colspan="4" class="empty">Sem dados no período.</td></tr>';return;}
  tb.innerHTML=ops.map((o,i)=>`<tr><td>${i+1}</td><td><strong>${o.nome||'—'}</strong></td>
    <td class="mono">${o.cestos}</td><td class="mono">${o.pecas}</td></tr>`).join('');
}
function _opsTxt(r){
  const ops=(r.itens&&r.itens.length? r.itens.map(it=>it.ordem):[r.ordem]).filter(Boolean);
  return ops.length? ops.join(', ') : '—';
}
function renderHistorico(regs){
  const tb=document.getElementById('histBody');
  if(!regs||!regs.length){tb.innerHTML='<tr><td colspan="12" class="empty">Nenhum cesto concluído ainda.</td></tr>';return;}
  tb.innerHTML=regs.map(r=>{
    // texto pesquisável: todas as OPs, códigos e descrições do cesto
    const itens=(r.itens&&r.itens.length)?r.itens:[{ordem:r.ordem,material:r.material,texto_breve:r.texto_breve}];
    const busca=(r.numero_cesto+' '+itens.map(it=>(it.ordem||'')+' '+(it.material||'')+' '+(it.texto_breve||'')).join(' ')+' '+(r.processo||'')+' '+(r.tipo||'')+' '+(r.turno_lbl||'')).toLowerCase();
    return `<tr class="${r.tipo==='Retrabalho'?'retrab':''}" data-busca="${busca.replace(/"/g,'')}">
    <td>${r.id}</td><td><strong>${r.numero_cesto}</strong></td><td>${_opsTxt(r)}</td>
    <td>${r.material||'—'}</td><td><span class="small">${r.texto_breve||'—'}</span></td><td>${r.qtd_total}</td>
    <td>${r.processo||'—'}</td><td><span class="pill ${r.tipo==='Retrabalho'?'pill-retrab':'pill-normal'}">${r.tipo}</span></td>
    <td><span class="pill pill-turno">${r.turno_lbl||'—'}</span></td>
    <td class="mono">${r.prep_minutos}</td><td class="mono">${r.banho_minutos}</td>
    <td><span class="small">${r.banho_fim||''}</span></td>
  </tr>`;}).join('');
  if(typeof aplicarBuscaHistorico==='function')aplicarBuscaHistorico();
}
function set(id,v){const e=document.getElementById(id);if(e)e.textContent=v;}

function renderAtivos(ativos){
  const c=document.getElementById('andamento'); if(!c)return;
  if(!ativos.length){c.innerHTML='<div class="empty"><i class="bi bi-clipboard-check"></i>Nenhum cesto em andamento.</div>';return;}
  c.innerHTML=ativos.map(a=>{
    const e=ESTADOS[a.estado]||{t:a.estado,c:'#888',i:'circle'};
    return `<div class="and-chip" style="border-left:4px solid ${e.c}">
      <div class="and-top"><span class="and-cesto">${a.numero_cesto}</span><i class="bi bi-${e.i}" style="color:${e.c}"></i></div>
      <div class="and-st" style="color:${e.c}">${e.t}</div>
      <div class="and-op">OP ${a.ordem||'—'}</div>
    </div>`;
  }).join('');
}

function renderCharts(d){
  _setupChartDefaults();
  // se a área de gráficos ainda está oculta (visão Histórico não aberta), aguarda
  const _anchor=document.getElementById('chartTipo');
  if(_anchor && _anchor.offsetParent===null && !chartTipo) return;
  const labels=Object.keys(d.por_processo), data=Object.values(d.por_processo);
  const diaL=Object.keys(d.por_dia), diaD=Object.values(d.por_dia);
  const baseOpts={responsive:true,maintainAspectRatio:true,aspectRatio:1.7,animation:{duration:600}};
  const doughOpts={responsive:true,maintainAspectRatio:true,aspectRatio:1.5,animation:{duration:600}};
  if(!chartTipo){
    chartTipo=new Chart(document.getElementById('chartTipo'),{type:'doughnut',
      data:{labels:['Normal','Retrabalho'],datasets:[{data:[d.normais,d.retrabalhos],
        backgroundColor:[BRAND_GREEN,BRAND_BLUE],borderWidth:3,borderColor:'#fff',hoverOffset:6}]},
      options:{...doughOpts,cutout:'68%',plugins:{legend:{position:'bottom'}}}});

    chartProc=new Chart(document.getElementById('chartProc'),{type:'bar',
      data:{labels,datasets:[{data,borderRadius:8,maxBarThickness:46,
        backgroundColor:c=>_grad(c.chart.ctx,c.chart.chartArea,'#2E8FD6',BRAND_BLUE)}]},
      options:{...baseOpts,plugins:{legend:{display:false}},
        scales:{y:{beginAtZero:true,ticks:{stepSize:1,precision:0},grid:_gridCfg},x:{grid:{display:false}}}}});

    if(document.getElementById('chartDia'))
      chartDia=new Chart(document.getElementById('chartDia'),{type:'line',
        data:{labels:diaL,datasets:[{data:diaD,borderColor:BRAND_GREEN,borderWidth:3,tension:.35,fill:true,
          pointRadius:3,pointHoverRadius:6,pointBackgroundColor:BRAND_GREEN,pointBorderColor:'#fff',pointBorderWidth:2,
          backgroundColor:c=>_grad(c.chart.ctx,c.chart.chartArea,'rgba(43,164,92,.28)','rgba(43,164,92,0)')}]},
        options:{...baseOpts,plugins:{legend:{display:false}},
          scales:{y:{beginAtZero:true,ticks:{stepSize:1,precision:0},grid:_gridCfg},x:{grid:{display:false}}}}});
  }else{
    chartTipo.data.datasets[0].data=[d.normais,d.retrabalhos];chartTipo.update();
    chartProc.data.labels=labels;chartProc.data.datasets[0].data=data;chartProc.update();
    if(chartDia){chartDia.data.labels=diaL;chartDia.data.datasets[0].data=diaD;chartDia.update();}
  }
  // Cestos por turno — comparativo pré-banho × banho (barras agrupadas)
  const cvT=document.getElementById('chartTurno');
  const tPrep=d.turnos_prep||d.turnos, tBanho=d.turnos_banho||d.turnos;
  if(cvT && tPrep){
    if(!chartTurno){
      chartTurno=new Chart(cvT,{type:'bar',
        data:{labels:tPrep.labels,datasets:[
          {label:'Pré-banho',data:tPrep.cestos,borderRadius:7,maxBarThickness:34,backgroundColor:'#7C5CFC'},
          {label:'Banho',data:tBanho.cestos,borderRadius:7,maxBarThickness:34,backgroundColor:'#1668C0'}]},
        options:{...baseOpts,plugins:{legend:{display:true,position:'bottom'}},
          scales:{y:{beginAtZero:true,ticks:{stepSize:1,precision:0},grid:_gridCfg},x:{grid:{display:false}}}}});
    }else{
      chartTurno.data.labels=tPrep.labels;
      chartTurno.data.datasets[0].data=tPrep.cestos;
      chartTurno.data.datasets[1].data=tBanho.cestos;
      chartTurno.update();
    }
  }
  // peso e área por dia (linha)
  const pesoL=Object.keys(d.peso_por_dia||{}), pesoD=Object.values(d.peso_por_dia||{});
  const areaL=Object.keys(d.area_por_dia||{}), areaD=Object.values(d.area_por_dia||{});
  if(document.getElementById('chartPeso')){
    if(!chartPeso){
      chartPeso=new Chart(document.getElementById('chartPeso'),{type:'line',
        data:{labels:pesoL,datasets:[{label:'kg',data:pesoD,borderColor:BRAND_GREEN,borderWidth:3,tension:.35,fill:true,
          pointRadius:3,pointHoverRadius:6,pointBackgroundColor:BRAND_GREEN,pointBorderColor:'#fff',pointBorderWidth:2,
          backgroundColor:c=>_grad(c.chart.ctx,c.chart.chartArea,'rgba(43,164,92,.28)','rgba(43,164,92,0)')}]},
        options:{...baseOpts,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,grid:_gridCfg},x:{grid:{display:false}}}}});
    }else{chartPeso.data.labels=pesoL;chartPeso.data.datasets[0].data=pesoD;chartPeso.update();}
  }
  if(document.getElementById('chartArea')){
    if(!chartArea){
      chartArea=new Chart(document.getElementById('chartArea'),{type:'line',
        data:{labels:areaL,datasets:[{label:'m²',data:areaD,borderColor:BRAND_BLUE,borderWidth:3,tension:.35,fill:true,
          pointRadius:3,pointHoverRadius:6,pointBackgroundColor:BRAND_BLUE,pointBorderColor:'#fff',pointBorderWidth:2,
          backgroundColor:c=>_grad(c.chart.ctx,c.chart.chartArea,'rgba(22,104,192,.28)','rgba(22,104,192,0)')}]},
        options:{...baseOpts,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,grid:_gridCfg},x:{grid:{display:false}}}}});
    }else{chartArea.data.labels=areaL;chartArea.data.datasets[0].data=areaD;chartArea.update();}
  }
}

function renderTabela(regs){
  const tb=document.getElementById('tbody');
  if(!regs.length){tb.innerHTML='<tr><td colspan="12" class="empty">Nenhum registro no período.</td></tr>';return;}
  tb.innerHTML=regs.map(r=>`<tr class="${r.tipo==='Retrabalho'?'retrab':''}"
    data-txt="${(r.numero_cesto+' '+r.ordem+' '+r.material+' '+(r.texto_breve||'')+' '+r.operador_prep+' '+r.operador_banho).toLowerCase()}"
    data-tipo="${r.tipo}" data-proc="${r.processo}">
    <td>${r.id}</td><td><strong>${r.numero_cesto}</strong></td><td>${_opsTxt(r)}</td>
    <td>${r.material||'—'}</td><td><span class="small">${r.texto_breve||'—'}</span></td><td>${r.qtd_total}</td>
    <td>${r.processo||'—'}</td><td><span class="pill ${r.tipo==='Retrabalho'?'pill-retrab':'pill-normal'}">${r.tipo}</span></td>
    <td><span class="pill pill-turno">${r.turno_lbl||'—'}</span></td>
    <td class="mono">${r.prep_minutos}</td><td class="mono">${r.banho_minutos}</td>
    <td><span class="small">${r.banho_fim||''}</span></td>
  </tr>`).join('');
  aplicarFiltroTexto();
}

function aplicarFiltroTexto(){
  const txt=(document.getElementById('fTexto')?.value||'').toLowerCase();
  const tipo=document.getElementById('fTipo')?.value||'';
  const proc=document.getElementById('fProc')?.value||'';
  document.querySelectorAll('#tbody tr[data-txt]').forEach(tr=>{
    const ok=(!txt||tr.dataset.txt.includes(txt))&&(!tipo||tr.dataset.tipo===tipo)&&(!proc||tr.dataset.proc===proc);
    tr.style.display=ok?'':'none';
  });
}

document.addEventListener('DOMContentLoaded',()=>{
  ['fTexto','fTipo','fProc'].forEach(id=>{const e=document.getElementById(id);if(e)e.addEventListener(id==='fTexto'?'input':'change',aplicarFiltroTexto);});
  ['fDe','fAte','fTurno'].forEach(id=>{const e=document.getElementById(id);if(e)e.addEventListener('change',atualizar);});
  atualizar();
  setInterval(atualizar,8000);
});
window.atualizar=atualizar;
function resizeCharts(){
  [chartTipo,chartProc,chartDia,chartPeso,chartArea,chartTurno].forEach(c=>{try{c&&c.resize();}catch(_){}});
}
window.resizeCharts=resizeCharts;
