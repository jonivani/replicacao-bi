import { Injectable } from '@angular/core';
import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { catchError, map } from 'rxjs/operators';

import { environment } from '../../environments/environment';

/** Forma padrão de sucesso de um POST de ação (Fase 2 do roadmap v2 — docs/API_CONTRACT.md). */
export interface RespostaSucesso<T = Record<string, unknown>> {
  success: boolean;
  data: T;
}

/**
 * Camada HTTP única do front — conhece o envelope padronizado da API (Fase 2) pra que
 * nenhum componente precise saber que erro é `{"error": {"message": "..."}}` e sucesso de
 * POST é `{"success": true, "data": {...}}`. GETs de leitura devolvem os dados direto na
 * raiz, sem envelope (mesma regra do backend).
 */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly baseUrl = environment.apiUrl;

  constructor(private readonly http: HttpClient) {}

  /** GET de leitura — resposta já é o dado, sem desembrulhar nada. */
  get<T>(path: string, params?: Record<string, string>): Observable<T> {
    return this.http
      .get<T>(`${this.baseUrl}${path}`, { params: this.paramsDe(params) })
      .pipe(catchError(this.tratarErro));
  }

  /** POST de ação — desembrulha `{success, data}` e devolve só `data`. */
  post<T>(path: string, body: unknown = {}): Observable<T> {
    return this.http
      .post<RespostaSucesso<T>>(`${this.baseUrl}${path}`, body)
      .pipe(
        map((resposta) => resposta.data),
        catchError(this.tratarErro)
      );
  }

  /** Variante de `post()` que preserva o envelope inteiro (`{success, data}`) em vez de
   * desembrulhar só `data` — necessária quando `success` pode legitimamente vir `false` com
   * HTTP 200 (ex: `POST /api/config/test`, onde `success` reflete o resultado do TESTE de
   * conexão, não se a chamada em si funcionou — ver docs/API_CONTRACT.md). */
  postComEnvelope<T>(path: string, body: unknown = {}): Observable<RespostaSucesso<T>> {
    return this.http
      .post<RespostaSucesso<T>>(`${this.baseUrl}${path}`, body)
      .pipe(catchError(this.tratarErro));
  }

  private paramsDe(params?: Record<string, string>): HttpParams | undefined {
    if (!params) return undefined;
    let httpParams = new HttpParams();
    for (const [chave, valor] of Object.entries(params)) {
      httpParams = httpParams.set(chave, valor);
    }
    return httpParams;
  }

  /** Normaliza `{"error": {"message": "..."}}` (ou uma falha de rede) num Error comum, pra
   * quem consome `ApiService` nunca precisar conhecer a forma do envelope de erro. */
  private tratarErro(erro: HttpErrorResponse) {
    const mensagem =
      (erro.error && erro.error.error && erro.error.error.message) ||
      erro.message ||
      'Erro desconhecido';
    return throwError(() => new Error(mensagem));
  }
}
