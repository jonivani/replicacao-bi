import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { PoModule, PoMultiselectOption, PoNotificationService, PoSelectOption } from '@po-ui/ng-components';
import { Subscription } from 'rxjs';

import { ApiService } from '../../core/api.service';
import { poll } from '../../core/polling';
import { TabelasService } from '../../core/tabelas.service';

interface RunAtivo {
  running: boolean;
  mode: string | null;
  smart: boolean;
  tabela: string | null;
  duration: number;
  output: string;
  exit_code: number | null;
  error: string | null;
  progress: { atual: number; total: number } | null;
}

/**
 * Central de Carga — terceira tela migrada na Fase 3b (roadmap v2), depois de Auditoria e
 * Painel Geral. Os 4 disparos principais (DIFF, SMART COMPLETO, COMPLETO CEGO, VALIDADOR) e o
 * console ao vivo são o núcleo funcional da tela.
 *
 * Card "Replicar Tabelas Específicas" fechado em 21/08/2026 (depois da revisão de paridade da
 * tarefa 7): `po-multiselect` (componente oficial do PO-UI, não um dropdown feito à mão como o
 * v1) alimentado por `TabelasService.listar()` (união de `tabelas_diff`/`tabelas_completo`, ver
 * `core/tabelas.service.ts`) + seletor de modo + disparo — `POST /api/run` já aceita `tabela`
 * como uma lista separada por vírgula (`replicacao.py`, `tabela_unica.split(',')`), então não
 * precisou de nenhuma mudança no backend.
 */
@Component({
  selector: 'app-central-carga',
  standalone: true,
  imports: [CommonModule, FormsModule, PoModule],
  templateUrl: './central-carga.html',
  styleUrl: './central-carga.scss',
})
export class CentralCarga implements OnInit, OnDestroy {
  runAtivo: RunAtivo | null = null;
  erro: string | null = null;
  carregando = true;

  tabelasDisponiveis: PoMultiselectOption[] = [];
  tabelasSelecionadas: string[] = [];
  modoSelecionadas = 'diff';

  readonly opcoesModo: Array<PoSelectOption> = [
    { label: 'DIFF (Incremental)', value: 'diff' },
    { label: 'COMPLETO', value: 'completo' },
  ];

  private pollingSub?: Subscription;

  constructor(
    private readonly api: ApiService,
    private readonly tabelasService: TabelasService,
    private readonly notification: PoNotificationService
  ) {
    this.atualizarStatus();
  }

  ngOnInit(): void {
    this.tabelasService.listar().subscribe((opcoes) => (this.tabelasDisponiveis = opcoes));
  }

  ngOnDestroy(): void {
    this.pollingSub?.unsubscribe();
  }

  get rodando(): boolean {
    return this.runAtivo?.running ?? false;
  }

  get progressoPercent(): number {
    const p = this.runAtivo?.progress;
    return p && p.total > 0 ? Math.round((p.atual / p.total) * 100) : 0;
  }

  disparar(modo: 'diff' | 'completo' | 'validador', smart = false): void {
    this.erro = null;
    this.api.post('/api/run', { modo, smart }).subscribe({
      next: () => this.iniciarPolling(),
      error: (e: Error) => (this.erro = e.message),
    });
  }

  dispararSelecionadas(): void {
    if (this.tabelasSelecionadas.length === 0) {
      this.notification.warning('Selecione ao menos uma tabela antes de disparar.');
      return;
    }
    this.erro = null;
    this.api.post('/api/run', { modo: this.modoSelecionadas, tabela: this.tabelasSelecionadas.join(',') }).subscribe({
      next: () => this.iniciarPolling(),
      error: (e: Error) => (this.erro = e.message),
    });
  }

  private atualizarStatus(): void {
    this.api.get<RunAtivo>('/api/run/active').subscribe({
      next: (run) => {
        this.runAtivo = run;
        this.carregando = false;
        if (run.running) this.iniciarPolling();
      },
      error: (e: Error) => {
        this.erro = e.message;
        this.carregando = false;
      },
    });
  }

  private iniciarPolling(): void {
    this.pollingSub?.unsubscribe();
    // 1s, mesmo intervalo do v1 (era o poller mais sensível ao problema de requisição
    // empilhada que motivou a Fase 2 do roadmap v2 — aqui isso é estrutural via poll()).
    this.pollingSub = poll(
      () => this.api.get<RunAtivo>('/api/run/active'),
      1000,
      (run) => run.running
    ).subscribe((run) => (this.runAtivo = run));
  }
}
