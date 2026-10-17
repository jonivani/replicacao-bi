import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { PoModule, PoProgressStatus, PoTableColumn } from '@po-ui/ng-components';
import { Subscription, interval, timer } from 'rxjs';

import { ApiService } from '../../core/api.service';
import { poll } from '../../core/polling';
import { MetricCard } from '../../shared/metric-card/metric-card';
import { ResultadoSpeedtest, SpeedtestCard } from '../../shared/speedtest-card/speedtest-card';

interface RelatorioExecucao {
  filename: string;
  data: string;
  duracao: string;
  total_tabelas: number;
  total_inseridos: number;
  status: string;
  erros: string[];
  vazao: number;
  tabelas: Record<string, number>;
  tabelas_detalhe?: Record<string, { registros: number; tempo: number }>;
  error?: string;
}

interface TaxaSucesso {
  total: number;
  sucesso: number;
  percentual: number | null;
}

interface SaudeServidor {
  cpu_percent: number;
  memoria: { percent: number; usado_gb: number; total_gb: number };
  disco: { percent: number; usado_gb: number; total_gb: number; livre_gb: number };
}

interface ResumoValidacao {
  data_execucao?: string;
  status_geral?: string;
  total_tabelas?: number;
  tabelas_divergentes?: number;
  tabelas_corrigidas?: number;
  duplicados_removidos?: number;
  email_enviado?: boolean | null;
  email_erro?: string | null;
  error?: string;
}

interface ResumoAuditoria {
  quando: string | null;
  tabelas_auditadas: number;
  tabelas_duplicadas: number;
  duplicados_registros: number;
  tabelas_divergentes: number;
  registros_deletados_bi: number;
}

interface TarefaAgendada {
  installed: boolean;
  name: string;
  next_run: string;
  status: string;
  last_run: string;
  last_result: string;
}

interface StatusGeral {
  sistema: string;
  last_diff: RelatorioExecucao | null;
  last_completo: RelatorioExecucao | null;
  active_task: { running: boolean; mode: string | null; smart: boolean; tabela: string | null; duration: number };
  server_health: SaudeServidor | null;
  taxa_sucesso: { diff: TaxaSucesso; completo: TaxaSucesso };
  last_validacao: ResumoValidacao | null;
  ultima_auditoria: ResumoAuditoria | null;
  last_speedtest: ResultadoSpeedtest | null;
  schedules: TarefaAgendada[];
}

interface ActiveSpeedtest {
  running: boolean;
  started_at: number | null;
  fase: string | null;
  parcial_mbps: number | null;
  resultado: ResultadoSpeedtest | null;
  error: string | null;
}

interface LinhaVolumetria {
  tabela: string;
  registros: number;
}

interface LinhaLenta {
  tabela: string;
  tempo: string;
}

/** "YYYY-MM-DD HH:mm:ss" (formato dos logs de replicação) → epoch ms. */
function parseDataExecucao(str: string | undefined): number | null {
  if (!str) return null;
  const dt = new Date(str.replace(' ', 'T'));
  return isNaN(dt.getTime()) ? null : dt.getTime();
}

/** "dd/MM/yyyy HH:mm:ss" (formato devolvido por `_formatar_task_info`, ver servicos.py) → epoch ms. */
function parseBrDateTime(str: string | undefined): number | null {
  if (!str || str === 'N/A') return null;
  const [datePart, timePart] = str.split(' ');
  if (!datePart || !timePart) return null;
  const [d, m, y] = datePart.split('/').map(Number);
  const [hh, mm, ss] = timePart.split(':').map(Number);
  const dt = new Date(y, m - 1, d, hh, mm, ss || 0);
  return isNaN(dt.getTime()) ? null : dt.getTime();
}

/**
 * Painel Geral (tela inicial do v1) — segunda tela migrada na Fase 3b (roadmap v2), depois do
 * piloto de Auditoria de Dados.
 *
 * Cards "Última Validação de Integridade", "Última Auditoria Manual", "Frescor dos Dados",
 * "Próxima Execução Agendada" e "Tabelas Mais Lentas" — adicionados em 21/08/2026, depois da
 * revisão de paridade v1→v2 — não precisaram de nenhuma mudança no backend: `GET /api/status`
 * já devolvia `last_validacao`/`ultima_auditoria`/`schedules`/`tabelas_detalhe` desde a Fase 3a,
 * só não eram consumidos aqui ainda.
 */
@Component({
  selector: 'app-painel-geral',
  standalone: true,
  imports: [CommonModule, PoModule, MetricCard, SpeedtestCard],
  templateUrl: './painel-geral.html',
  styleUrl: './painel-geral.scss',
})
export class PainelGeral implements OnInit, OnDestroy {
  status: StatusGeral | null = null;
  volumetria: LinhaVolumetria[] = [];
  tabelasMaisLentas: LinhaLenta[] = [];

  speedtestAtivo: ActiveSpeedtest | null = null;
  private speedtestPollSub?: Subscription;

  frescorTexto = '-';
  frescorClasse: 'success' | 'warning' | 'failed' | '' = '';
  frescorSubtitulo = 'Sem execução DIFF registrada.';

  proximaExecucaoTexto = '-';
  proximaExecucaoSubtitulo = 'Nenhuma tarefa ativa agendada.';

  private pollingSub?: Subscription;
  private relogioSub?: Subscription;

  readonly colunasVolumetria: Array<PoTableColumn> = [
    { property: 'tabela', label: 'Tabela', type: 'string' },
    { property: 'registros', label: 'Registros', type: 'number' },
  ];

  readonly colunasLentas: Array<PoTableColumn> = [
    { property: 'tabela', label: 'Tabela', type: 'string' },
    { property: 'tempo', label: 'Tempo de Execução', type: 'string' },
  ];

  constructor(private readonly api: ApiService) {}

  ngOnInit(): void {
    // A cada 20s, mesmo intervalo do v1 — só pra manter os números vivos sem o usuário
    // precisar apertar "atualizar"; não tem uma condição de parada (painel fica sempre visível).
    this.pollingSub = poll(() => this.api.get<StatusGeral>('/api/status'), 20000).subscribe((status) => {
      this.aplicarStatus(status);
    });
    // Frescor dos Dados e Próxima Execução Agendada são relógios locais — recalculados a cada
    // 30s (mesmo padrão do v1, `tickDashClocks`) em cima do último `status` já recebido, sem
    // bater no servidor de novo: só o "há quanto tempo" / contagem regressiva precisa andar
    // entre um poll de 20s e outro.
    this.relogioSub = interval(30000).subscribe(() => this.recalcularRelogios());

    // Se a página for recarregada com um teste de velocidade em andamento (disparado antes do
    // reload, ou pela automação do DIFF que roda no mesmo host), retoma o acompanhamento em vez
    // de deixar o card parado sem saber que já tem um teste rolando.
    this.api.get<ActiveSpeedtest>('/api/speedtest/status').subscribe((estado) => {
      this.speedtestAtivo = estado;
      if (estado.running) this.iniciarPollingSpeedtest();
    });
  }

  ngOnDestroy(): void {
    this.pollingSub?.unsubscribe();
    this.relogioSub?.unsubscribe();
    this.speedtestPollSub?.unsubscribe();
  }

  dispararSpeedtest(): void {
    this.api.post('/api/speedtest/start').subscribe({
      next: () => this.iniciarPollingSpeedtest(),
      // 409 (já tem um teste rodando, ex: outra aba) — mesmo assim acompanha o progresso dele.
      error: () => this.iniciarPollingSpeedtest(),
    });
  }

  private iniciarPollingSpeedtest(): void {
    this.speedtestPollSub?.unsubscribe();
    this.speedtestPollSub = timer(0, 400).subscribe(() => {
      this.api.get<ActiveSpeedtest>('/api/speedtest/status').subscribe((estado) => {
        this.speedtestAtivo = estado;
        if (!estado.running) {
          this.speedtestPollSub?.unsubscribe();
          if (estado.resultado && this.status) {
            this.status.last_speedtest = estado.resultado;
          }
        }
      });
    });
  }

  private aplicarStatus(status: StatusGeral): void {
    this.status = status;

    const tabelas = status.last_diff?.tabelas ?? {};
    this.volumetria = Object.entries(tabelas)
      .map(([tabela, registros]) => ({ tabela, registros }))
      .sort((a, b) => b.registros - a.registros);

    this.tabelasMaisLentas = this.calcularTabelasMaisLentas(status.last_completo?.tabelas_detalhe);
    this.recalcularRelogios();
  }

  private calcularTabelasMaisLentas(detalhe: Record<string, { registros: number; tempo: number }> | undefined): LinhaLenta[] {
    if (!detalhe) return [];
    return Object.entries(detalhe)
      .sort((a, b) => b[1].tempo - a[1].tempo)
      .slice(0, 10)
      .map(([tabela, info]) => {
        const tempo = info.tempo || 0;
        const tempoTexto = tempo >= 60 ? `${Math.floor(tempo / 60)}m ${(tempo % 60).toFixed(0)}s` : `${tempo.toFixed(2)}s`;
        return { tabela, tempo: tempoTexto };
      });
  }

  private recalcularRelogios(): void {
    const ultimoDiff = this.status?.last_diff;
    const tsFrescor = parseDataExecucao(ultimoDiff?.data);
    if (!tsFrescor) {
      this.frescorTexto = '-';
      this.frescorClasse = '';
      this.frescorSubtitulo = 'Sem execução DIFF registrada.';
    } else {
      const diffMin = Math.max(0, Math.floor((Date.now() - tsFrescor) / 60000));
      this.frescorTexto = diffMin < 1 ? 'Agora mesmo' : diffMin < 60 ? `Há ${diffMin} min` : `Há ${Math.floor(diffMin / 60)}h ${diffMin % 60}min`;
      this.frescorClasse = diffMin <= 10 ? 'success' : diffMin <= 30 ? 'warning' : 'failed';
      this.frescorSubtitulo = `Última execução: ${new Date(tsFrescor).toLocaleString('pt-BR')} (${ultimoDiff?.status ?? '-'})`;
    }

    let proximaTs: number | null = null;
    let proximaTarefa: string | null = null;
    for (const tarefa of this.status?.schedules ?? []) {
      if (!tarefa.installed || tarefa.status === 'Desativado') continue;
      const ts = parseBrDateTime(tarefa.next_run);
      if (ts && (proximaTs === null || ts < proximaTs)) {
        proximaTs = ts;
        proximaTarefa = tarefa.name;
      }
    }
    if (!proximaTs) {
      this.proximaExecucaoTexto = '-';
      this.proximaExecucaoSubtitulo = 'Nenhuma tarefa ativa agendada.';
    } else {
      const diffMs = proximaTs - Date.now();
      if (diffMs <= 0) {
        this.proximaExecucaoTexto = 'A qualquer momento';
      } else {
        const totalMin = Math.floor(diffMs / 60000);
        const h = Math.floor(totalMin / 60);
        const m = totalMin % 60;
        this.proximaExecucaoTexto = h > 0 ? `em ${h}h ${m}min` : `em ${m}min`;
      }
      this.proximaExecucaoSubtitulo = `${proximaTarefa} · ${new Date(proximaTs).toLocaleString('pt-BR')}`;
    }
  }

  /** Verde até 70%, laranja até 90%, vermelho acima — mesma faixa usada no v1. Usado no texto/valor do card. */
  corPorUso(percent: number): 'success' | 'warning' | 'failed' {
    if (percent >= 90) return 'failed';
    if (percent >= 70) return 'warning';
    return 'success';
  }

  /** Mesma faixa, mas nos 3 status que o `po-progress` aceita (sem laranja/warning nativo —
   * a faixa intermediária fica 'default', neutra, em vez de destacada). */
  statusProgresso(percent: number): PoProgressStatus {
    if (percent >= 90) return PoProgressStatus.Error;
    if (percent >= 70) return PoProgressStatus.Default;
    return PoProgressStatus.Success;
  }

  /** Rótulo/classe de cor pro status_geral da última validação — mesmo mapeamento do v1. */
  statusValidacao(statusGeral: string | undefined): { rotulo: string; classe: 'success' | 'failed' | '' } {
    if (statusGeral === 'SUCESSO') return { rotulo: 'OK', classe: 'success' };
    if (statusGeral === 'FALHA') return { rotulo: 'DIVERGÊNCIAS', classe: 'failed' };
    if (statusGeral === 'ERRO_EXECUCAO') return { rotulo: 'ERRO NA EXECUÇÃO', classe: 'failed' };
    return { rotulo: statusGeral ?? '-', classe: '' };
  }
}
