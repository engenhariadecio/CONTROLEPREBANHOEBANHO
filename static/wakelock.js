/* Mantém a TELA LIGADA enquanto o sistema estiver aberto no navegador.
   Pensado para o tablet/TV do chão de fábrica, que fica horas na mesma tela.

   Três camadas, porque nenhuma sozinha cobre todos os aparelhos:

   1. Screen Wake Lock API — o caminho oficial. Exige HTTPS (ou localhost) e só
      vale enquanto a aba está VISÍVEL: o navegador solta o bloqueio sozinho ao
      trocar de aba, minimizar ou bloquear o aparelho.
   2. Vigia periódico — o sistema também pode soltar o bloqueio por conta
      própria (bateria fraca, economia de energia). Sem alguém conferindo, a
      tela volta a apagar e ninguém percebe. A cada 20 s reconferimos e
      repedimos se preciso.
   3. Vídeo mudo em loop — para quem não tem a API (Safari antigo, WebView). Um
      vídeo tocando segura a tela acesa na prática.

   Diagnóstico: window.wakeLockInfo() devolve o estado atual, e a página ganha
   o atributo data-wakelock = "ativo" | "video" | "falhou".
*/
(function () {
  'use strict';

  var wakeLock = null;          // objeto da API, quando ativo
  var video = null;             // fallback
  var modo = 'iniciando';
  var timer = null;
  var tentativasApi = 0;
  var API = !!(('wakeLock' in navigator) && navigator.wakeLock
               && typeof navigator.wakeLock.request === 'function');
  var INTERVALO = 20000;        // vigia a cada 20 s

  function marcar(novo) {
    if (modo === novo) return;
    modo = novo;
    try { document.documentElement.setAttribute('data-wakelock', novo); } catch (e) {}
    try { window.dispatchEvent(new CustomEvent('wakelock', { detail: { modo: novo } })); } catch (e) {}
  }

  // ── 1. Screen Wake Lock API ────────────────────────────────────────────────
  function pedirApi() {
    if (!API) return Promise.resolve(false);
    // Atenção: um bloqueio já solto continua referenciado na variável. Sem
    // checar `released` aqui, o vigia acharia que ainda está valendo e nunca
    // pediria outro — a tela voltaria a apagar em silêncio.
    if (wakeLock && !wakeLock.released) return Promise.resolve(true);
    wakeLock = null;
    return navigator.wakeLock.request('screen').then(function (lock) {
      wakeLock = lock;
      tentativasApi = 0;
      marcar('ativo');
      // O 'release' dispara tanto quando o navegador solta (aba oculta) quanto
      // quando o sistema decide soltar. Se a página ainda está à vista, pedimos
      // de novo na hora — é o caso que o vigia sozinho demoraria a cobrir.
      lock.addEventListener('release', function () {
        wakeLock = null;
        if (document.visibilityState === 'visible') setTimeout(ativar, 300);
      });
      return true;
    }).catch(function () {
      tentativasApi++;
      return false;
    });
  }

  // ── 3. Vídeo mudo em loop (fallback) ───────────────────────────────────────
  function criarVideo() {
    if (video || !document.body) return;
    var v = document.createElement('video');
    v.setAttribute('muted', '');
    v.setAttribute('playsinline', '');      // iOS: sem isso abre em tela cheia
    v.setAttribute('webkit-playsinline', '');
    v.setAttribute('aria-hidden', 'true');
    v.muted = true;
    v.loop = true;
    v.style.cssText = 'position:fixed;right:0;bottom:0;width:1px;height:1px;' +
                      'opacity:0;pointer-events:none;';
    var mp4 = document.createElement('source');
    mp4.type = 'video/mp4';
    mp4.src = 'data:video/mp4;base64,AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAMVbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAA+gAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAj90cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAA+gAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAABAAAAAQAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAAPoAAAAAAABAAAAAAG3bWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAABAAAAAQABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAABYm1pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAASJzdGJsAAAAvnN0c2QAAAAAAAAAAQAAAK5hdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAABAAEABIAAAASAAAAAAAAAABFUxhdmM2MC4zMS4xMDIgbGlieDI2NAAAAAAAAAAAAAAAGP//AAAANGF2Y0MBZAAK/+EAF2dkAAqs2V7ARAAAAwAEAAADAAg8SJZYAQAGaOvjyyLA/fj4AAAAABBwYXNwAAAAAQAAAAEAAAAUYnRydAAAAAAAABYoAAAWKAAAABhzdHRzAAAAAAAAAAEAAAABAABAAAAAABxzdHNjAAAAAAAAAAEAAAABAAAAAQAAAAEAAAAUc3RzegAAAAAAAALFAAAAAQAAABRzdGNvAAAAAAAAAAEAAANFAAAAYnVkdGEAAABabWV0YQAAAAAAAAAhaGRscgAAAAAAAAAAbWRpcmFwcGwAAAAAAAAAAAAAAAAtaWxzdAAAACWpdG9vAAAAHWRhdGEAAAABAAAAAExhdmY2MC4xNi4xMDAAAAAIZnJlZQAAAs1tZGF0AAACrQYF//+p3EXpvebZSLeWLNgg2SPu73gyNjQgLSBjb3JlIDE2NCByMzEwOCAzMWUxOWY5IC0gSC4yNjQvTVBFRy00IEFWQyBjb2RlYyAtIENvcHlsZWZ0IDIwMDMtMjAyMyAtIGh0dHA6Ly93d3cudmlkZW9sYW4ub3JnL3gyNjQuaHRtbCAtIG9wdGlvbnM6IGNhYmFjPTEgcmVmPTMgZGVibG9jaz0xOjA6MCBhbmFseXNlPTB4MzoweDExMyBtZT1oZXggc3VibWU9NyBwc3k9MSBwc3lfcmQ9MS4wMDowLjAwIG1peGVkX3JlZj0xIG1lX3JhbmdlPTE2IGNocm9tYV9tZT0xIHRyZWxsaXM9MSA4eDhkY3Q9MSBjcW09MCBkZWFkem9uZT0yMSwxMSBmYXN0X3Bza2lwPTEgY2hyb21hX3FwX29mZnNldD0tMiB0aHJlYWRzPTEgbG9va2FoZWFkX3RocmVhZHM9MSBzbGljZWRfdGhyZWFkcz0wIG5yPTAgZGVjaW1hdGU9MSBpbnRlcmxhY2VkPTAgYmx1cmF5X2NvbXBhdD0wIGNvbnN0cmFpbmVkX2ludHJhPTAgYmZyYW1lcz0zIGJfcHlyYW1pZD0yIGJfYWRhcHQ9MSBiX2JpYXM9MCBkaXJlY3Q9MSB3ZWlnaHRiPTEgb3Blbl9nb3A9MCB3ZWlnaHRwPTIga2V5aW50PTI1MCBrZXlpbnRfbWluPTEgc2NlbmVjdXQ9NDAgaW50cmFfcmVmcmVzaD0wIHJjX2xvb2thaGVhZD00MCByYz1jcmYgbWJ0cmVlPTEgY3JmPTIzLjAgcWNvbXA9MC42MCBxcG1pbj0wIHFwbWF4PTY5IHFwc3RlcD00IGlwX3JhdGlvPTEuNDAgYXE9MToxLjAwAIAAAAAQZYiEABX//vfJ78Cm69vfgQ==';
    var webm = document.createElement('source');
    webm.type = 'video/webm';
    webm.src = 'data:video/webm;base64,GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwEAAAAAAAHkEU2bdLpNu4tTq4QVSalmU6yBoU27i1OrhBZUrmtTrIHYTbuMU6uEElTDZ1OsggEeTbuMU6uEHFO7a1OsggHO7AEAAAAAAABZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmsirXsYMPQkBNgI1MYXZmNjAuMTYuMTAwV0GNTGF2ZjYwLjE2LjEwMESJiECPQAAAAAAAFlSua8GuAQAAAAAAADjXgQFzxYgvgQ5phgFzaJyBACK1nIN1bmSIgQCGhVZfVlA5g4EBI+ODhDuaygDgibCBELqBEJqBAhJUw2dAgHNzoGPAgGfImkWjh0VOQ09ERVJEh41MYXZmNjAuMTYuMTAwc3PaY8CLY8WIL4EOaYYBc2hnyKVFo4dFTkNPREVSRIeYTGF2YzYwLjMxLjEwMiBsaWJ2cHgtdnA5Z8ihRaOIRFVSQVRJT05Eh5MwMDowMDowMS4wMDAwMDAwMDAAH0O2daXngQCjoIEAAICCSYNCAADwAPYAOCQcGEoAADBgAAAQv//9SIwAHFO7a5G7j7OBALeK94EB8YIBpPCBAw==';
    v.appendChild(webm);
    v.appendChild(mp4);
    // Se o navegador pausar o vídeo (troca de aba, economia), retoma assim que
    // a página voltar a ficar visível.
    v.addEventListener('pause', function () {
      if (document.visibilityState === 'visible') tocarVideo();
    });
    document.body.appendChild(v);
    video = v;
  }

  function tocarVideo() {
    criarVideo();
    if (!video) return;
    var p = video.play();
    if (p && p.then) {
      p.then(function () { marcar('video'); })
       .catch(function () { marcar('falhou'); });
    } else {
      marcar('video');
    }
  }

  // ── Orquestração ───────────────────────────────────────────────────────────
  function ativar() {
    if (document.visibilityState !== 'visible') return;   // oculta: não adianta
    if (API && tentativasApi < 3) {
      pedirApi().then(function (ok) { if (!ok) tocarVideo(); });
    } else {
      tocarVideo();
    }
  }

  // ── 2. Vigia: confere de tempos em tempos e repõe o que caiu ───────────────
  function vigiar() {
    if (document.visibilityState !== 'visible') return;
    if (wakeLock && !wakeLock.released) return;           // segue firme
    if (video && !video.paused && !API) return;           // fallback rodando
    ativar();
  }

  function comecar() {
    ativar();
    if (!timer) timer = setInterval(vigiar, INTERVALO);
  }

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') ativar();
  });
  // Voltar pelo histórico (bfcache) não dispara DOMContentLoaded
  window.addEventListener('pageshow', ativar);
  // Alguns navegadores só liberam após um gesto: o primeiro toque reforça
  ['click', 'touchstart', 'keydown'].forEach(function (ev) {
    document.addEventListener(ev, function () {
      if (modo !== 'ativo' && modo !== 'video') ativar();
    }, { passive: true });
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', comecar);
  } else {
    comecar();
  }

  // Diagnóstico pelo console do navegador
  window.wakeLockInfo = function () {
    return {
      modo: modo,
      apiDisponivel: API,
      contextoSeguro: window.isSecureContext,
      bloqueioAtivo: !!(wakeLock && !wakeLock.released),
      videoTocando: !!(video && !video.paused),
      dica: API ? (window.isSecureContext ? ''
                   : 'A API exige HTTPS; em HTTP só o vídeo funciona.')
                : 'Navegador sem Screen Wake Lock — usando vídeo.'
    };
  };
})();
