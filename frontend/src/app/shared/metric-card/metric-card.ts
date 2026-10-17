import { CommonModule } from '@angular/common';
import { Component, Input } from '@angular/core';

/**
 * Card de KPI reutilizável (título + valor grande + linha de apoio opcional). O v1 tinha isso
 * como `.metric-card` em CSS solto, repetido em cada card do Painel Geral — aqui vira um
 * componente único, usado pelo Painel Geral e por qualquer outra tela que precisar do mesmo
 * padrão (ex: futuras telas de Configuração de Tabelas/Bancos, Fase 4/5 do roadmap v2).
 */
@Component({
  selector: 'app-metric-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './metric-card.html',
  styleUrl: './metric-card.scss',
})
export class MetricCard {
  @Input() titulo = '';
  @Input() valor: string | number | null = '-';
  @Input() subtitulo = '';
  /** 'success' | 'failed' | 'warning' | 'info' | undefined — mesma paleta semântica do v1. */
  @Input() variante?: 'success' | 'failed' | 'warning' | 'info';
}
