import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { PoDialogService, PoModule, PoMultiselectOption, PoNotificationService, PoTableAction, PoTableColumn } from '@po-ui/ng-components';
import { Subscription, firstValueFrom } from 'rxjs';

import { ApiService } from '../../core/api.service';
import { poll } from '../../core/polling';
import { TabelasService } from '../../core/tabelas.service';

export interface ResultadoAuditoria {
  tabela: string;
  reg_producao: number | null;
  reg_bi: number | null;
  deletados_prod: number | null;
  deletados_bi: number | null;
  stamp_prod: string | null;
  stamp_bi: string | null;
  duplicados_bi: number | null;
  status_tabela: string;
}

export interface StatusAuditoria {
  running: boolean;
  total: number;
  processadas: number;
  resultados: ResultadoAuditoria[];
  error: string | null;
  finished_at: string | null;
}

interface Contagem {
  total: number;
  ok: number;
  divergente: number;
  duplicados: number;
  erro: number;
}

/**
 * Piloto da Fase 3b (roadmap v2): primeira aba migrada pro Angular + PO-UI, escolhida por ser
 * a mais rica em tabela/polling/interação do painel v1. Valida o padrão de `ApiService` +
 * `poll()` (core/) antes de replicar pras demais abas.
 *
 * Fechado em 21/08/2026 (depois da revisão de paridade da tarefa 7): filtro de resultados,
 * contadores de resumo, remoção de duplicados em lote, modal de detalhe de divergência e
 * "Auditar Selecionadas" (`po-multiselect` + `TabelasService.listar()`, mesmo padrão da Central
 * de Carga — mas com seleção local a esta tela, não compartilhada entre as duas, ver
 * `core/tabelas.service.ts`).
 */
@Component({
  selector: 'app-auditoria',
  standalone: true,
  imports: [CommonModule, FormsModule, PoModule],
  templateUrl: './auditoria.html',
  styleUrl: './auditoria.scss',
})
export class Auditoria implements OnInit, OnDestroy {
  rodando = false;
  progresso = 0;
  total = 0;
  processadas = 0;
  resultados: ResultadoAuditoria[] = [];
  resultadosFiltrados: ResultadoAuditoria[] = [];
  erro: string | null = null;

  filtro = '';
  contagem: Contagem = { total: 0, ok: 0, divergente: 0, duplicados: 0, erro: 0 };

  tabelasDisponiveis: PoMultiselectOption[] = [];
  tabelasSelecionadas: string[] = [];

  removendoDuplicados = false;
  removendoAtual = 0;
  removendoTotal = 0;

  private pollingSub?: Subscription;

  readonly colunas: Array<PoTableColumn> = [
    { property: 'tabela', label: 'Tabela', type: 'string' },
    { property: 'reg_producao', label: 'Reg. Prod', type: 'number' },
    { property: 'reg_bi', label: 'Reg. BI', type: 'number' },
    { property: 'deletados_prod', label: 'Del. Prod', type: 'number' },
    { property: 'deletados_bi', label: 'Del. BI', type: 'number' },
    { property: 'stamp_prod', label: 'U.A. Prod', type: 'string' },
    { property: 'stamp_bi', label: 'U.A. BI', type: 'string' },
    { property: 'duplicados_bi', label: 'Duplic. BI', type: 'number' },
    {
      property: 'status_tabela',
      label: 'Status',
      type: 'label',
      labels: [
        { value: 'OK', color: 'color-11', label: 'Sincronizada' },
        { value: 'DIVERGENTE', color: 'color-07', label: 'Divergente' },
        { value: 'DUPLICADO', color: 'color-08', label: 'Duplicados' },
        { value: 'INEXISTENTE_ORIGEM', color: 'color-03', label: 'Inexistente no ERP' },
        { value: 'INEXISTENTE_DESTINO', color: 'color-03', label: 'Inexistente no BI' },
        { value: 'INEXISTENTE_AMBOS', color: 'color-03', label: 'Sem tabela' },
      ],
    },
  ];

  readonly acoes: Array<PoTableAction> = [
    {
      label: 'Ver divergência',
      action: (linha: ResultadoAuditoria) => this.verDivergencia(linha),
      visible: (linha: ResultadoAuditoria) => linha.status_tabela === 'DIVERGENTE',
    },
    {
      label: 'Replicar',
      action: (linha: ResultadoAuditoria) => this.replicarTabela(linha.tabela),
      visible: (linha: ResultadoAuditoria) =>
        linha.status_tabela === 'DIVERGENTE' || linha.status_tabela === 'INEXISTENTE_DESTINO',
    },
    {
      label: 'Remover duplicados',
      type: 'danger',
      action: (linha: ResultadoAuditoria) => this.removerDuplicados(linha.tabela),
      visible: (linha: ResultadoAuditoria) => !!linha.duplicados_bi && linha.duplicados_bi > 0,
    },
  ];

  constructor(
    private readonly api: ApiService,
    private readonly dialog: PoDialogService,
    private readonly notification: PoNotificationService,
    private readonly tabelasService: TabelasService
  ) {}

  ngOnInit(): void {
    this.atualizarStatus();
    this.tabelasService.listar().subscribe((opcoes) => (this.tabelasDisponiveis = opcoes));
  }

  ngOnDestroy(): void {
    this.pollingSub?.unsubscribe();
  }

  auditarTodas(): void {
    this.api.post('/api/audit/start', {}).subscribe({
      next: () => this.iniciarPolling(),
      error: (erro: Error) => (this.erro = erro.message),
    });
  }

  auditarSelecionadas(): void {
    if (this.tabelasSelecionadas.length === 0) {
      this.dialog.alert({ title: 'Aviso', message: 'Selecione pelo menos uma tabela para auditar.' });
      return;
    }
    this.api.post('/api/audit/start', { tabelas: this.tabelasSelecionadas.join(',') }).subscribe({
      next: () => this.iniciarPolling(),
      error: (erro: Error) => (this.erro = erro.message),
    });
  }

  cancelar(): void {
    this.api.post<{ message: string; cancelled: boolean }>('/api/audit/cancel').subscribe(() => {
      this.pollingSub?.unsubscribe();
      this.atualizarStatus();
    });
  }

  aplicarFiltro(): void {
    const termo = this.filtro.trim().toUpperCase();
    this.resultadosFiltrados = termo ? this.resultados.filter((r) => r.tabela.toUpperCase().includes(termo)) : this.resultados;
  }

  /** Explica a divergência a partir dos próprios dados da linha (sem buscar nada novo) — mesmo
   * cálculo do `showDivergenceModal` do v1, agora como uma ação de linha em vez de clique no
   * badge de status (mais consistente com o padrão de `p-actions` já usado no resto do app). */
  verDivergencia(linha: ResultadoAuditoria): void {
    const itens: string[] = [];

    if (linha.reg_producao !== linha.reg_bi && linha.reg_producao !== null && linha.reg_bi !== null) {
      const diff = linha.reg_producao - linha.reg_bi;
      const acao =
        diff > 0
          ? `faltam <strong>${diff.toLocaleString('pt-BR')}</strong> registros no BI`
          : `há <strong>${Math.abs(diff).toLocaleString('pt-BR')}</strong> registros a mais no BI`;
      itens.push(
        `<li><strong>Contagem:</strong> Origem tem ${linha.reg_producao.toLocaleString('pt-BR')} e BI tem ${linha.reg_bi.toLocaleString('pt-BR')} registros (${acao}).</li>`
      );
    }

    if (linha.deletados_prod !== linha.deletados_bi && linha.deletados_prod !== null && linha.deletados_bi !== null) {
      const diffDel = linha.deletados_prod - linha.deletados_bi;
      const acaoDel =
        diffDel > 0
          ? `faltam excluir <strong>${diffDel.toLocaleString('pt-BR')}</strong> no BI`
          : `foram excluídos <strong>${Math.abs(diffDel).toLocaleString('pt-BR')}</strong> a mais no BI`;
      itens.push(
        `<li><strong>Deletados (D_E_L_E_T_):</strong> Origem tem ${linha.deletados_prod.toLocaleString('pt-BR')} e BI tem ${linha.deletados_bi.toLocaleString('pt-BR')} registros marcados como deletados (${acaoDel}).</li>`
      );
    }

    if (linha.stamp_prod !== linha.stamp_bi && linha.stamp_prod && linha.stamp_bi) {
      itens.push(`<li><strong>Última Atualização:</strong> Prod [${linha.stamp_prod}] vs BI [${linha.stamp_bi}].</li>`);
    }

    const mensagem =
      `<p>A tabela <strong>${linha.tabela}</strong> apresenta divergência de dados entre a Origem (Protheus) e o Destino (BI).</p>` +
      (itens.length ? `<ul>${itens.join('')}</ul>` : '') +
      `<p><strong>Ação recomendada:</strong> clique em "Replicar" para forçar a sincronização imediata desta tabela e corrigir a divergência.</p>`;

    this.dialog.alert({ title: `Divergência: ${linha.tabela}`, message: mensagem });
  }

  removerTodosDuplicados(): void {
    const alvos = this.resultados.filter((r) => (r.duplicados_bi ?? 0) > 0);
    if (alvos.length === 0) {
      this.dialog.alert({ title: 'Nenhum Duplicado', message: 'Não há tabelas com registros duplicados na última auditoria.' });
      return;
    }

    const totalRegistros = alvos.reduce((soma, r) => soma + (r.duplicados_bi ?? 0), 0);
    this.dialog.confirm({
      title: 'Remover Duplicados de Todas as Tabelas',
      message:
        `Foram encontradas ${alvos.length} tabela(s) com um total de ${totalRegistros.toLocaleString('pt-BR')} registro(s) ` +
        `duplicado(s) (mesmo R_E_C_N_O_) no BI (Destino). Esta ação é IRREVERSÍVEL e roda tabela por tabela: os registros ` +
        `excedentes serão excluídos permanentemente, mantendo apenas 1 ocorrência de cada. Um log será gravado para cada ` +
        `tabela processada. Deseja continuar?`,
      confirm: () => this.executarRemocaoEmLote(alvos.map((r) => r.tabela)),
    });
  }

  private async executarRemocaoEmLote(tabelas: string[]): Promise<void> {
    this.removendoDuplicados = true;
    this.removendoTotal = tabelas.length;
    this.removendoAtual = 0;

    let sucesso = 0;
    let totalRemovidos = 0;
    const falhas: string[] = [];

    for (const tabela of tabelas) {
      this.removendoAtual++;
      try {
        const resultado = await firstValueFrom(
          this.api.post<{ removidos: number }>('/api/audit/remove-duplicates', { tabela })
        );
        sucesso++;
        totalRemovidos += resultado.removidos;
      } catch {
        falhas.push(tabela);
      }
    }

    this.removendoDuplicados = false;
    const resumo = `${sucesso} tabela(s) processada(s), ${totalRemovidos.toLocaleString('pt-BR')} registro(s) removido(s).`;
    if (falhas.length) {
      this.notification.warning(`${resumo} Falhas: ${falhas.join(', ')}.`);
    } else {
      this.notification.success(resumo);
    }
    this.atualizarStatus();
  }

  private atualizarStatus(): void {
    this.api.get<StatusAuditoria>('/api/audit/status').subscribe((status) => this.aplicarStatus(status));
  }

  private iniciarPolling(): void {
    this.pollingSub?.unsubscribe();
    // 800ms, mesmo intervalo do v1 — o poll() (core/polling.ts) já garante que nunca empilha
    // uma chamada em cima de outra ainda pendente, e para sozinho quando running vira false.
    this.pollingSub = poll(
      () => this.api.get<StatusAuditoria>('/api/audit/status'),
      800,
      (status) => status.running
    ).subscribe((status) => this.aplicarStatus(status));
  }

  private aplicarStatus(status: StatusAuditoria): void {
    this.rodando = status.running;
    this.total = status.total;
    this.processadas = status.processadas;
    this.progresso = status.total > 0 ? Math.round((status.processadas / status.total) * 100) : 0;
    this.resultados = status.resultados;
    this.erro = status.error;
    this.aplicarFiltro();
    this.recalcularContagem();
  }

  private recalcularContagem(): void {
    this.contagem = {
      total: this.resultados.length,
      ok: this.resultados.filter((r) => r.status_tabela === 'OK').length,
      divergente: this.resultados.filter((r) => r.status_tabela === 'DIVERGENTE').length,
      duplicados: this.resultados.filter((r) => (r.duplicados_bi ?? 0) > 0).length,
      erro: this.resultados.filter((r) => r.status_tabela.startsWith('INEXISTENTE')).length,
    };
  }

  private replicarTabela(tabela: string): void {
    this.api.post('/api/run', { modo: 'diff', tabela }).subscribe();
  }

  private removerDuplicados(tabela: string): void {
    this.api
      .post<{ removidos: number; log_file: string; message: string }>('/api/audit/remove-duplicates', { tabela })
      .subscribe(() => this.atualizarStatus());
  }
}
