import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { PoDialogService, PoModule, PoNotificationService, PoTableAction, PoTableColumn } from '@po-ui/ng-components';
import { Subscription } from 'rxjs';

import { ApiService } from '../../core/api.service';
import { poll } from '../../core/polling';

interface TarefaAgendada {
  installed: boolean;
  name: string;
  next_run: string;
  status: string;
  last_run: string;
  last_result: string;
}

interface Progresso {
  atual: number;
  total: number;
}

interface LogAoVivo {
  name: string | null;
  content: string;
  mtime: string | null;
  progress: Progresso | null;
}

/** Mapeia cada tarefa do Agendador do Windows para a pasta de logs correspondente (logs/<modo>/),
 * usada pelo painel de log em tempo real quando a tarefa está "Executando" — mesmo mapeamento do v1. */
const MODO_POR_TAREFA: Record<string, string> = {
  'Protheus BI - Replicacao DIFF': 'diff',
  'Protheus BI - Replicacao COMPLETO': 'completo',
  'Protheus BI - Validacao Integridade': 'validacao',
};

const DESCRICAO_POR_TAREFA: Record<string, string> = {
  'Protheus BI - Replicacao DIFF': 'Replicador incremental periódico (Novos/Deletados a cada 5m).',
  'Protheus BI - Replicacao COMPLETO': 'Carga completa diária estruturada (Recarga inteligente de tabelas).',
  'Protheus BI - Validacao Integridade': 'Auditoria de integridade diária (Contagem/Schema com alerta de e-mail).',
};

/**
 * Agendador Windows — quarta tela migrada na Fase 3b (roadmap v2), depois de Auditoria, Painel
 * Geral e Central de Carga.
 *
 * O `status` retornado por `/api/schedules` já vem pronto em português ("Pronto"/"Executando"/
 * "Desativado"/"Não Instalada") — `_formatar_task_info` em `scripts/servicos.py` resolve isso no
 * backend, então (diferente do v1, que fazia esse mapeamento no JS do cliente) a tela aqui só
 * precisa casar contra esses literais diretamente.
 *
 * Confirmação de ação (ativar/pausar/rodar/parar) usa `PoDialogService.confirm()` — mesmo padrão
 * do `showConfirmModal` do v1, agora componentizado. Feedback de sucesso/erro usa
 * `PoNotificationService` (toaster), primeiro uso desse serviço no v2.
 */
@Component({
  selector: 'app-agendador',
  standalone: true,
  imports: [CommonModule, PoModule],
  templateUrl: './agendador.html',
  styleUrl: './agendador.scss',
})
export class Agendador implements OnInit, OnDestroy {
  tarefas: TarefaAgendada[] = [];
  // Computado uma vez em `aplicarTarefas()`, não um getter — um getter chamado direto no
  // template (`[p-items]="linhas"`) é reavaliado a cada ciclo de detecção de mudanças e devolve
  // um array com objetos NOVOS toda vez, mesmo quando os dados não mudaram. O `po-table` reage a
  // essa referência nova recriando a linha, o que fecha o menu de ações (\"...\") assim que ele
  // abre — mesmo bug já achado e corrigido em Histórico de Logs (ver ROADMAP_V2.md), só que lá
  // travava a página inteira e aqui só quebra o dropdown, então passou despercebido até o
  // usuário reportar visualmente.
  linhas: Array<TarefaAgendada & { descricao: string }> = [];
  carregando = true;
  erro: string | null = null;

  logAoVivo: LogAoVivo | null = null;
  tarefaMonitorada: string | null = null;
  progressoLog: Progresso | null = null;

  private pollingSub?: Subscription;
  private logPollingSub?: Subscription;

  readonly colunas: Array<PoTableColumn> = [
    { property: 'name', label: 'Nome da Tarefa', type: 'string' },
    { property: 'descricao', label: 'Descrição da Carga', type: 'string' },
    {
      property: 'status',
      label: 'Status',
      type: 'label',
      labels: [
        { value: 'Pronto', color: 'color-11', label: 'Pronto' },
        { value: 'Executando', color: 'color-08', label: 'Executando' },
        { value: 'Desativado', color: 'color-07', label: 'Desativado' },
        { value: 'Não Instalada', color: 'color-03', label: 'Não Instalada' },
      ],
    },
    { property: 'last_run', label: 'Última Execução', type: 'string' },
    { property: 'next_run', label: 'Próxima Execução', type: 'string' },
    { property: 'last_result', label: 'Resultado', type: 'string' },
  ];

  readonly acoes: Array<PoTableAction> = [
    {
      label: 'Ativar',
      action: (linha: TarefaAgendada) => this.confirmarAcao(linha, 'enable'),
      visible: (linha: TarefaAgendada) => linha.installed && linha.status === 'Desativado',
    },
    {
      label: 'Pausar',
      action: (linha: TarefaAgendada) => this.confirmarAcao(linha, 'disable'),
      visible: (linha: TarefaAgendada) => linha.installed && linha.status !== 'Desativado',
    },
    {
      label: 'Rodar Agora',
      action: (linha: TarefaAgendada) => this.confirmarAcao(linha, 'run'),
      visible: (linha: TarefaAgendada) => linha.installed && linha.status !== 'Executando',
    },
    {
      label: 'Parar',
      type: 'danger',
      action: (linha: TarefaAgendada) => this.confirmarAcao(linha, 'stop'),
      visible: (linha: TarefaAgendada) => linha.installed && linha.status === 'Executando',
    },
    {
      // Garante que o menu "..." nunca fica vazio quando a tarefa não está instalada — sem
      // isso, nenhuma das 4 ações acima é `visible`, o menu abre sem nenhuma opção, e parece
      // quebrado (foi reportado assim pelo usuário, testando numa máquina sem as 3 tarefas
      // registradas no Agendador do Windows).
      label: 'Tarefa não instalada',
      action: (linha: TarefaAgendada) =>
        this.dialog.alert({
          title: 'Tarefa não instalada',
          message: `A tarefa "${linha.name}" ainda não foi registrada no Agendador de Tarefas do Windows deste servidor. Use o script de instalação para registrá-la antes de poder ativá-la, pausá-la ou executá-la por aqui.`,
        }),
      visible: (linha: TarefaAgendada) => !linha.installed,
    },
  ];

  constructor(
    private readonly api: ApiService,
    private readonly dialog: PoDialogService,
    private readonly notification: PoNotificationService
  ) {}

  ngOnInit(): void {
    this.iniciarPolling();
  }

  ngOnDestroy(): void {
    this.pollingSub?.unsubscribe();
    this.logPollingSub?.unsubscribe();
  }

  private confirmarAcao(tarefa: TarefaAgendada, acao: 'enable' | 'disable' | 'run' | 'stop'): void {
    const verbos: Record<string, string> = {
      enable: 'ativar',
      disable: 'pausar (desativar)',
      run: 'executar imediatamente',
      stop: 'forçar a interrupção de',
    };
    this.dialog.confirm({
      title: 'Ação no Agendador',
      message: `Deseja ${verbos[acao]} a tarefa agendada "${tarefa.name}"?`,
      confirm: () => this.executarAcao(tarefa.name, acao),
    });
  }

  private executarAcao(taskName: string, acao: string): void {
    this.api.post<{ message: string }>('/api/schedules/action', { task: taskName, action: acao }).subscribe({
      next: (resultado) => {
        this.notification.success(resultado.message);
        this.iniciarPolling();
      },
      error: (e: Error) => this.notification.error(e.message),
    });
  }

  private iniciarPolling(): void {
    this.pollingSub?.unsubscribe();
    // 5s enquanto alguma tarefa mapeada estiver "Executando", mesmo padrão do v1
    // (scheduleNextStatusPoll) — para sozinho assim que não sobra nenhuma rodando.
    this.pollingSub = poll(
      () => this.api.get<TarefaAgendada[]>('/api/schedules'),
      5000,
      (tarefas) => this.algumaTarefaMonitoravelRodando(tarefas)
    ).subscribe({
      next: (tarefas) => this.aplicarTarefas(tarefas),
      error: (e: Error) => {
        this.erro = e.message;
        this.carregando = false;
      },
    });
  }

  private algumaTarefaMonitoravelRodando(tarefas: TarefaAgendada[]): boolean {
    return tarefas.some((t) => t.status === 'Executando' && !!MODO_POR_TAREFA[t.name]);
  }

  private aplicarTarefas(tarefas: TarefaAgendada[]): void {
    this.tarefas = tarefas;
    this.linhas = tarefas.map((t) => ({
      ...t,
      descricao: DESCRICAO_POR_TAREFA[t.name] ?? 'Tarefa agendada do pipeline de replicação.',
    }));
    this.carregando = false;
    const rodando = tarefas.find((t) => t.status === 'Executando' && !!MODO_POR_TAREFA[t.name]);
    this.atualizarLogAoVivo(rodando?.name ?? null);
  }

  private atualizarLogAoVivo(taskName: string | null): void {
    if (!taskName) {
      this.logPollingSub?.unsubscribe();
      this.tarefaMonitorada = null;
      this.logAoVivo = null;
      this.progressoLog = null;
      return;
    }
    if (this.tarefaMonitorada === taskName) return; // já monitorando esta tarefa

    this.tarefaMonitorada = taskName;
    this.logAoVivo = null;
    this.progressoLog = null;

    const modo = MODO_POR_TAREFA[taskName];
    this.logPollingSub?.unsubscribe();
    // 3s, mesmo intervalo do v1 (fetchSchedLiveLog) — para sozinho quando a tarefa monitorada
    // deixa de existir (atualizarLogAoVivo(null) cancela via unsubscribe acima).
    this.logPollingSub = poll(() => this.api.get<LogAoVivo>('/api/schedules/live-log', { modo }), 3000).subscribe(
      (log) => {
        this.logAoVivo = log;
        this.progressoLog = log.progress && log.progress.total > 0 ? log.progress : null;
      }
    );
  }
}
