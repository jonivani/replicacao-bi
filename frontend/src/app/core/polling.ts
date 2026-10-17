import { Observable, timer } from 'rxjs';
import { exhaustMap, takeWhile } from 'rxjs/operators';

/**
 * Polling declarativo com `exhaustMap`: se uma chamada de `fonte()` ainda está em andamento
 * quando o próximo tick do intervalo chega, esse tick é ignorado — nunca dispara uma
 * requisição por cima de outra ainda pendente.
 *
 * É a versão RxJS do que cada poller do painel v1 precisou implementar manualmente com uma
 * flag booleana (`statusFetchInFlight`, `auditPollFetchInFlight`, `runPollFetchInFlight`,
 * `schedLiveLogFetchInFlight` — ver templates/index.html do v1). Foi exatamente a falta desse
 * controle que causou o incidente de requisições empilhadas descrito no ROADMAP_V2.md — aqui
 * ele é estrutural, não algo que cada tela precisa lembrar de implementar de novo.
 *
 * @param fonte função que dispara a chamada (normalmente `() => api.get(...)`)
 * @param intervaloMs intervalo entre o fim de uma chamada e o início da próxima tentativa
 * @param deveContinuar reavaliado a cada emissão; quando retorna `false`, o polling para
 *   sozinho (ex: parar de consultar status assim que `running` virar `false`) — se omitido,
 *   o polling continua indefinidamente até quem assinou o Observable cancelar (`unsubscribe`)
 */
export function poll<T>(
  fonte: () => Observable<T>,
  intervaloMs: number,
  deveContinuar?: (valor: T) => boolean
): Observable<T> {
  const fluxo = timer(0, intervaloMs).pipe(exhaustMap(fonte));
  return deveContinuar ? fluxo.pipe(takeWhile(deveContinuar, true)) : fluxo;
}
