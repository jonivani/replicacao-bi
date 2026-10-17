import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, OnChanges, OnDestroy, Output, SimpleChanges } from '@angular/core';
import { PoModule } from '@po-ui/ng-components';

export interface ResultadoSpeedtest {
  timestamp: string;
  origem: string;
  ping_ms: number | null;
  download_mbps: number | null;
  upload_mbps: number | null;
  error: string | null;
}

/**
 * Card "Velocidade da Internet do Servidor" — mesma ideia do fast.com: um número grande
 * centralizado que sobe animado enquanto mede, pra diagnosticar se a internet do servidor está
 * lenta (o que pode agravar problemas na transferência de dados da replicação).
 *
 * O componente só exibe e anima; quem decide QUANDO medir (disparar POST /api/speedtest/start e
 * fazer polling de /api/speedtest/status) é o `PainelGeral` — este card só recebe o estado via
 * @Input e emite `testar` quando o botão é clicado.
 */
@Component({
  selector: 'app-speedtest-card',
  standalone: true,
  imports: [CommonModule, PoModule],
  templateUrl: './speedtest-card.html',
  styleUrl: './speedtest-card.scss',
})
export class SpeedtestCard implements OnChanges, OnDestroy {
  @Input() rodando = false;
  @Input() valorAoVivo: number | null = null;
  @Input() fase: string | null = null;
  @Input() ultimoResultado: ResultadoSpeedtest | null = null;
  @Output() testar = new EventEmitter<void>();

  /** Valor exibido no card, já em animação de aproximação — ver animarPara(). */
  displayValue = 0;

  private rafId?: number;

  get valorAlvo(): number {
    if (this.rodando) return this.valorAoVivo ?? 0;
    return this.ultimoResultado?.download_mbps ?? 0;
  }

  get origemTexto(): string {
    const origem = this.ultimoResultado?.origem;
    if (origem === 'automatico_diff') return 'medição automática (fim do DIFF)';
    if (origem === 'manual') return 'medição manual';
    return '';
  }

  get faseTexto(): string {
    switch (this.fase) {
      case 'ping':
        return 'Medindo ping...';
      case 'download':
        return 'Medindo download...';
      case 'upload':
        return 'Medindo upload...';
      default:
        return 'Medindo...';
    }
  }

  ngOnChanges(changes: SimpleChanges): void {
    if ('valorAoVivo' in changes || 'ultimoResultado' in changes || 'rodando' in changes) {
      this.animarPara(this.valorAlvo);
    }
  }

  ngOnDestroy(): void {
    if (this.rafId) cancelAnimationFrame(this.rafId);
  }

  /** Anima displayValue do valor atual até `alvo` em ~350ms — dá o efeito de "número subindo"
   * do fast.com em vez do valor só saltar a cada tick do polling (a cada 400ms). */
  private animarPara(alvo: number): void {
    if (this.rafId) cancelAnimationFrame(this.rafId);
    const inicio = this.displayValue;
    const t0 = performance.now();
    const duracao = 350;
    const passo = (agora: number): void => {
      const t = Math.min(1, (agora - t0) / duracao);
      this.displayValue = inicio + (alvo - inicio) * t;
      if (t < 1) {
        this.rafId = requestAnimationFrame(passo);
      }
    };
    this.rafId = requestAnimationFrame(passo);
  }
}
